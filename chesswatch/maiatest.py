"""Checks for the rating half of the red arrow: maia/engine.py and the client
that talks to it.

Run:  python maiatest.py

Nothing in here is skipped. The red arrow can do without Maia, so the app
carries on quietly when onnxruntime is missing, and that is exactly why this
file must not: a model that stopped loading would otherwise show up as nothing
at all, a red arrow that no longer moves with the slider. A machine that cannot
load the model fails every check that needs it, and says why.

engine.py is a separate AGPL-3.0 program, and this file is on the MIT side of
that line like the rest of chesswatch. It only ever starts it as a process and
reads what it writes, and one of the checks is that nothing on this side does
more than that.
"""

import ast
import glob
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

import chess

import maiaclient

R = []
HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "maia", "engine.py")

START = chess.STARTING_FEN
# Black to move, where the top move at 500 is not the top move at 1500 even
# with no history behind the board. One of 34 such positions in 97 sampled from
# recorded games.
SPLIT = "rn1qkbnr/pp3ppp/8/3p4/3Q4/7P/PP2PPBP/RNB1K2R b KQkq - 0 7"
MIDDLE = "3rrq1k/pp3pb1/5N1p/1Pn1PQ2/8/P2R3P/4R1B1/7K b - - 4 30"
PINNED = "r2q1rk1/1pp2ppp/p2p1n2/2bNp1B1/2BnP3/P2P1b2/1PPQ1PPP/R4RK1 w - - 0 6"
# A capture that promotes, for each colour: black's is white's mirrored.
PROMOTE_W = "r1n5/1P4k1/8/8/8/8/6K1/8 w - - 0 1"
PROMOTE_B = "8/6k1/8/8/8/8/1p4K1/R1N5 b - - 0 1"
CASTLE = "r3k2r/pppq1ppp/2npbn2/4p3/4P3/2NPBN2/PPPQ1PPP/R3K2R w KQkq - 0 1"
PASSANT_B = "rnbqkbnr/ppp1pppp/8/8/2PpP3/5N2/PP1P1PPP/RNBQKB1R b KQkq c3 0 3"
PASSANT_W = "rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3"

# The most likely move and its probability, worked out by maia3's own UCI
# engine (CSSLab/maia3 at 1e13597, torch, maia3-5m.pt at Hugging Face commit
# b6559de2) with --temperature 0 and a bare FEN, so its history is the current
# board eight times over, the same as engine.py. Probabilities are rounded to
# four places there, so they are compared to within a thousandth. Chosen with a
# clear gap to the second move at every rating, so the check is about the
# answer rather than about rounding.
GOLDEN = [
    (START, 500, "e2e4", 0.5207),
    (START, 1000, "e2e4", 0.6780),
    (START, 1500, "e2e4", 0.6428),
    (SPLIT, 500, "b8c6", 0.3675),
    (SPLIT, 1000, "b8c6", 0.5027),
    (SPLIT, 1500, "g8f6", 0.6230),
    (MIDDLE, 500, "c5d3", 0.4464),
    (MIDDLE, 1000, "c5d3", 0.4513),
    (MIDDLE, 1500, "g7f6", 0.5602),
    (PINNED, 500, "g2f3", 0.2843),
    (PINNED, 1000, "d5f6", 0.3126),
    (PINNED, 1500, "d5f6", 0.4950),
    (PROMOTE_W, 500, "b7a8q", 0.5855),
    (PROMOTE_W, 1500, "b7a8q", 0.9517),
    (PROMOTE_B, 1000, "b2a1q", 0.9094),
    (CASTLE, 1000, "e1c1", 0.3754),
    (PASSANT_B, 500, "d4d3", 0.2166),
    (PASSANT_B, 1500, "d4c3", 0.4000),
]


def check(name, got, want):
    ok = got == want
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        print("        got  ", got)
        print("        want ", want)
    R.append(ok)
    return ok


class Raw:
    """engine.py spoken to line by line, with nothing of the client in the way,
    so the protocol itself is what is checked."""

    def __init__(self, prelude=None):
        if prelude is None:
            argv = [sys.executable, "-X", "utf8", ENGINE]
        else:
            # Run engine.py as __main__ after some code of our own, the way
            # python would run it, so that code can take things away first.
            code = prelude + "\nimport runpy, sys\nsys.argv = [%r]\n" \
                   "runpy.run_path(%r, run_name='__main__')\n" % (ENGINE, ENGINE)
            argv = [sys.executable, "-X", "utf8", "-c", code]
        # stderr to a file rather than a pipe nobody reads, which would stop
        # the process dead if onnxruntime ever wrote enough warnings to fill it.
        self.errors = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE,
                                     stderr=self.errors, text=True,
                                     encoding="utf-8", bufsize=1,
                                     **maiaclient.POPEN_FLAGS)

    def line(self, text, seconds=60):
        """Send one line and read one back, or None if none came."""
        got = []

        def read():
            got.append(self.proc.stdout.readline())
        try:
            self.proc.stdin.write(text + "\n")
            self.proc.stdin.flush()
        except OSError:
            return None
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(seconds)
        if not got or not got[0]:
            return None
        try:
            return json.loads(got[0])
        except ValueError:
            return got[0]

    def send(self, **request):
        return self.line(json.dumps(request))

    def close(self):
        """Close stdin and return the exit code, or None if it did not exit."""
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            return self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return None
        finally:
            self.errors.close()

    def stderr(self):
        try:
            self.errors.seek(0)
            return self.errors.read()[-800:]
        except (OSError, ValueError):
            return ""


def moves_of(reply):
    if isinstance(reply, dict) and isinstance(reply.get("moves"), list):
        return reply["moves"]
    return None


# ---------------------------------------------------------------- the licence

def imports_in(path):
    """Every module name a Python file imports, read rather than run."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def separation():
    print("\n-- maia/ is a separate program --------------------------")
    theirs = imports_in(ENGINE)
    check("engine.py imports no torch, by reading it",
          sorted(n for n in theirs if n.split(".")[0] == "torch"), [])
    check("  nor anything from the MIT side of chesswatch",
          sorted(n for n in theirs
                 if os.path.exists(os.path.join(HERE, n.split(".")[0] + ".py"))), [])

    ours = {}
    for path in sorted(glob.glob(os.path.join(HERE, "*.py"))):
        took = [n for n in imports_in(path)
                if n.split(".")[0] in ("maia", "engine")]
        if took:
            ours[os.path.basename(path)] = took
    check("no MIT file in chesswatch imports anything out of maia/", ours, {})

    folder = os.path.join(HERE, "maia")
    with open(os.path.join(folder, "LICENSE"), encoding="utf-8") as fh:
        head = fh.read(200)
    check("maia/ carries the AGPL-3.0 text",
          "GNU AFFERO GENERAL PUBLIC LICENSE" in head, True)
    check("  and says what it is and where it came from",
          os.path.exists(os.path.join(folder, "README.md")), True)
    check("the model is in the repository, not downloaded at run time",
          os.path.getsize(os.path.join(folder, "maia3-5m.onnx")) > 20 * 2**20, True)


# ---------------------------------------------------------------- the engine

def without_torch():
    print("\n-- it runs with torch taken away ------------------------")
    # sys.modules["torch"] = None makes every `import torch` in the process
    # raise, including one buried inside a dependency.
    raw = Raw(prelude="import sys\nsys.modules['torch'] = None")
    reply = raw.send(fen=START, elo=1000)
    got = moves_of(reply)
    if not check("an answer comes back from a process where torch cannot be"
                 " imported", got is not None, True):
        print("        it said: %r" % (reply,))
        print("        stderr: %s" % raw.stderr())
    raw.close()


def protocol():
    print("\n-- one line in, one line out ----------------------------")
    raw = Raw()
    t0 = time.monotonic()
    first = raw.send(fen=START, elo=1000, id=7)
    took = time.monotonic() - t0
    print("      first answer %.2fs after starting, model load included" % took)
    got = moves_of(first)
    if not check("the model loads and answers", got is not None, True):
        print("        it said: %r" % (first,))
        print("        stderr: %s" % raw.stderr())
        print("        this needs onnxruntime: pip install -r requirements.txt")
    check("  and the id asked with comes back on the answer",
          isinstance(first, dict) and first.get("id"), 7)

    positions = [START, SPLIT, MIDDLE, PINNED, PROMOTE_W, PROMOTE_B, CASTLE,
                 PASSANT_B, PASSANT_W]
    exact, summed, ordered, bounded = [], [], [], []
    for fen in positions:
        for elo in (500, 1000, 1500):
            got = moves_of(raw.send(fen=fen, elo=elo)) or []
            legal = sorted(m.uci() for m in chess.Board(fen).legal_moves)
            names = [uci for uci, _ in got]
            probs = [p for _, p in got]
            exact.append(sorted(names) == legal
                         and len(set(names)) == len(names))
            summed.append(abs(sum(probs) - 1.0) < 1e-6)
            ordered.append(probs == sorted(probs, reverse=True))
            bounded.append(all(0.0 <= p <= 1.0 for p in probs))
    n = len(exact)
    check("every legal move is in the answer once and nothing else is, in %d"
          " answers" % n, sum(exact), n)
    check("  the probabilities add up to 1", sum(summed), n)
    check("  most likely first", sum(ordered), n)
    check("  and each is a probability", sum(bounded), n)

    once = raw.send(fen=SPLIT, elo=900)
    again = raw.send(fen=SPLIT, elo=900)
    check("the same question gets exactly the same answer",
          moves_of(once) is not None and once == again, True)

    top = {}
    for elo in (500, 1500):
        got = moves_of(raw.send(fen=SPLIT, elo=elo)) or [[None, 0]]
        top[elo] = got[0][0]
    check("the rating changes the answer, with nothing but the board to go on",
          (top[500], top[1500]), ("b8c6", "g8f6"))

    wrong = []
    for fen, elo, want, p in GOLDEN:
        top = (moves_of(raw.send(fen=fen, elo=elo)) or [[None, -1.0]])[0]
        if top[0] != want or abs(top[1] - p) > 1e-3:
            wrong.append((fen, elo, want, p, top))
    check("the most likely move and its probability match maia3's own engine"
          " on %d answers" % len(GOLDEN), wrong, [])

    print("\n-- a bad question does not end it ----------------------")
    bad = raw.send(fen="this is not a position", elo=1000, id="x")
    check("a fen that is not one is an error",
          (isinstance(bad, dict) and "error" in bad and "moves" not in bad,
           isinstance(bad, dict) and bad.get("id")), (True, "x"))
    check("  and the next question is still answered",
          moves_of(raw.send(fen=START, elo=1000)) is not None, True)
    no_king = raw.send(fen="8/5ppp/8/8/8/6P1/5P1P/R5K1 w - - 0 1", elo=1000)
    check("a position with a king missing is an error, not a crash",
          isinstance(no_king, dict) and "error" in no_king, True)
    check("  and so is a line that is not JSON",
          isinstance(raw.line("hello"), dict), True)
    check("  and a question with no rating in it",
          "error" in (raw.send(fen=START) or {}), True)
    check("still answering after all of that",
          moves_of(raw.send(fen=SPLIT, elo=500)) is not None, True)
    check("a finished game has no moves to be likely",
          raw.send(fen="7k/5Q2/6K1/8/8/8/8/8 b - - 0 1", elo=1000), {"moves": []})
    check("it exits cleanly at the end of its input", raw.close(), 0)


# ---------------------------------------------------------------- the client

def client():
    print("\n-- the client the coach uses ----------------------------")
    seen = {}
    real = subprocess.Popen

    def spy(args, **kwargs):
        seen["args"] = list(args)
        seen.update(kwargs)
        return real(args, **kwargs)

    m = maiaclient.Maia()
    subprocess.Popen = spy
    try:
        started = m.start()
    finally:
        subprocess.Popen = real
    check("it starts engine.py with this Python, as a separate process",
          (started, seen.get("args")),
          (True, [sys.executable, "-X", "utf8", str(maiaclient.ENGINE)]))
    if sys.platform == "win32":
        check("  with no console window, the way Stockfish is started",
              seen.get("creationflags"), subprocess.CREATE_NO_WINDOW)
    else:
        check("  asking for nothing Windows-only elsewhere",
              "creationflags" in seen, False)

    got = m.ask(START, 1000)
    if not check("it gets an answer", got is not None, True):
        print("        problem: %s" % m.problem)
    check("  as (move, probability) pairs, most likely first",
          bool(got) and got[0][0] == "e2e4" and isinstance(got[0][1], float), True)
    check("  and a bad question is None, with the reason kept",
          (m.ask("not a fen", 1000), "bad fen" in (m.problem or "")), (None, True))

    # Given up on, the answer still arrives later. The next question must get
    # its own answer and not that one.
    t0 = time.monotonic()
    gone = m.ask(SPLIT, 500, give_up=lambda: True)
    check("a question given up on comes back at once as None",
          (gone, time.monotonic() - t0 < 0.5), (None, True))
    after = m.ask(START, 1500)
    check("  and the answer that turns up late is not handed to the next one",
          bool(after) and after[0][0] == "e2e4"
          and len(after) == chess.Board(START).legal_moves.count(), True)

    m._proc.kill()
    m._proc.wait(timeout=10)
    t0 = time.monotonic()
    dead = m.ask(START, 1000)
    check("a killed process is None, not a hang",
          (dead, time.monotonic() - t0 < 2.0), (None, True))
    check("  and the client knows it is gone", m.alive(), False)
    check("  and does not come back from the dead",
          m.ask(START, 1000), None)
    m.close()
    m.close()

    missing = maiaclient.Maia(script=os.path.join(HERE, "maia", "no-such-file.py"))
    t0 = time.monotonic()
    check("a script that is not there is None rather than an error",
          (missing.ask(START, 1000), time.monotonic() - t0 < 10), (None, True))
    missing.close()

    closed = maiaclient.Maia()
    closed.close()
    check("a client already closed starts nothing",
          (closed.ask(START, 1000), closed._proc), (None, None))


def main():
    print("engine: %s" % ENGINE)
    separation()
    without_torch()
    protocol()
    client()
    print("\n%d/%d passed" % (sum(R), len(R)))
    return 0 if all(R) else 1


if __name__ == "__main__":
    sys.exit(main())
