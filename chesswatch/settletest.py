"""Does the reader survive chess.com's move animation?

This is the dangerous case, and it is not hypothetical. chess.com slides a piece
to its destination over roughly 200ms, and for much of that slide the piece sits
centred on an intermediate square. For e2-e4 the board reads as a clean, stable
"pawn on e3" for about 86ms, and e3 is itself a legal move. A rook sliding h3-a3
passes over g3, f3, e3 and d3, all legal. Castling reads as Kf1 on the way.

So the screen genuinely shows a legal position that never happened. Counting
agreeing reads cannot tell the difference, because every read inside that window
agrees. Only outlasting the animation can.

The screen grabs are replaced with a clock-driven script, so the animation has a
real duration rather than a frame count.

Run:  python settletest.py
"""

import queue
import tempfile
import time

import chess

import watcher as W
import chesswatch as C
from fakeboard import Renderer
from shots import shot

REF = shot("1")


def animation(stages):
    """A fake screen. stages is [(image, seconds_to_show), ..., (final, None)]."""
    start = time.time()

    def grab(_region):
        elapsed = time.time() - start
        run = 0.0
        for image, hold in stages:
            if hold is None:
                return image
            run += hold
            if elapsed < run:
                return image
        return stages[-1][0]
    return grab


def worker_for(render, setup, tmp):
    w = C.Worker((0, 0, 824, 824), queue.Queue(), directory=tmp)
    w.tracker.feed(W.START_WHITE_VIEW)
    board = chess.Board()
    for san in setup:
        board.push_san(san)
        w.tracker.feed(W.occupancy_of(board, False))
    w._accepted = W.occupancy_of(board, False)
    return w, board


def main():
    render = Renderer(REF, (225, 63, 824))
    ok = True
    real_grab = C.grab

    cases = [
        # setup, the move played, what it is drawn on part way, animation ms
        ([], "e4", ["e3"], 0.20),
        ([], "e4", ["e3"], 0.40),
        ([], "e4", ["e3"], 0.55),
        ([], "e4", ["e3"], 0.90),
        (["a4", "h6", "Ra3", "h5", "Rh3", "g6"], "Ra3",
         ["Rg3", "Rf3", "Rd3"], 0.25),
        (["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"], "O-O", ["Kf1"], 0.25),
        (["e4", "e5", "Nf3", "Nc6"], "Bb5", ["Be2", "Bd3", "Bc4"], 0.30),
    ]

    try:
        for setup, played, midway, ms in cases:
            tmp = tempfile.mkdtemp()
            worker, board = worker_for(render, setup, tmp)

            final = chess.Board(board.fen())
            final.push_san(played)
            want = board.san(board.parse_san(played))

            stages = []
            for san in midway:
                step = chess.Board(board.fen())
                step.push_san(san)
                stages.append((render.render(step), ms / len(midway)))
            stages.append((render.render(final), None))

            C.grab = animation(stages)
            deadline = time.time() + 4
            while time.time() < deadline:
                worker._tick()
                if worker.tracker.game.moves[len(setup):]:
                    break
            got = worker.tracker.game.moves[len(setup):]
            good = got == [want]
            ok = ok and good
            print("%-28s drawn on %-22s -> %-8s %s"
                  % (played + " over %.0fms" % (ms * 1000),
                     ",".join(midway), got,
                     "ok" if good else "WRONG, wanted [%s]" % want))
    finally:
        C.grab = real_grab

    # A bot replying while our own move is still animating. The reading never
    # holds still until both are done, so it arrives as a two move jump, which
    # is always safe to order because the colours alternate.
    tmp = tempfile.mkdtemp()
    worker, board = worker_for(render, [], tmp)
    one, two = chess.Board(), chess.Board()
    one.push_san("e4")
    two.push_san("e4")
    two.push_san("e5")
    mid1, mid2 = chess.Board(), chess.Board(one.fen())
    mid1.push_san("e3")
    mid2.push_san("e6")
    C.grab = animation([(render.render(mid1), 0.15), (render.render(one), 0.20),
                        (render.render(mid2), 0.15), (render.render(two), None)])
    deadline = time.time() + 4
    while time.time() < deadline:
        worker._tick()
        if len(worker.tracker.game.moves) >= 2:
            break
    C.grab = real_grab
    got = worker.tracker.game.moves
    good = got == ["e4", "e5"]
    ok = ok and good
    print("%-28s %-33s -> %-12s %s"
          % ("bot replies mid-animation", "e3 then e6 drawn in flight", got,
             "ok" if good else "WRONG, wanted ['e4', 'e5']"))

    # Show the danger is real: the same intermediate picture, believed at once.
    naive = W.BoardTracker(directory=tempfile.mkdtemp())
    naive.feed(W.START_WHITE_VIEW)
    mid = chess.Board()
    mid.push_san("e3")
    naive.feed(W.occupancy_of(mid, False))
    print("\nbelieving a single mid-slide frame records:", naive.game.moves)

    print("\nSETTLE TEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
