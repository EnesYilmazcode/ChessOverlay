"""Proof that the arrow can sit on the real board without changing what the
recorder reads off it.

Run:  python overlaytest.py

This one takes over the screen. It covers the desktop, paints a real chess.com
board on it, puts the actual overlay window over that board, and then captures
the screen through mss and reads it with the real reader.

The claim has two halves, and the second is worthless without the first:

  1. The arrow really is on screen, inside the board rectangle, in front of the
     board. Every capture is searched for arrow coloured pixels and the test
     fails if there are none. Without this check, a broken overlay that drew
     nothing at all would sail through everything below.

  2. With the arrow up, all 64 squares read exactly as they do without it, and
     a whole game still records move for move.
"""

import os
import sys
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
import overlay as OV
from fakeboard import Renderer
from shots import shot

REF_RECT = (225, 63, 824)
OUT_DIR = os.path.join(W.APP_DIR, "test-games")
GAME = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "O-O", "Nf6", "d3", "d6"]

R = []


def check(name, got, want):
    ok = got == want
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        print("        got  ", got)
        print("        want ", want)
    R.append(ok)
    return ok


def arrow_pixels(img):
    """Count pixels that can only be the arrow. Cyan at 85 per cent over either
    square colour leaves red low with green and blue both high. No chess.com
    pixel and no piece does that."""
    n = 0
    for r, g, b in img.convert("RGB").getdata():
        if r < 90 and g > 170 and b > 170:
            n += 1
    return n


def grab_at(x, y, size):
    return C.grab((x, y, size, size))


# ---------------------------------------------------------------- geometry

def geometry():
    print("\n-- where a square is -------------------------------------")
    region = (100, 200, 800, 800)
    check("a1 sits bottom left", OV.square_centre(region, chess.A1, False),
          (150.0, 950.0))
    check("h8 sits top right", OV.square_centre(region, chess.H8, False),
          (850.0, 250.0))
    check("flipping puts a1 top right",
          OV.square_centre(region, chess.A1, True), (850.0, 250.0))
    check("and h8 bottom left",
          OV.square_centre(region, chess.H8, True), (150.0, 950.0))
    check("a straight move is two points",
          len(OV.path_points(region, chess.Move.from_uci("a1a8"), False)), 2)
    check("a knight turns a corner",
          OV.path_points(region, chess.Move.from_uci("g1f3"), False),
          [(750.0, 950.0), (750.0, 750.0), (650.0, 750.0)])
    check("and turns it the other way when the long leg is sideways",
          OV.path_points(region, chess.Move.from_uci("b1d2"), False),
          [(250.0, 950.0), (450.0, 950.0), (450.0, 850.0)])

    # This colour is the whole safety argument. The reader converts to grey and
    # counts only pixels brighter than BRIGHT or darker than DARK.
    grey = Image.new("RGB", (1, 1), OV.COLOUR).convert("L").getpixel((0, 0))
    check("the arrow colour is invisible to the reader",
          W.DARK < grey < W.BRIGHT, True)
    print("      arrow greys to %d, the reader ignores %d..%d"
          % (grey, W.DARK, W.BRIGHT))


# ---------------------------------------------------------------- on screen

def on_screen():
    print("\n-- on the real screen ------------------------------------")
    renderer = Renderer(shot("1"), REF_RECT)
    left, top, width, height = C.virtual_screen()

    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry("%dx%d+%d+%d" % (width, height, left, top))
    root.attributes("-topmost", True)
    root.configure(bg="#262421")
    label = tk.Label(root, bd=0, bg="#262421")

    size = 664
    at = (400, max(0, (height - size) // 2))
    board = chess.Board()

    def paint(px_size):
        img = renderer.render(board).resize((px_size, px_size), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        label.configure(image=photo)
        label.image = photo
        label.place(x=at[0] - left, y=at[1] - top)
        root.update()

    paint(size)
    time.sleep(0.3)

    region = (at[0], at[1], size, size)
    plain = grab_at(at[0], at[1], size)
    base = W.read_occupancy(plain)
    check("the board on screen reads as the start position",
          base, W.START_WHITE_VIEW)
    check("and there is no arrow on it yet", arrow_pixels(plain), 0)

    arrow = OV.Arrow(root)
    check("the arrow window is click-through", arrow.click_through, True)

    # Worst cases: long arrows across the crowded ranks, arrows landing on a
    # piece, and short pushes that sit entirely inside two squares.
    moves = ["a1h8", "e1e8", "d1d8", "a1a8", "h1a8", "e2e4", "g1f3", "b1c3",
             "d1h5", "c1h6", "f1a6", "e1a5", "h2h7", "b2g7", "d2d7", "a2a7"]
    clean = True
    drawn = []
    for uci in moves:
        arrow.show(region, chess.Move.from_uci(uci), False)
        root.update()
        time.sleep(0.12)
        painted = grab_at(at[0], at[1], size)
        px = arrow_pixels(painted)
        drawn.append(px)
        if px == 0:
            print("FAIL  nothing was drawn for " + uci)
            clean = False
            continue
        occ = W.read_occupancy(painted)
        if occ != base:
            print("FAIL  %s changed the reading" % uci)
            for a, b in zip(base, occ):
                if a != b:
                    print("        %s -> %s" % (a, b))
            clean = False
    check("%d arrows drawn, not one changed a single square" % len(moves),
          clean, True)
    print("      arrow covers %d to %d pixels of a %dpx board"
          % (min(drawn), max(drawn), size))

    # Again on a small board, where the arrow is a bigger share of what it
    # covers and a piece is only a few pixels across.
    small = 240
    arrow.hide()          # the last big arrow would otherwise sit over it
    paint(small)
    root.update()
    time.sleep(0.25)
    region2 = (at[0], at[1], small, small)
    base2 = W.read_occupancy(grab_at(at[0], at[1], small))
    ok_small = base2 == W.START_WHITE_VIEW
    if not ok_small:
        print("        the small board does not read as the start position")
    for uci in ("a1h8", "e1e8", "e2e4", "g1f3"):
        arrow.show(region2, chess.Move.from_uci(uci), False)
        root.update()
        time.sleep(0.12)
        got = grab_at(at[0], at[1], small)
        if arrow_pixels(got) == 0:
            print("        nothing was drawn for %s at %dpx" % (uci, small))
            ok_small = False
        elif W.read_occupancy(got) != base2:
            print("        %s changed the reading at %dpx" % (uci, small))
            ok_small = False
    check("holds on a %dpx board too" % small, ok_small, True)

    arrow.hide()
    root.update()
    time.sleep(0.2)
    check("hiding it takes every arrow pixel away",
          arrow_pixels(grab_at(at[0], at[1], small)), 0)

    return root, label, renderer, arrow, at, left, top


# ------------------------------------------------------- a whole game

def whole_game(root, label, renderer, arrow, at, left, top):
    print("\n-- a game recorded with the arrow up the whole time ------")
    shutil.rmtree(OUT_DIR, ignore_errors=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    size = 664
    region = (at[0], at[1], size, size)

    q = queue.Queue()
    worker = C.Worker(None, q, directory=OUT_DIR)

    def paint(b):
        img = renderer.render(b).resize((size, size), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        label.configure(image=photo)
        label.image = photo
        label.place(x=at[0] - left, y=at[1] - top)
        root.update()

    board = chess.Board()
    paint(board)
    for _ in range(30):
        worker._tick()
        if (worker.region and tuple(worker.region[:2]) == at
                and worker.tracker.locked_on and not worker.tracker.game.moves):
            break
        time.sleep(0.1)

    expected = []
    for san in GAME:
        move = board.parse_san(san)
        expected.append(board.san(move))
        board.push(move)
        paint(board)
        # An arrow for whoever is to move now, so one is on the board in every
        # single frame the worker reads.
        arrow.show(region, list(board.legal_moves)[0], False)
        root.update()
        time.sleep(0.35)
        worker._tick()

    got = worker.tracker.game.moves if worker.tracker.game else []
    check("every move recorded with the arrow up throughout", got, expected)
    check("and it still knew which colour was at the bottom",
          worker.tracker.game.my_color, "white")
    arrow.destroy()
    root.destroy()
    shutil.rmtree(OUT_DIR, ignore_errors=True)


def main():
    geometry()
    if os.environ.get("CHESSWATCH_NO_SCREEN"):
        print("\nSKIP  on-screen checks (CHESSWATCH_NO_SCREEN)")
    else:
        root, label, renderer, arrow, at, left, top = on_screen()
        whole_game(root, label, renderer, arrow, at, left, top)
    print("\n%d/%d passed" % (sum(R), len(R)))
    return 0 if all(R) else 1


if __name__ == "__main__":
    sys.exit(main())
