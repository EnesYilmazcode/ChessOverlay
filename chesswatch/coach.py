"""The teaching half: Stockfish, on whatever position ChessWatch is reading.

The reader must never wait for the engine, so the engine lives on its own
thread and answers a question at a time. Ask for a position with ask(); the
answer turns up on the out queue whenever it is ready. Only the newest question
matters, so an answer to a position that has already been played past is
dropped rather than shown.

The engine is read while it is still thinking rather than only once it has
finished, so a first answer is on screen in a few hundredths of a second and
improves from there. Every answer says whether it is the last word on that
position: one with "final" false is the engine still looking, and may change.

Stockfish is not in this repository. It is found at $STOCKFISH_PATH, in the
sibling holochess/engine/stockfish folder, in chesswatch/engine, or on PATH.
"""

import os
import queue
import shutil
import threading
import time
from pathlib import Path

import chess
import chess.engine

APP_DIR = Path(__file__).resolve().parent

THINK_CHOICES = (0.3, 1.0, 2.5)


def find_engine():
    """The Stockfish binary, or None. Both halves of this repo share one copy."""
    env = os.environ.get("STOCKFISH_PATH")
    if env and Path(env).exists():
        return Path(env)
    for folder in (APP_DIR / "engine" / "stockfish",
                   APP_DIR.parent / "holochess" / "engine" / "stockfish"):
        if folder.is_dir():
            for f in sorted(folder.iterdir()):
                if f.is_file() and f.stem.startswith("stockfish"):
                    if os.name != "nt" or f.suffix.lower() == ".exe":
                        return f
    found = shutil.which("stockfish")
    return Path(found) if found else None


def describe(board, move, score):
    """What to show a player who is still learning the names of things."""
    piece = board.piece_at(move.from_square)
    name = chess.piece_name(piece.piece_type) if piece else "piece"
    san = board.san(move)
    text = "%s: %s to %s" % (name, chess.square_name(move.from_square),
                             chess.square_name(move.to_square))
    if board.is_capture(move):
        taken = board.piece_at(move.to_square)
        if taken is not None:
            text += ", taking the " + chess.piece_name(taken.piece_type)
        else:
            text += ", taking the pawn"      # en passant leaves the square empty
    if board.gives_check(move):
        text += ", with check"
    return san, text, score


def read_score(score, turn):
    """Stockfish's number, from the point of view of whoever is to move."""
    pov = score.pov(turn)
    mate = pov.mate()
    if mate is not None:
        if mate > 0:
            return "mate in %d" % mate
        return "mated in %d" % abs(mate)
    cp = pov.score()
    if cp is None:
        return ""
    return "%+.1f" % (cp / 100.0)


class Coach(threading.Thread):
    """Answers one position at a time. Newest question wins.

    think_seconds is how long the engine gets on one position. It is a bound,
    not a promise: a position that has been played past is stopped the moment
    the next one arrives. What it is not is open ended, because between
    positions the engine goes idle, and a search left running while you decide
    on a move would hold a whole core for as long as you took.
    """

    daemon = True

    # Below this depth the top move changes several times inside a few
    # milliseconds, and putting that on screen is flicker, not information.
    FIRST_DEPTH = 8
    # The shortest time anything on screen is allowed to live, so the label and
    # the arrow cannot chase the engine faster than the eye can follow.
    PARTIAL_GAP = 0.35

    def __init__(self, path, think_seconds=1.0):
        super().__init__()
        self.path = str(path)
        self.think_seconds = think_seconds
        self.out = queue.Queue()
        self.stop_flag = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._want = None
        self._busy = None        # the position being searched at this moment
        self._done = None        # the position most recently answered in full
        self._live = None        # the running search, so a question can end it

    def ask(self, fen):
        """Look at this position. Repeats and stale positions cost nothing.

        A position already under the engine counts as a repeat. Without that it
        gets searched twice over, because a search in progress is invisible to
        everything except the thread reading it.

        Asking for something new ends the search in progress, from this thread
        rather than from the one reading the engine. The engine can go two
        seconds between lines, so the reader cannot be relied on to notice.
        """
        with self._lock:
            if fen == self._want or fen == self._busy or fen == self._done:
                return
            self._want = fen
            live = self._live
        self._end(live)
        self._wake.set()

    def stop(self):
        self.stop_flag.set()
        with self._lock:
            live = self._live
        self._end(live)
        self._wake.set()

    def _end(self, live):
        """Ask a running search to wrap up. Safe from any thread, and safe on a
        search that has already finished or an engine that has already gone."""
        if live is None:
            return
        try:
            live.stop()
        except Exception:
            pass

    def run(self):
        try:
            engine = chess.engine.SimpleEngine.popen_uci(self.path)
        except Exception as exc:
            self.out.put(("engine", "Stockfish would not start: %s" % exc))
            return
        self.out.put(("engine", "ready"))
        try:
            while not self.stop_flag.is_set():
                self._wake.wait(0.5)
                self._wake.clear()
                while True:
                    with self._lock:
                        fen, self._want = self._want, None
                        self._busy = fen
                    if fen is None or self.stop_flag.is_set():
                        break
                    try:
                        self._answer(engine, fen)
                    finally:
                        with self._lock:
                            self._busy = None
        finally:
            try:
                engine.quit()
            except Exception:
                pass

    def _settle(self, fen):
        """This position is answered as well as it is going to be. Remembering
        that is the whole of what stops one board being asked about eight times
        a second for the rest of the game."""
        with self._lock:
            self._done = fen

    def _answer(self, engine, fen):
        try:
            board = chess.Board(fen)
        except ValueError:
            self._settle(fen)
            return
        if board.is_game_over():
            self._settle(fen)
            self.out.put(("advice", {"fen": fen, "over": True}))
            return
        try:
            self._search(engine, board, fen)
        except Exception as exc:
            self.out.put(("engine", "Stockfish stopped: %s" % exc))
            self.stop_flag.set()

    def _search(self, engine, board, fen):
        """Read the engine to the end of its stream, putting the answer up as
        it improves and marking the last one final.

        The loop is never broken out of. Whoever wants the search over calls
        stop() on the handle, the engine answers with its best move, and that
        ends the iterator by itself. Leaving the iterator early and stopping
        afterwards leaves the engine searching and the next question waiting on
        it for good.
        """
        best = None              # newest full line, the one published as final
        shown = None             # the move on screen
        shown_at = 0.0
        with engine.analysis(board,
                             chess.engine.Limit(time=self.think_seconds)) as an:
            with self._lock:
                self._live = an
                # Starting a search costs a round trip to the engine, and a
                # question that arrived during it would otherwise have found
                # nothing to stop and be answered a whole think late.
                stale = self._want is not None or self.stop_flag.is_set()
            if stale:
                self._end(an)
            try:
                for info in an:
                    pv = info.get("pv") or []
                    if not pv or "score" not in info:
                        continue
                    best = info
                    if (info.get("depth") or 0) < self.FIRST_DEPTH:
                        continue
                    now = time.monotonic()
                    if pv[0] == shown or (shown is not None
                                          and now - shown_at < self.PARTIAL_GAP):
                        continue
                    shown, shown_at = pv[0], now
                    self._publish(board, fen, info, False)
            finally:
                with self._lock:
                    self._live = None
                    # A question arriving mid-search means this board is not on
                    # screen any more. Its answer would be dropped at the draw
                    # step regardless, and it never finished, so it is not
                    # remembered as answered either.
                    cut = self._want is not None or self.stop_flag.is_set()
        if cut:
            return
        self._settle(fen)
        if best is not None:
            self._publish(board, fen, best, True)

    def _publish(self, board, fen, info, final):
        move = info["pv"][0]
        san, text, _ = describe(board, move, info.get("score"))
        self.out.put(("advice", {
            "fen": fen,
            "over": False,
            "final": final,
            "depth": info.get("depth") or 0,
            "turn": "white" if board.turn == chess.WHITE else "black",
            "san": san,
            "uci": move.uci(),
            "text": text,
            "score": read_score(info["score"], board.turn) if "score" in info else "",
        }))
