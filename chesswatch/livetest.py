"""End-to-end live test.

Paints real chess.com pixels onto the actual desktop, plays whole games across
them, and runs the real capture worker against the real screen. Nothing is
stubbed: the worker hunts the screen for the board on its own, grabs it through
mss, classifies the squares, infers the moves, and writes the files.

Scenario 1: full size board on the primary monitor, playing white.
Scenario 2: small board on the second monitor, playing black, ending in mate.
Scenario 3: moves skipped with no frame in between, recovered by the checker.

Run:  python livetest.py [reference-screenshot.png]
"""

import os
import sys
import glob
import time
import queue
import shutil
import ctypes

if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

import chess
import tkinter as tk
from PIL import Image, ImageTk

import watcher as W
import chesswatch as C
from fakeboard import Renderer
from shots import shot

REF_RECT = (225, 63, 824)          # board in the reference screenshot
OUT_DIR = os.path.join(W.APP_DIR, "test-games")

# A game with captures, checks and castling on both sides.
GAME = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "O-O", "Nf6", "Ng5", "O-O",
        "Nxf7", "Rxf7", "Bxf7+", "Kxf7", "Qf3+", "Kg8", "Qxf6", "Qxf6"]
MATE = ["f3", "e5", "g4", "Qh4"]


def show(root, label, image, x, y):
    photo = ImageTk.PhotoImage(image)
    label.configure(image=photo)
    label.image = photo
    label.place(x=x, y=y)
    root.update()


def run_sequence(root, label, renderer, worker, sans, flipped=False,
                 size=None, at=(0, 0)):
    """Show each position in turn and let the worker read the screen."""
    def frame(board):
        img = renderer.render(board, flipped)
        if size and size != renderer.size:
            img = img.resize((size, size), Image.LANCZOS)
        return img

    board = chess.Board()
    show(root, label, frame(board), *at)
    time.sleep(0.3)
    # Wait until it has located THIS board and started a fresh game on it. A
    # previous scenario leaves the tracker locked onto the old region, so
    # locked_on alone is not the signal.
    for _ in range(30):
        worker._tick()
        if (worker.region and tuple(worker.region[:2]) == tuple(at)
                and worker.tracker.locked_on and not worker.tracker.game.moves):
            break
        time.sleep(0.1)

    # Canonical SAN from python-chess is the oracle, not the shorthand above:
    # only the rules know whether a move gives check.
    expected = []
    for san in sans:
        move = board.parse_san(san)
        expected.append(board.san(move))
        board.push(move)
        show(root, label, frame(board), *at)
        time.sleep(0.05)
        worker._tick()
    return board, expected


def main():
    ref = sys.argv[1] if len(sys.argv) > 1 else shot("1")
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

    # Cover the whole desktop, both monitors, so nothing else on screen can
    # contaminate the run.
    left, top, width, height = C.virtual_screen()
    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry("%dx%d+%d+%d" % (width, height, left, top))
    root.attributes("-topmost", True)
    root.configure(bg="#262421")
    label = tk.Label(root, bd=0, bg="#262421")
    print("desktop %dx%d covered" % (width, height))

    q = queue.Queue()
    worker = C.Worker(None, q, directory=OUT_DIR)      # None = find it yourself

    ok = True

    print("\n--- 1: primary monitor, %dpx board, white ---" % renderer.size)
    at1 = (300, (height - renderer.size) // 2)
    _, expected = run_sequence(root, label, renderer, worker, GAME, at=at1)
    print("found board at :", worker.region)
    print("playing as     :", worker.tracker.game.my_color)
    print("recorded       :", " ".join(worker.tracker.game.moves))
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
    at2 = (1920 + 500, 300)
    print("\n--- 2: SECOND monitor, %dpx board, black, mate ---" % small)
    board, expected = run_sequence(root, label, renderer, worker, MATE,
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
    # which is what happens when the app is busy or a bot moves instantly. The
    # fast reader cannot bridge that on its own; the piece checker must.
    print("\n--- 3: SECOND monitor, three moves skipped, order forced ---")
    skip = ["e4", "e5", "Qh5"]   # Qh5 is impossible before e4, so no transposition
    board = chess.Board()
    show(root, label, renderer.render(board).resize((small, small), Image.LANCZOS),
         *at2)
    time.sleep(0.3)
    for _ in range(20):
        worker._tick()
        if worker.tracker.locked_on and not worker.tracker.game.moves:
            break
        time.sleep(0.1)
    expected = []
    for san in skip:
        move = board.parse_san(san)
        expected.append(board.san(move))
        board.push(move)
    show(root, label, renderer.render(board).resize((small, small), Image.LANCZOS),
         *at2)
    time.sleep(0.3)
    worker._tick()
    print("after the jump, fast reader has:",
          " ".join(worker.tracker.game.moves) or "(nothing)")
    worker.check_now.set()
    for _ in range(4):
        worker._tick()
        if worker.tracker.game.moves == expected:
            break
    print("checker says :", worker.tracker.last_check)
    print("recovered    :", " ".join(worker.tracker.game.moves))
    if worker.tracker.game.moves != expected:
        print("FAIL: did not recover the skipped moves, expected",
              " ".join(expected))
        ok = False

    # Flush the game in progress, which is what Stop does in the app.
    if worker.tracker.game and worker.tracker.game.moves:
        worker.tracker.game.save()

    root.destroy()

    files = sorted(glob.glob(os.path.join(OUT_DIR, "*.pgn")))
    print("\n--- files written ---")
    for path in files:
        print(" ", os.path.basename(path))
    if len(files) < 3:
        print("FAIL: expected all three games on disk")
        ok = False
    else:
        print("\n" + open(files[-1], encoding="utf-8").read().strip())

    print("\nLIVE TEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
