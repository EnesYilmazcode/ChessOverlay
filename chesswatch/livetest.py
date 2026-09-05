"""End-to-end live test.

Plays whole games across real chess.com pixels and runs the real capture worker
against them. Nothing about the worker is stubbed: it hunts for the board on its
own, grabs it, classifies the squares, infers the moves and writes the files.

By default none of that reaches a monitor. The boards are rendered into a
desktop sized image and the worker's capture is pointed at that image, so the
whole run costs no screen space at all. --on-screen paints them on the real
desktop instead, in a window the size of the board plus a margin, which is the
only way to exercise mss, DPI scaling and coordinates on a second monitor.

Scenario 1: full size board on the primary monitor, playing white.
Scenario 2: small board on the second monitor, playing black, ending in mate.
Scenario 3: moves skipped with no frame in between, recovered by the checker.

Run:  python livetest.py [reference-screenshot.png] [--on-screen] [--topmost]
"""

import os
import sys
import glob
import time
import queue
import shutil

import chess
from PIL import Image

import watcher as W
import chesswatch as C
import testscreen as TS
from fakeboard import Renderer
from shots import shot

# Where the board sits in the reference screenshot. One pixel out here and the
# renderer cuts a column of whatever is beside the board into every sprite,
# which moves the board find_board reports by a pixel and darkens the a file
# enough to fool the piece checker on a small board.
REF_RECT = (226, 63, 824)          # the board in 1.png, where selftest finds it
OUT_DIR = os.path.join(W.APP_DIR, "test-games")

# A game with captures, checks and castling on both sides.
GAME = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "O-O", "Nf6", "Ng5", "O-O",
        "Nxf7", "Rxf7", "Bxf7+", "Kxf7", "Qf3+", "Kg8", "Qxf6", "Qxf6"]
MATE = ["f3", "e5", "g4", "Qh4"]


def run_sequence(screen, renderer, worker, sans, flipped=False,
                 size=None, at=(0, 0)):
    """Show each position in turn and let the worker read the screen."""
    def frame(board):
        img = renderer.render(board, flipped)
        if size and size != renderer.size:
            img = img.resize((size, size), Image.LANCZOS)
        return img

    board = chess.Board()
    screen.show(frame(board), at)
    time.sleep(screen.pause)
    # Wait until it has located THIS board and started a fresh game on it. A
    # previous scenario leaves the tracker locked onto the old region, so
    # locked_on alone is not the signal.
    for _ in range(30):
        worker._tick()
        if (worker.region and tuple(worker.region[:2]) == tuple(at)
                and worker.tracker.locked_on and not worker.tracker.game.moves):
            break
        time.sleep(screen.pause)

    # Canonical SAN from python-chess is the oracle, not the shorthand above:
    # only the rules know whether a move gives check.
    expected = []
    for san in sans:
        move = board.parse_san(san)
        expected.append(board.san(move))
        board.push(move)
        screen.show(frame(board), at)
        time.sleep(screen.pause)
        worker._tick()
    return board, expected


def open_screen(on_screen, topmost):
    """The desktop to paint on, and the two places to paint. Returns the
    screen, the primary monitor and a second one to put scenario 2 on."""
    if on_screen:
        mons = TS.monitors()
        return TS.RealScreen(topmost=topmost), mons[0], mons[-1]
    # A pretend pair of 1920x1080 monitors side by side, so scenario 2's
    # coordinates land past the first one the way they do on a real second
    # screen. The arithmetic is the same; the hardware is not.
    return TS.PaperScreen(3840, 1080), (0, 0, 1920, 1080), (1920, 0, 1920, 1080)


def say_mode(screen, home, away):
    print("mode           :", screen.mode)
    if screen.mode == "headless":
        print("  proves      : finding the board, reading the 64 squares,"
              " inferring the moves,")
        print("                the piece checker, the PGN and JSON on disk")
        print("  proves NOT  : mss against a real display, DPI scaling, a real"
              " second monitor,")
        print("                anything another window draws over the board."
              " Use --on-screen.")
    else:
        print("  proves      : everything the headless run proves, plus the"
              " real capture path")
        print("                and absolute coordinates across monitors")
        if home == away:
            print("  proves NOT  : a second monitor. This machine has one, so"
                  " scenario 2 is")
            print("                on the primary and only the arithmetic is"
                  " being checked.")
        if not screen.topmost:
            print("  note        : the window is not topmost. If something"
                  " covers it the worker")
            print("                reads that instead, which shows up as a"
                  " board found elsewhere.")


def main():
    args = sys.argv[1:]
    on_screen = "--on-screen" in args
    topmost = "--topmost" in args
    paths = [a for a in args if not a.startswith("--")]
    ref = paths[0] if paths else shot("1")

    renderer = Renderer(ref, REF_RECT)
    print("sprites cut from", os.path.basename(ref), "| board", renderer.size, "px")

    # Prove the fixture before trusting any result that depends on it.
    start = renderer.render(chess.Board())
    score = W.grid_score(start, 0, 0, renderer.size)
    occ = W.read_occupancy(start)
    print("rendered start: grid %.2f, reads as start position: %s"
          % (score, occ == W.START_WHITE_VIEW))
    if score < 0.95 or occ != W.START_WHITE_VIEW:
        print("FAIL: the test renderer itself is wrong, results below are meaningless")
        return 1

    shutil.rmtree(OUT_DIR, ignore_errors=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    screen, home, away = open_screen(on_screen, topmost)
    say_mode(screen, home, away)

    q = queue.Queue()
    worker = C.Worker(None, q, directory=OUT_DIR)      # None = find it yourself

    ok = True
    try:
        print("\n--- 1: primary monitor, %dpx board, white ---" % renderer.size)
        at1 = (home[0] + 300, home[1] + (home[3] - renderer.size) // 2)
        _, expected = run_sequence(screen, renderer, worker, GAME, at=at1)
        print("found board at :", worker.region)
        print("playing as     :", worker.tracker.game.my_color)
        print("recorded       :", " ".join(worker.tracker.game.moves))
        print("screen space   :", screen.footprint())
        if worker.region[:2] != at1:
            print("FAIL: located the board at the wrong place, expected", at1)
            ok = False
        if worker.tracker.game.moves != expected:
            print("FAIL: moves do not match")
            print("  expected   :", " ".join(expected))
            ok = False
        if worker.tracker.game.my_color != "white":
            print("FAIL: wrong colour")
            ok = False

        small = 400
        at2 = (away[0] + 500, away[1] + 300)
        print("\n--- 2: second monitor, %dpx board, black, mate ---" % small)
        board, expected = run_sequence(screen, renderer, worker, MATE,
                                       flipped=True, size=small, at=at2)
        game2 = worker.tracker.game
        print("found board at :", worker.region)
        print("playing as     :", game2.my_color)
        print("recorded       :", " ".join(game2.moves))
        print("result         :", game2.result, game2.termination, "->", game2.won)
        if worker.region[:2] != at2 or worker.region[2] != small:
            print("FAIL: second monitor board located wrong, expected", at2, small)
            ok = False
        if game2.moves != expected:
            print("FAIL: mate sequence wrong, expected", " ".join(expected))
            ok = False
        if game2.my_color != "black" or game2.result != "0-1":
            print("FAIL: colour or result wrong")
            ok = False
        if not board.is_checkmate():
            print("FAIL: test sequence is not actually mate")
            ok = False

        # 3: the board jumps several moves with no frames captured in between,
        # which is what happens when the app is busy or a bot moves instantly.
        # Either the fast reader bridges the gap by search or the piece checker
        # does, and the run says which; what matters is that the three moves
        # come out in the only order that is legal.
        print("\n--- 3: second monitor, three moves skipped, order forced ---")
        skip = ["e4", "e5", "Qh5"]   # Qh5 is impossible before e4, so no transposition
        board = chess.Board()
        screen.show(renderer.render(board).resize((small, small), Image.LANCZOS),
                    at2)
        time.sleep(screen.pause)
        for _ in range(20):
            worker._tick()
            if worker.tracker.locked_on and not worker.tracker.game.moves:
                break
            time.sleep(screen.pause)
        expected = []
        for san in skip:
            move = board.parse_san(san)
            expected.append(board.san(move))
            board.push(move)
        screen.show(renderer.render(board).resize((small, small), Image.LANCZOS),
                    at2)
        time.sleep(screen.pause)
        worker._tick()
        bridged = worker.tracker.game.moves == expected
        print("after the jump, fast reader has:",
              " ".join(worker.tracker.game.moves) or "(nothing)")
        worker.check_now.set()
        for _ in range(4):
            worker._tick()
            if worker.tracker.game.moves == expected:
                break
        print("checker says :", worker.tracker.last_check)
        print("recovered    :", " ".join(worker.tracker.game.moves))
        print("bridged by   :", "the fast reader" if bridged else "the checker")
        if worker.tracker.game.moves != expected:
            print("FAIL: did not recover the skipped moves, expected",
                  " ".join(expected))
            ok = False

        # Flush the game in progress, which is what Stop does in the app.
        if worker.tracker.game and worker.tracker.game.moves:
            worker.tracker.game.save()
    finally:
        screen.close()

    files = sorted(glob.glob(os.path.join(OUT_DIR, "*.pgn")))
    print("\n--- files written ---")
    for path in files:
        print(" ", os.path.basename(path))
    if len(files) < 3:
        print("FAIL: expected all three games on disk")
        ok = False
    else:
        print("\n" + open(files[-1], encoding="utf-8").read().strip())

    print("\nscreen space :", screen.footprint())
    print("LIVE TEST:", "PASS" if ok else "FAIL", "(%s)" % screen.mode)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
