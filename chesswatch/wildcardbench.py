"""How often the check pass follows a game, and how often it invents a move.

Run:  python wildcardbench.py [--full] [--depth N]

Three rules are run over the same bytes, frame for frame, so the numbers can
be compared to each other rather than to a memory of another run:

  all 64        what main does. Any square the reader will not name and the
                whole pass is refused.
  shallowest    treat "?" as a wildcard inside the existing search, which
                stops at the shallowest run of moves that fits. This is the
                design PR #15 cut, kept here because a rule that fabricates
                is the only thing that can show a rule that does not.
  unique        this branch. Every run up to the depth limit is played out and
                the answer is taken only when they all arrive at the same
                position.

Followed means the recorded moves ARE the moves that were played, all the way
to the frame on screen. Invented means the record holds a move that was not
played, which is the number that has to be zero.
"""

import sys
import time
import tempfile

import chess
from PIL import ImageFilter

import watcher as W
import pieces as P
import fakeboard as F
from shots import shot

# A real game rather than a line chosen to be easy: two captures, a castle, a
# pawn taken on the square it moved to, which is the shape that made the
# shallowest rule invent a move.
GAME = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "b4", "Bxb4", "c3", "Ba5",
        "d4", "exd4", "O-O", "d6", "cxd4", "Bb6", "Nc3", "Na5", "Bd3", "Ne7"]


class Shallowest(W.BoardTracker):
    """PR #15's rule, for measuring against.

    The wildcard lives inside the old search: unread squares are skipped and
    the shallowest run of moves that fits the rest wins. Kept out of watcher.py
    on purpose. It is a fixture, not an option.
    """

    def _solve_wildcard(self, rows, depth):
        target = self._target_map(rows)
        wrong = self._wrong_known(target)
        if wrong == 0:
            return None
        for limit in range(1, depth + 1):
            if wrong > self.SQUARES_PER_MOVE * limit:
                continue
            found = self._shallow_walk(target, limit, wrong, [])
            orders = {tuple((m.from_square, m.to_square) for m in seq)
                      for seq in found}
            if len(orders) == 1:
                return found[0]
            if orders:
                return None
        return None

    def _shallow_walk(self, target, k, wrong, prefix, limit=3):
        out = []
        for move in W._ordered_moves(self.board):
            touched = self._touched(move)
            before = sum(1 for sq in touched if target[sq] != "?"
                         and self._symbol_at(sq) != target[sq])
            self.board.push(move)
            after = sum(1 for sq in touched if target[sq] != "?"
                        and self._symbol_at(sq) != target[sq])
            now = wrong + after - before
            if k == 1:
                if now == 0:
                    out.append(prefix + [move])
            elif now <= self.SQUARES_PER_MOVE * (k - 1):
                out.extend(self._shallow_walk(target, k - 1, now,
                                              prefix + [move], limit - len(out)))
            self.board.pop()
            if len(out) >= limit:
                break
        return out


def every_fit(tracker, rows, depth):
    """Every run of up to `depth` moves that fits the squares the reader named.

    Every legal run is played out and the whole board is looked at afterwards.
    No pruning, no early exit, no cleverness of any kind, which is the point:
    the search in watcher.py prunes and stops early, and this is what it has to
    agree with. Costs about 30 to the power of depth, so depth 3 is the most
    anyone should ask it for.

    Returns (positions, runs): the end positions reached, and the length and
    from-to pairs of each run that reached one.
    """
    target = tracker._target_map(rows)
    positions, runs = set(), set()

    def walk(prefix):
        if all(target[sq] == "?" or tracker._symbol_at(sq) == target[sq]
               for sq in chess.SQUARES):
            positions.add((tracker.board.board_fen(), tracker.board.turn))
            runs.add((len(prefix),
                      tuple((m.from_square, m.to_square) for m in prefix)))
        if len(prefix) == depth:
            return
        for move in W._ordered_moves(tracker.board):
            tracker.board.push(move)
            walk(prefix + [move])
            tracker.board.pop()

    walk([])
    return positions, runs


def tracker_at(cls, reader, ply, flipped):
    """A tracker that has followed the game as far as `ply` by occupancy alone,
    which is the state the check pass is called in."""
    t = cls(directory=tempfile.mkdtemp(), reader=reader)
    t.preferred_flipped = flipped
    t.feed(W.START_BLACK_VIEW if flipped else W.START_WHITE_VIEW)
    board = chess.Board()
    for san in GAME[:ply]:
        board.push_san(san)
        t.feed(W.occupancy_of(board, flipped))
    return t


def board_at(ply):
    board = chess.Board()
    for san in GAME[:ply]:
        board.push_san(san)
    return board


def screen_square(square, flipped):
    """Where a board square is drawn, as (row, col)."""
    rank, file = chess.square_rank(square), chess.square_file(square)
    return (rank, 7 - file) if flipped else (7 - rank, file)


def obstructions(img, spots, hard):
    """(label, image) for each way of drawing over each spot.

    A pointer sits where the player just moved a piece, so the squares of the
    moves that were missed are where a real one lands and also where hiding
    one costs the most.
    """
    out = [("clean", img)]
    for row, col in spots:
        out.append(("pointer", F.with_pointer(img, row, col)))
        out.append(("big pointer", F.with_pointer(img, row, col, size=1.8)))
        out.append(("panel", F.with_panel(img, row, col, 1, 1)))
        if hard:
            out.append(("covered", F.cover(img, row, col)))
    return out


def verdict(tracker, before_game, ply):
    """followed / invented / behind / restarted for one check pass."""
    if tracker.game is not before_game:
        return "restarted"
    moves = tracker.game.moves
    if moves != GAME[:len(moves)]:
        return "invented"
    return "followed" if len(moves) == ply else "behind"


RULES = ("all 64", "shallowest", "unique")


def run(full=False, depth=None, margin=None, nodes=None):
    if margin is not None:
        W.BoardTracker.WILDCARD_MARGIN = margin
    if nodes is not None:
        W.BoardTracker.WILDCARD_NODES = nodes
    print("margin %d, budget %d positions, depth %s" % (
        W.BoardTracker.WILDCARD_MARGIN, W.BoardTracker.WILDCARD_NODES,
        depth or W.BoardTracker.MAX_CATCHUP))
    reader = P.PieceReader()
    if not reader.ready:
        print("no piece templates")
        return 1

    sets = [("chesscom", shot("1"), chess.Board(
        "rnbqkbnr/pp1p1ppp/4p3/2p5/3PP3/8/PPP2PPP/RNBQKBNR w KQkq - 0 3")),
        ("flat", shot("6"), chess.Board())]
    sizes = (None, 480) if full else (None,)
    stales = (1, 2, 3) if full else (1, 3)
    plies = range(4, len(GAME) + 1) if full else range(4, len(GAME) + 1, 2)

    tally = {rule: {} for rule in RULES}
    frames = 0
    spent = {rule: 0.0 for rule in RULES}
    hardest = 0

    for name, path, ref in sets:
        for flipped in (False, True):
            import watcher
            from PIL import Image
            rect = W.find_board(Image.open(path).convert("RGB"))
            render = F.Renderer(path, rect, ref)
            for size in sizes:
                for ply in plies:
                    board = board_at(ply)
                    if not render.can_render(board):
                        continue
                    shown = render.render(board, flipped, size)
                    for blur in (0.0, 1.1):
                        img = (shown if not blur else
                               shown.filter(ImageFilter.GaussianBlur(blur)))
                        for stale in stales:
                            if stale > ply:
                                continue
                            missed = board_at(ply - stale)
                            spots = []
                            for san in GAME[ply - stale:ply]:
                                move = missed.parse_san(san)
                                for sq in (move.from_square, move.to_square):
                                    at = screen_square(sq, flipped)
                                    if at not in spots:
                                        spots.append(at)
                                missed.push(move)
                            for label, frame in obstructions(img, spots[:4],
                                                             full):
                                frames += 1
                                for rule in RULES:
                                    cls = (Shallowest if rule == "shallowest"
                                           else W.BoardTracker)
                                    t = tracker_at(cls, reader, ply - stale,
                                                   flipped)
                                    game = t.game
                                    start = time.time()
                                    if rule == "all 64":
                                        rows, _ = reader.classify(
                                            frame, W.grid_of(t.board, flipped))
                                        if not any("?" in row for row in rows):
                                            t.check(frame, depth)
                                    else:
                                        t.check(frame, depth)
                                    took = time.time() - start
                                    spent[rule] += took
                                    if rule == "unique":
                                        hardest = max(hardest, took)
                                    got = verdict(t, game, ply)
                                    key = (label, got)
                                    tally[rule][key] = tally[rule].get(key, 0) + 1

    print("%d frames, %d tracked plies per rule" % (frames, frames))
    print()
    print("%-12s %9s %9s %9s %9s" % ("", "followed", "behind", "invented",
                                     "restarted"))
    for rule in RULES:
        got = tally[rule]
        totals = {k: sum(n for (lab, kind), n in got.items() if kind == k)
                  for k in ("followed", "behind", "invented", "restarted")}
        print("%-12s %8.1f%% %9d %9d %9d  (%.0f s)" % (
            rule, 100.0 * totals["followed"] / max(1, frames),
            totals["behind"], totals["invented"], totals["restarted"],
            spent[rule]))
    print()
    labels = []
    for rule in RULES:
        for (lab, _kind) in tally[rule]:
            if lab not in labels:
                labels.append(lab)
    print("followed, by what was drawn on the board")
    print("%-14s %10s %11s %8s" % ("", "all 64", "shallowest", "unique"))
    for lab in labels:
        row = []
        for rule in RULES:
            got = tally[rule]
            seen = sum(n for (l, _k), n in got.items() if l == lab)
            hit = got.get((lab, "followed"), 0)
            row.append(100.0 * hit / max(1, seen))
        print("%-14s %9.1f%% %10.1f%% %7.1f%%" % (lab, row[0], row[1], row[2]))
    print()
    print("slowest single unique check: %.0f ms" % (1000 * hardest))
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]

    def opt(name):
        return int(args[args.index(name) + 1]) if name in args else None

    sys.exit(run(full="--full" in args, depth=opt("--depth"),
                 margin=opt("--margin"), nodes=opt("--nodes")))
