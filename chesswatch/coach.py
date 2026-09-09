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

Once that answer is settled the same position is asked a second question: which
move you might play instead that is really worse. That answer arrives on its
own, as a "mistake" message, because it takes longer and the move to play must
not wait for it.

Stockfish is not in this repository. It is found at $STOCKFISH_PATH, in the
sibling holochess/engine/stockfish folder, in chesswatch/engine, or on PATH.
"""

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections import namedtuple
from pathlib import Path

import chess
import chess.engine

APP_DIR = Path(__file__).resolve().parent

# Stockfish is a console program, and Windows gives one started from a process
# with no console of its own a fresh console window. popen_uci passes these
# through to Popen. CREATE_NO_WINDOW exists only on Windows.
POPEN_FLAGS = {}
if sys.platform == "win32":
    POPEN_FLAGS["creationflags"] = subprocess.CREATE_NO_WINDOW

THINK_CHOICES = (0.3, 1.0, 2.5)
DEFAULT_THINK = 1.0


def nearest_think(value):
    """The offered think time closest to what was asked for.

    config.json is a text file and nothing stops it holding 60. The choices are
    the whole of the argument that a longer think does not cost you a core
    while you sit over a move, so the bound is enforced where the untrusted
    number comes in rather than by the widget that usually writes it. Callers
    inside the program still pass what they like; the tests search for a tenth
    of a second.
    """
    try:
        want = float(value)
    except (TypeError, ValueError):
        return DEFAULT_THINK
    return min(THINK_CHOICES, key=lambda offered: abs(offered - want))


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


# ------------------------------------------------------- the move to avoid

# The second answer is a move worth being warned off: one you might actually
# play, that a search says is really worse. That is a different problem from
# finding the best move, and the obvious approach does not work.
#
# Measured over 40 positions from real recorded games: asking the engine for
# its top five and taking the worst of them finds nothing to warn about in more
# than half of them, because the fifth best move is only 0.32 behind the best at
# the median. It also costs the move to play two to three plies of depth, since
# every extra line comes out of the same search. So the top five are not it. The
# best move is still found by the search it always was, on its own, and the move
# to avoid is a second pass afterwards over every legal move.
#
# That pass is two searches and sometimes a third, and each one is there because
# the one before it is not to be trusted on its own:
#
#   nominate  every legal move scored roughly, which is the only way to see the
#             moves that are bad enough to be worth showing. A depth limit
#             rather than a time limit, because lines searched to different
#             depths cannot be compared with each other at all.
#   verify    the shortlist searched properly. This is what picks the move, and
#             two thirds of what nominate puts up does not survive it.
#   confirm   only for an extraordinary claim. Measured over the same games,
#             one red arrow in nine pointed at a move a longer search says is
#             fine, and every one of those was a shallow search inventing a
#             forced mate. A drop that rests on a mate, or one too large to
#             believe, is put to a longer search on those two moves alone.
#
# What comes out of that: a red arrow on 53% of real positions, and on the 45
# it was measured over, every single one of them was still at least half a pawn
# worse under an independent two second search. The other 47% is not a failure
# to find something. Half of it is positions where nothing tempting is really
# worse, and the rest is positions already so won or lost that the difference
# between two moves is not a lesson.

Line = namedtuple("Line", "score move depth")

MATE_CP = 100000        # what a mate is worth when two scores are subtracted
SHORTLIST = 4           # how many nominees get searched properly
NOMINATE_DROP = 70      # how far behind a move has to look to become one
MIN_DROP = 90           # and how far behind it has to be once it is searched
DECIDED = 500           # past this the game is over bar the moves
HUGE = 800              # a drop this big is put to a longer search first

# How deep the two cheap passes go. The think-time dial is what a player has
# said about how much of a core this may hold, so it drives this pass too: a
# shallower nominate and verify are cheaper and less certain, which is what the
# dial means everywhere else. Measured at 1.0s: the whole pass takes 0.35s at
# the median and 0.82s at the 90th percentile.
DEPTHS = {0.3: (8, 12), 1.0: (10, 14), 2.5: (12, 16)}

# A depth limit is open ended: a position that is hard to search would hold a
# core for as long as it takes. This is the ceiling on one pass, as a multiple
# of the think time. It almost never fires, and when it does the lines can come
# back at different depths, which comparable() then refuses.
CEILING = 2.0


def depths_for(think_seconds):
    """How deep the nominate and verify passes go, at this think time."""
    return DEPTHS[nearest_think(think_seconds)]


def value(score):
    """A score as one number, so two of them can be subtracted. Mate is not a
    number of pawns, so it becomes a number no evaluation can reach."""
    return score.score(mate_score=MATE_CP)


def comparable(lines):
    """Whether these lines are evidence about each other.

    Only if they were all searched to the same depth. A move that looks lost at
    depth 12, held up against a best move seen at depth 20, is an artefact of
    the two depths rather than a mistake.
    """
    return bool(lines) and len({line.depth for line in lines}) == 1


def shortlist(board, best, rough, drop=NOMINATE_DROP, most=SHORTLIST):
    """Which roughly scored moves are worth searching properly.

    Captures and checks first, because that is where a player's eye goes and a
    mistake nobody was ever tempted by teaches nothing. Then the rest in the
    engine's own order, so what is left is the best of the bad moves rather
    than the worst move on the board.
    """
    if not rough:
        return []
    top = value(rough[0].score)
    bad = [line.move for line in rough
           if line.move != best and top - value(line.score) >= drop]
    tempting = [move for move in bad
                if board.is_capture(move) or board.gives_check(move)]
    return (tempting + [move for move in bad if move not in tempting])[:most]


def most_tempting(drops, bar=MIN_DROP):
    """The closest call among the moves that are really worse.

    The smallest drop that still clears the bar, rather than the biggest drop
    there is. A move a pawn behind the best is one a player is about to make;
    the worst move on the board is one nobody was going to play.
    """
    for drop, move in sorted(drops, key=lambda pair: pair[0]):
        if drop >= bar:
            return drop, move
    return None


def already_decided(best, bad, margin=DECIDED):
    """Whether the game is past the point where this is a lesson.

    Five pawns up, every move wins and the difference between two of them is
    noise; five pawns down, the same in reverse. Measured, this is 18% of real
    positions, and warning in them was where the wording went silly: "not
    e8=N+, 3.1 worse" about two moves that both promote and both win.
    """
    return value(bad) >= margin or value(best) <= -margin


def needs_confirming(drop, best, bad, huge=HUGE):
    """Whether a claim is extraordinary enough to be searched again.

    Anything resting on a mate, and anything so large that a wrong search is
    the likelier explanation: a shortlisted move rarely loses eight pawns.
    """
    return drop >= huge or best.is_mate() or bad.is_mate()


def drop_words(best, bad):
    """How much worse the second move is, in words rather than centipawns.

    Mate is not a number of pawns. Subtracting the two scores would say
    "989.4 worse", so a position that turns on a mate says so instead.
    """
    if best.is_mate() and best.mate() > 0 and not (bad.is_mate() and bad.mate() > 0):
        return "throws away mate in %d" % best.mate()
    if bad.is_mate() and bad.mate() < 0 and not (best.is_mate() and best.mate() < 0):
        return "walks into mate in %d" % abs(bad.mate())
    return "%.1f worse" % ((value(best) - value(bad)) / 100.0)


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

    def __init__(self, path, think_seconds=DEFAULT_THINK):
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
            engine = chess.engine.SimpleEngine.popen_uci(self.path, **POPEN_FLAGS)
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
        if not board.is_valid():
            # Stockfish does not survive a position with a king missing off it:
            # it exits with an access violation and takes the coaching half of
            # the program down with it, since the thread cannot restart the
            # engine. board_from_grid already refuses one, so this is the second
            # line rather than the only one, and ask() takes any string.
            self._settle(fen)
            return
        if board.is_game_over():
            self._settle(fen)
            self.out.put(("advice", {"fen": fen, "over": True}))
            return
        try:
            best = self._search(engine, board, fen)
            # Second, and only once the move to play is on screen. It is the
            # answer that matters, and this pass costs about as long again.
            if best is not None and not self._cut():
                self._mistake(engine, board, fen, best)
        except Exception as exc:
            self.out.put(("engine", "Stockfish stopped: %s" % exc))
            self.stop_flag.set()

    def _search(self, engine, board, fen):
        """Read the engine to the end of its stream, putting the answer up as
        it improves and marking the last one final. Hands back the move it
        settled on, which is what the second pass is measured against.

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
            return None
        self._settle(fen)
        if best is None:
            return None
        self._publish(board, fen, best, True)
        return best["pv"][0]

    def _cut(self):
        """Whether there is any point carrying on. A question waiting means the
        board on screen has moved past this position, so its answer would be
        thrown away at the draw step anyway."""
        with self._lock:
            return self._want is not None or self.stop_flag.is_set()

    def _scan(self, engine, board, limit, multipv, root_moves=None):
        """Every line the engine has for these moves, best first.

        Registered as the live search the same way the main one is, so a new
        position stops it where it stands rather than at the end of the pass.
        Read to the end of the stream for the same reason as _search: leaving
        the iterator early leaves the engine searching.
        """
        with engine.analysis(board, limit, multipv=multipv,
                             root_moves=root_moves) as an:
            with self._lock:
                self._live = an
                stale = self._want is not None or self.stop_flag.is_set()
            if stale:
                self._end(an)
            try:
                for _ in an:
                    pass
                got = an.multipv
            finally:
                with self._lock:
                    self._live = None
        lines = [Line(info["score"].pov(board.turn), info["pv"][0],
                      info.get("depth") or 0)
                 for info in got if info.get("pv") and "score" in info]
        lines.sort(key=lambda line: -value(line.score))
        return lines

    def _mistake(self, engine, board, fen, best):
        """The move worth being warned off, published on its own.

        Two searches and sometimes a third; the comment above Line says why each
        one is there. Every one of them can be cut short by the next position
        arriving, which is checked between them: this whole pass is about a
        board that may well have been played past while it ran.
        """
        nominate, verify = depths_for(self.think_seconds)
        ceiling = CEILING * self.think_seconds

        rough = self._scan(engine, board,
                           chess.engine.Limit(depth=nominate, time=ceiling),
                           board.legal_moves.count())
        if self._cut():
            return
        picks = shortlist(board, best, rough)
        if not picks:
            return                       # nothing here looks bad enough

        roots = [best] + picks
        fine = self._scan(engine, board,
                          chess.engine.Limit(depth=verify, time=ceiling),
                          len(roots), roots)
        if self._cut() or not comparable(fine):
            return
        scores = {line.move: line.score for line in fine}
        if best not in scores:
            return
        chosen = most_tempting([(value(scores[best]) - value(scores[move]), move)
                                for move in picks if move in scores])
        if chosen is None:
            return                       # the proper search says they are fine
        drop, move = chosen
        if already_decided(scores[best], scores[move]):
            return

        if needs_confirming(drop, scores[best], scores[move]):
            # On these two moves alone, with the same clock the move to play
            # got, which is far deeper than the verify pass reached.
            pair = self._scan(engine, board,
                              chess.engine.Limit(time=self.think_seconds),
                              2, [best, move])
            if self._cut() or len(pair) != 2 or not comparable(pair):
                return
            scores = {line.move: line.score for line in pair}
            if best not in scores or move not in scores:
                return
            drop = value(scores[best]) - value(scores[move])
            if drop < MIN_DROP or already_decided(scores[best], scores[move]):
                return                   # the longer look says it is fine

        san, text, _ = describe(board, move, None)
        self.out.put(("mistake", {
            "fen": fen,
            "turn": "white" if board.turn == chess.WHITE else "black",
            "san": san,
            "uci": move.uci(),
            "text": text,
            "worse": drop_words(scores[best], scores[move]),
            "drop": drop,
        }))

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
