"""The teaching half: Stockfish, on whatever position ChessWatch is reading.

The reader must never wait for the engine, so the engine lives on its own
thread and answers a question at a time. Ask for a position with ask(); the
answer turns up on the out queue whenever it is ready. Only the newest question
matters, so an answer to a position that has already been played past is
dropped rather than shown.

Stockfish is not in this repository. It is found at $STOCKFISH_PATH, in the
sibling holochess/engine/stockfish folder, in chesswatch/engine, or on PATH.
"""

import os
import queue
import shutil
import threading
from pathlib import Path

import chess
import chess.engine

APP_DIR = Path(__file__).resolve().parent


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
    """Answers one position at a time. Newest question wins."""

    daemon = True

    def __init__(self, path, movetime=0.30):
        super().__init__()
        self.path = str(path)
        self.movetime = movetime
        self.out = queue.Queue()
        self.stop_flag = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._want = None
        self._done = None

    def ask(self, fen):
        """Look at this position. Repeats and stale positions cost nothing."""
        with self._lock:
            if fen == self._want or fen == self._done:
                return
            self._want = fen
        self._wake.set()

    def stop(self):
        self.stop_flag.set()
        self._wake.set()

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
                    if fen is None or self.stop_flag.is_set():
                        break
                    self._answer(engine, fen)
        finally:
            try:
                engine.quit()
            except Exception:
                pass

    def _answer(self, engine, fen):
        try:
            board = chess.Board(fen)
        except ValueError:
            return
        if board.is_game_over():
            self.out.put(("advice", {"fen": fen, "over": True}))
            return
        try:
            info = engine.analyse(board, chess.engine.Limit(time=self.movetime))
        except Exception as exc:
            self.out.put(("engine", "Stockfish stopped: %s" % exc))
            self.stop_flag.set()
            return
        pv = info.get("pv") or []
        if not pv:
            return
        san, text, _ = describe(board, pv[0], info.get("score"))
        with self._lock:
            self._done = fen
        self.out.put(("advice", {
            "fen": fen,
            "over": False,
            "turn": "white" if board.turn == chess.WHITE else "black",
            "san": san,
            "uci": pv[0].uci(),
            "text": text,
            "score": read_score(info["score"], board.turn) if "score" in info else "",
        }))
