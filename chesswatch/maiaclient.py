"""Which move a player of a given rating is likely to play, asked of maia/engine.py.

maia/ is a separate program under its own licence, AGPL-3.0, and this module is
the whole of the MIT side's contact with it: it starts engine.py as a child
process and exchanges one line of JSON per question over its stdin and stdout.
Nothing from maia/ is imported here or anywhere else in chesswatch, which is
what keeps the two programs apart. Stockfish is kept at the same distance for
the same reason.

    m = Maia()
    m.ask(fen, 800)        # [("e2e4", 0.31), ("d2d4", 0.22), ...] or None
    m.close()

Everything here is allowed to fail. No onnxruntime on the machine, a process
that has died, an answer that does not come: each of them is None, and the
coach goes on choosing its red arrow the way it did before there was a model.
After any of those alive() is False, which is how the coach tells a Maia that
has gone from one that only got a question wrong.
"""

import itertools
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

ENGINE = Path(__file__).resolve().parent / "maia" / "engine.py"

# The same flag coach.py starts Stockfish with, for the same reason: a console
# program started from a windowless app gets a console window of its own on
# Windows otherwise. Written out again rather than imported, because coach.py
# imports this module.
POPEN_FLAGS = {}
if sys.platform == "win32":
    POPEN_FLAGS["creationflags"] = subprocess.CREATE_NO_WINDOW


class Maia:
    """One engine.py process, asked one question at a time.

    The process is started by start(), or by the first ask() if nobody called
    start(). Starting does not wait for the model to load: the first answer
    comes about 0.6s after the start, measured on the machine this was written
    on, and the caller gets on with something else meanwhile. So the first
    answer is allowed longer than the rest.
    """

    # Once warm an answer takes about 9 ms there, pipe included, so a wait this
    # long means the process is stuck, and it is put down rather than waited
    # on again.
    TIMEOUT = 5.0
    # The first answer also waits for Python, onnxruntime and the model.
    FIRST_TIMEOUT = 30.0
    # How often a wait looks up to see whether the caller has given up on it.
    POLL = 0.05

    def __init__(self, script=ENGINE, python=None):
        self.script = str(script)
        self.python = python or sys.executable
        self.problem = None          # why the last answer was None, for a person
        self._proc = None
        self._replies = queue.Queue()
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._closed = False
        self._warm = False

    def start(self):
        """Start the process if it is not already running. True if it is."""
        if self._closed:
            return False
        if self._proc is not None:
            return True
        try:
            proc = subprocess.Popen(
                [self.python, "-X", "utf8", self.script],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                bufsize=1, **POPEN_FLAGS)
        except (OSError, ValueError) as exc:
            self._give_up("would not start: %s" % exc)
            return False
        self._proc = proc
        threading.Thread(target=self._read, args=(proc,), daemon=True).start()
        return True

    def _read(self, proc):
        """Every line the process writes, onto a queue, then None at the end.
        A thread of its own, because a pipe on Windows cannot be waited on
        with a timeout any other way."""
        try:
            for line in proc.stdout:
                self._replies.put(line)
        except (OSError, ValueError):
            pass
        self._replies.put(None)

    def ask(self, fen, elo, give_up=None):
        """Every legal move with how likely a player rated elo is to play it,
        most likely first, as (uci, probability) pairs. None if there is no
        answer, and self.problem then says why.

        give_up is asked every few hundredths of a second while this waits.
        When it says yes the wait ends and None comes back at once; the answer
        that turns up later is recognised by its id and thrown away.
        """
        with self._lock:
            if not self.start():
                return None
            ident = next(self._ids)
            request = json.dumps({"id": ident, "fen": fen, "elo": int(elo)})
            try:
                self._proc.stdin.write(request + "\n")
                self._proc.stdin.flush()
            except (OSError, ValueError) as exc:
                self._give_up("the pipe is closed: %s" % exc)
                return None
            limit = self.TIMEOUT if self._warm else self.FIRST_TIMEOUT
            end = time.monotonic() + limit
            while True:
                if give_up is not None and give_up():
                    self.problem = "given up on"
                    return None
                left = end - time.monotonic()
                if left <= 0:
                    self._give_up("no answer in %g seconds" % limit)
                    return None
                try:
                    line = self._replies.get(timeout=min(left, self.POLL))
                except queue.Empty:
                    continue
                if line is None:
                    self._give_up("the process ended")
                    return None
                try:
                    reply = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(reply, dict) or reply.get("id") != ident:
                    continue             # the answer to a question given up on
                self._warm = True
                if reply.get("fatal"):
                    # The model did not load, so every later answer from this
                    # process would be this one again.
                    self._give_up(str(reply.get("error")))
                    return None
                if "error" in reply:
                    self.problem = str(reply["error"])
                    return None
                self.problem = None
                return [(str(uci), float(p)) for uci, p in reply.get("moves", [])]

    def alive(self):
        """Whether a question asked now could be answered."""
        return (not self._closed and self._proc is not None
                and self._proc.poll() is None)

    def _give_up(self, why):
        self.problem = why
        self.close()

    def close(self):
        """End the process. Safe from any thread, and safe to call twice.

        stdin is closed first, which engine.py takes as its cue to exit, and
        the process is killed if it has not a moment later. stdout is left to
        the reading thread, which is still inside a read on it and gets its end
        of file once the process has gone; closing it from here would wait on
        that read.
        """
        self._closed = True
        proc = self._proc
        if proc is None:
            return
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired):
                pass
        except OSError:
            pass
