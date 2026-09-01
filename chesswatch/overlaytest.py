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

Both halves are done twice, once per arrow colour, because the coach answers
for whoever is to move and the violet arrow is the opponent's. Neither colour
is safe on account of the other: cyan greys to 165 and violet to 123, so each
one has to land inside the reader's band on its own, over any background, at
the window's alpha. The arithmetic for that is in overlay.py. The reason for
measuring it here anyway is that what the reader gets is not the arithmetic, it
is whatever Windows composited a layered window into.
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


def is_yours(r, g, b):
    """Cyan at 85 per cent over either square colour leaves red low with green
    and blue both high. No chess.com pixel and no piece does that."""
    return r < 90 and g > 170 and b > 170


def is_theirs(r, g, b):
    """The violet arrow is the only thing on a board that holds blue high while
    green stays low. Blue carries it, green is what tells it apart from the
    cyan arrow, and red is fenced in on both sides so a blue piece cannot creep
    through. Measured over the four fixtures at five sizes: no pixel of any
    bare board, not one violet arrow missed, and never once a cyan one."""
    return b > 190 and g < 130 and 110 < r < 215


def arrow_pixels(img, hit=is_yours):
    """Count pixels that can only be the arrow."""
    n = 0
    for r, g, b in img.convert("RGB").getdata():
        if hit(r, g, b):
            n += 1
    return n


def arrow_greys(img, hit):
    """The lowest and highest grey the reader sees where the arrow really is.
    Both have to sit inside the band it throws away."""
    body = [v for (r, g, b), v in zip(img.convert("RGB").getdata(),
                                      img.convert("L").getdata())
            if hit(r, g, b)]
    return (min(body), max(body)) if body else (None, None)


def blended(spec, bg):
    """What a fully covered pixel of an arrow of this colour comes out as over
    this background, at the window's alpha."""
    c = Image.new("RGB", (1, 1), spec).getpixel((0, 0))
    b = Image.new("RGB", (1, 1), bg).getpixel((0, 0))
    return tuple(int(round(OV.ALPHA * ci + (1 - OV.ALPHA) * bi))
                 for ci, bi in zip(c, b))


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

    print("\n-- the two colours on paper ------------------------------")
    # This is the whole safety argument, and it has to be made once per colour
    # rather than once. The reader converts to grey and counts only pixels
    # brighter than BRIGHT or darker than DARK, so an arrow colour has to land
    # between them, and it has to still land between them after the window's
    # alpha has mixed it with whatever is underneath. Blending is linear in the
    # background and black and white bracket every background there is, so
    # those two extremes are the whole range.
    for spec, who in ((OV.YOURS, "your colour "), (OV.THEIRS, "their colour")):
        grey = Image.new("RGB", (1, 1), spec).convert("L").getpixel((0, 0))
        lo = OV.ALPHA * grey
        hi = OV.ALPHA * grey + (1 - OV.ALPHA) * 255
        check("%s is invisible to the reader over any background" % who,
              W.DARK < lo and hi < W.BRIGHT, True)
        print("      %s %s greys to %3d, blends to %5.1f..%5.1f, clear of "
              "%d..%d by %.1f and %.1f"
              % (who, spec, grey, lo, hi, W.DARK, W.BRIGHT,
                 lo - W.DARK, W.BRIGHT - hi))

    check("neither colour is the transparent key",
          OV.KEY in (OV.YOURS, OV.THEIRS), False)

    # arrow_pixels is the check that stops a blank overlay passing everything
    # below it, so the two detectors must not answer for each other. Over black
    # and over white, where the arrow body is at full strength, each one has to
    # find its own colour and reject the other.
    found = [hit(*blended(spec, bg))
             for spec, hit in ((OV.YOURS, is_yours), (OV.THEIRS, is_theirs))
             for bg in ("#000000", "#FFFFFF")]
    crossed = [hit(*blended(spec, bg))
               for spec, hit in ((OV.YOURS, is_theirs), (OV.THEIRS, is_yours))
               for bg in ("#000000", "#FFFFFF")]
    check("each detector finds its own colour and rejects the other",
          (all(found), any(crossed)), (True, False))


# ------------------------------------------------------- the blend extremes

def blend_extremes(root, arrow, left, top):
    """Both colours over pure black and over pure white, read back off the
    screen.

    The arithmetic above says a colour at 85 per cent stays inside the reader's
    band over any background. What the reader actually gets is whatever Windows
    composited a layered window into, which is not required to be that
    arithmetic. So this puts each colour over each extreme and reads the greys
    back, which is the only version of the claim that counts."""
    print("\n-- both colours at both blend extremes --------------------")
    size = 320
    at = (60, 60)
    region = (at[0], at[1], size, size)
    patch = tk.Label(root, bd=0)
    ok = True
    for bg, name in (("#000000", "black"), ("#FFFFFF", "white")):
        patch.configure(bg=bg)
        patch.place(x=at[0] - left, y=at[1] - top, width=size, height=size)
        root.update()
        time.sleep(0.2)
        for mine, hit, who in ((True, is_yours, "yours "),
                               (False, is_theirs, "theirs")):
            arrow.show(region, chess.Move.from_uci("a1h8"), False, mine)
            root.update()
            time.sleep(0.2)
            lo, hi = arrow_greys(grab_at(at[0], at[1], size), hit)
            if lo is None:
                print("        %s drew nothing over %s" % (who.strip(), name))
                ok = False
                continue
            inside = W.DARK < lo and hi < W.BRIGHT
            ok = ok and inside
            print("      %s over %-5s greys %3d..%3d   %s"
                  % (who, name, lo, hi,
                     "inside" if inside else "OUTSIDE THE BAND"))
    arrow.hide()
    patch.destroy()
    root.update()
    check("neither colour leaves the blind band over black or over white",
          ok, True)


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
    check("and neither colour is on it yet",
          (arrow_pixels(plain, is_yours), arrow_pixels(plain, is_theirs)),
          (0, 0))

    arrow = OV.Arrow(root)
    check("the arrow window is click-through", arrow.click_through, True)

    blend_extremes(root, arrow, left, top)

    # Worst cases: long arrows across the crowded ranks, arrows landing on a
    # piece, and short pushes that sit entirely inside two squares.
    moves = ["a1h8", "e1e8", "d1d8", "a1a8", "h1a8", "e2e4", "g1f3", "b1c3",
             "d1h5", "c1h6", "f1a6", "e1a5", "h2h7", "b2g7", "d2d7", "a2a7"]

    def sweep(where, px_size, mine, hit, baseline):
        """Draw every move for one side, one at a time, capturing and reading
        the screen after each. Returns whether nothing changed, plus the fewest
        and the most arrow pixels seen."""
        ok = True
        drawn = []
        spec = OV.YOURS if mine else OV.THEIRS
        for uci in moves:
            arrow.show(where, chess.Move.from_uci(uci), False, mine)
            root.update()
            time.sleep(0.12)
            painted = grab_at(where[0], where[1], px_size)
            px = arrow_pixels(painted, hit)
            drawn.append(px)
            if px == 0:
                print("FAIL  nothing was drawn for %s in %s" % (uci, spec))
                ok = False
                continue
            occ = W.read_occupancy(painted)
            if occ != baseline:
                print("FAIL  %s in %s changed the reading" % (uci, spec))
                for a, b in zip(baseline, occ):
                    if a != b:
                        print("        %s -> %s" % (a, b))
                ok = False
        return ok, min(drawn), max(drawn)

    for mine, hit, who in ((True, is_yours, "your arrow"),
                           (False, is_theirs, "their arrow")):
        clean, lo, hi = sweep(region, size, mine, hit, base)
        check("%d arrows in %s, not one changed a single square"
              % (len(moves), who), clean, True)
        print("      %s covers %d to %d pixels of a %dpx board"
              % (who, lo, hi, size))

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
    for mine, hit in ((True, is_yours), (False, is_theirs)):
        spec = OV.YOURS if mine else OV.THEIRS
        for uci in ("a1h8", "e1e8", "e2e4", "g1f3"):
            arrow.show(region2, chess.Move.from_uci(uci), False, mine)
            root.update()
            time.sleep(0.12)
            got = grab_at(at[0], at[1], small)
            if arrow_pixels(got, hit) == 0:
                print("        nothing was drawn for %s in %s at %dpx"
                      % (uci, spec, small))
                ok_small = False
            elif W.read_occupancy(got) != base2:
                print("        %s in %s changed the reading at %dpx"
                      % (uci, spec, small))
                ok_small = False
    check("both colours hold on a %dpx board too" % small, ok_small, True)

    arrow.hide()
    root.update()
    time.sleep(0.2)
    gone = grab_at(at[0], at[1], small)
    check("hiding it takes every arrow pixel away",
          (arrow_pixels(gone, is_yours), arrow_pixels(gone, is_theirs)),
          (0, 0))

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
        # White is at the bottom here, so an arrow for whoever is to move now
        # is yours on white's turn and theirs on black's. One is up in every
        # frame the worker reads and the colour changes under it every half
        # move, which is the whole of what issue 9 changed.
        arrow.show(region, list(board.legal_moves)[0], False,
                   board.turn == chess.WHITE)
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
