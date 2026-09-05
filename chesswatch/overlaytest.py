"""Proof that the arrow can sit on the real board without changing what the
recorder reads off it.

Run:  python overlaytest.py [--on-screen] [--topmost]

The claim has two halves, and the second is worthless without the first:

  1. The arrow really is there, inside the board rectangle, in front of the
     board. Every capture is diffed against the bare board and the test fails
     if nothing changed. Without this check, a broken overlay that drew nothing
     at all would sail through everything below.

  2. With the arrow up, all 64 squares read exactly as they do without it, and
     a whole game still records move for move.

By default the arrow is modelled: the same path from overlay.py, the same
colour, the same width, composited in PIL the way a layered window at ALPHA
composites. That costs no screen space and it does prove the part of the claim
that is arithmetic, which is the colour, the geometry and what the reader makes
of them. It cannot prove that Windows really paints those pixels.

--on-screen does. It opens a window the size of the board plus a margin, puts
the actual overlay over it, captures it through mss and reads that back. That is
the only run that touches the transparency key, the layered window alpha, the
stacking order and click-through.
"""

import os
import sys
import math
import time
import queue
import shutil

import chess
from PIL import Image, ImageChops, ImageDraw, ImageStat

import watcher as W
import chesswatch as C
import overlay as OV
import testscreen as TS
from fakeboard import Renderer
from shots import shot

REF_RECT = (226, 63, 824)          # the board in 1.png, where selftest finds it
OUT_DIR = os.path.join(W.APP_DIR, "test-games")
GAME = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "O-O", "Nf6", "d3", "d6"]

# Worst cases: long arrows across the crowded ranks, arrows landing on a piece,
# and short pushes that sit entirely inside two squares.
MOVES = ["a1h8", "e1e8", "d1d8", "a1a8", "h1a8", "e2e4", "g1f3", "b1c3",
         "d1h5", "c1h6", "f1a6", "e1a5", "h2h7", "b2g7", "d2d7", "a2a7"]

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
    r, g, b = img.convert("RGB").split()
    mask = ImageChops.multiply(
        ImageChops.multiply(r.point(lambda v: 255 * (v < 90)),
                            g.point(lambda v: 255 * (v > 170))),
        b.point(lambda v: 255 * (v > 170)))
    return mask.histogram()[255]


def arrow_marks(plain, painted):
    """Every pixel the arrow changed, and the grey levels it left them at.

    Differencing rather than hunting for a known colour is what makes this
    measure the claim instead of assuming it. An arrow drawn in some other
    colour still shows up here, where a colour hunt would report that nothing
    was drawn at all. The mechanism behind every check in this file is that
    read_occupancy counts only greys above BRIGHT or below DARK, so all of
    these have to land in between.

    Returns (count, darkest, brightest), and (0, 255, 0) for no change.
    """
    channels = ImageChops.difference(plain.convert("RGB"),
                                     painted.convert("RGB")).split()
    changed = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]),
                                 channels[2]).point(lambda v: 255 * (v > 8))
    n = changed.histogram()[255]
    if not n:
        return 0, 255, 0
    lo, hi = ImageStat.Stat(painted.convert("L"), changed).extrema[0]
    return n, lo, hi


# ------------------------------------------------------- the modelled arrow

def _head(pts, step):
    """The arrowhead Tk draws for arrowshape=(0.42, 0.52, 0.30) of a square:
    a tip, two back corners d3 either side of the line, and the barb where the
    trailing edge crosses it."""
    (x0, y0), (x1, y1) = pts[-2], pts[-1]
    dx, dy = x1 - x0, y1 - y0
    run = math.hypot(dx, dy) or 1.0
    ux, uy = dx / run, dy / run
    px, py = -uy, ux
    d1, d2, d3 = step * 0.42, step * 0.52, step * 0.30
    return [(x1, y1),
            (x1 - d2 * ux + d3 * px, y1 - d2 * uy + d3 * py),
            (x1 - d1 * ux, y1 - d1 * uy),
            (x1 - d2 * ux - d3 * px, y1 - d2 * uy - d3 * py)]


def paint_arrow(board, region, move, flipped=False):
    """What the overlay window puts on the screen, worked out in PIL instead.

    Same path, same colour, same width, blended the way a layered window at
    ALPHA blends over what is under it. It is a model of the compositor, not
    the compositor, so it settles the colour and the geometry and nothing else.
    """
    x, y, size = region[0], region[1], region[2]
    step = size / 8.0
    pts = [(px - x, py - y)
           for px, py in OV.path_points(region, move, flipped)]
    width = max(2, int(round(step * 0.16)))

    mask = Image.new("L", board.size, 0)
    pen = ImageDraw.Draw(mask)
    pen.line(pts, fill=255, width=width, joint="curve")
    # Tk rounds the ends and the corners of the shaft. PIL does not, so the
    # corners are filled in by hand, which is the wider of the two shapes.
    radius = width / 2.0
    for px, py in pts:
        pen.ellipse([px - radius, py - radius, px + radius, py + radius], fill=255)
    pen.polygon(_head(pts, step), fill=255)

    layer = Image.new("RGB", board.size, OV.COLOUR)
    return Image.composite(layer, board,
                           mask.point(lambda v: int(v * OV.ALPHA)))


# ------------------------------------------------------------------ stages

class PaperStage:
    """Board and arrow composited in PIL and handed to a screen made of paper."""

    kind = "modelled"
    click_through = None            # nothing to be click-through here

    def __init__(self, screen, renderer, at):
        self.screen, self.renderer, self.at = screen, renderer, at
        self.size = None
        self.picture = None
        self.move = None

    @property
    def region(self):
        return (self.at[0], self.at[1], self.size, self.size)

    def board(self, position, size):
        self.size = size
        self.picture = self.renderer.render(position).resize((size, size),
                                                             Image.LANCZOS)
        self.move = None
        self._paint()

    def arrow(self, move):
        self.move = move
        self._paint()

    def _paint(self):
        img = self.picture
        if self.move is not None:
            img = paint_arrow(img, self.region, self.move)
        self.screen.show(img, self.at)

    def grab(self):
        return C.grab(self.region)

    def close(self):
        pass


class RealStage:
    """The real overlay window over a real board on a real screen."""

    kind = "real"

    def __init__(self, screen, renderer, at):
        self.screen, self.renderer, self.at = screen, renderer, at
        self.size = None
        self.win = OV.Arrow(screen.root)
        self.click_through = self.win.click_through

    @property
    def region(self):
        return (self.at[0], self.at[1], self.size, self.size)

    def board(self, position, size):
        self.size = size
        self.win.hide()             # the arrow up now was drawn for the old size
        self.screen.show(self.renderer.render(position).resize((size, size),
                                                               Image.LANCZOS),
                         self.at)
        time.sleep(self.screen.pause * 4)

    def arrow(self, move):
        self.win.show(self.region, move)
        self.screen.root.update()
        time.sleep(self.screen.pause * 2)

    def grab(self):
        return C.grab(self.region)

    def close(self):
        self.win.destroy()


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

    # The window is 85 per cent opaque, so what lands on screen is the arrow
    # blended with whatever is under it. The two extremes bracket every board
    # there could ever be.
    pure = Image.new("RGB", (1, 1), OV.COLOUR).getpixel((0, 0))
    worst = []
    for under in ((255, 255, 255), (0, 0, 0)):
        mix = tuple(int(round(OV.ALPHA * c + (1 - OV.ALPHA) * u))
                    for c, u in zip(pure, under))
        worst.append(Image.new("RGB", (1, 1), mix).convert("L").getpixel((0, 0)))
    check("and stays invisible blended over pure white or pure black",
          all(W.DARK < g < W.BRIGHT for g in worst), True)
    print("      blended it greys to %d over black and %d over white"
          % (min(worst), max(worst)))


# ------------------------------------------------------------ arrows on a board

def arrow_checks(stage):
    print("\n-- %d arrows over a board (%s arrow) -------------------"
          % (len(MOVES), stage.kind))
    if stage.click_through is None:
        print("SKIP  click-through, there is no window in this mode")
    elif sys.platform != "win32":
        print("SKIP  click-through, it is a Windows window style")
    else:
        check("the arrow window is click-through", stage.click_through, True)

    size = 664
    stage.board(chess.Board(), size)
    plain = stage.grab()
    base = W.read_occupancy(plain)
    check("the board reads as the start position", base, W.START_WHITE_VIEW)
    check("and there is no arrow on it yet", arrow_pixels(plain), 0)

    clean = True
    drawn = []
    lo, hi = 255, 0
    for uci in MOVES:
        stage.arrow(chess.Move.from_uci(uci))
        painted = stage.grab()
        count, dark, bright = arrow_marks(plain, painted)
        drawn.append(count)
        if count == 0:
            print("FAIL  nothing was drawn for " + uci)
            clean = False
            continue
        lo, hi = min(lo, dark), max(hi, bright)
        occ = W.read_occupancy(painted)
        if occ != base:
            print("FAIL  %s changed the reading" % uci)
            for a, b in zip(base, occ):
                if a != b:
                    print("        %s -> %s" % (a, b))
            clean = False
    check("%d arrows drawn, not one changed a single square" % len(MOVES),
          clean, True)
    print("      arrow covers %d to %d pixels of a %dpx board"
          % (min(drawn), max(drawn), size))
    check("and every pixel it changed stayed between the reader's cutoffs",
          W.DARK < lo and hi < W.BRIGHT, True)
    print("      they land on greys %d..%d, the reader ignores %d..%d"
          % (lo, hi, W.DARK, W.BRIGHT))

    # Again on a small board, where the arrow is a bigger share of what it
    # covers and a piece is only a few pixels across.
    small = 240
    stage.board(chess.Board(), small)
    plain2 = stage.grab()
    base2 = W.read_occupancy(plain2)
    ok_small = base2 == W.START_WHITE_VIEW
    if not ok_small:
        print("        the small board does not read as the start position")
    for uci in ("a1h8", "e1e8", "e2e4", "g1f3"):
        stage.arrow(chess.Move.from_uci(uci))
        got = stage.grab()
        if arrow_marks(plain2, got)[0] == 0:
            print("        nothing was drawn for %s at %dpx" % (uci, small))
            ok_small = False
        elif W.read_occupancy(got) != base2:
            print("        %s changed the reading at %dpx" % (uci, small))
            ok_small = False
    check("holds on a %dpx board too" % small, ok_small, True)

    stage.arrow(None)
    check("hiding it takes every arrow pixel away", arrow_pixels(stage.grab()), 0)


# ------------------------------------------------------- a whole game

def whole_game(stage):
    print("\n-- a game recorded with the arrow up the whole time ------")
    shutil.rmtree(OUT_DIR, ignore_errors=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    worker = C.Worker(None, queue.Queue(), directory=OUT_DIR)
    board = chess.Board()
    stage.board(board, 664)
    for _ in range(30):
        worker._tick()
        if (worker.region and tuple(worker.region[:2]) == tuple(stage.at)
                and worker.tracker.locked_on and not worker.tracker.game.moves):
            break
        time.sleep(stage.screen.pause)

    expected = []
    for san in GAME:
        move = board.parse_san(san)
        expected.append(board.san(move))
        board.push(move)
        stage.board(board, 664)
        # An arrow for whoever is to move now, so one is on the board in every
        # single frame the worker reads.
        stage.arrow(list(board.legal_moves)[0])
        worker._tick()

    got = worker.tracker.game.moves if worker.tracker.game else []
    check("every move recorded with the arrow up throughout", got, expected)
    check("and it still knew which colour was at the bottom",
          worker.tracker.game.my_color, "white")
    shutil.rmtree(OUT_DIR, ignore_errors=True)


def say_mode(screen, stage):
    print("\nmode           : %s, %s arrow" % (screen.mode, stage.kind))
    print("screen space   :", screen.footprint())
    if screen.mode == "headless":
        print("  proves      : the arrow's path, its colour, what it blends to"
              " over the board,")
        print("                and that the reader and the recorder cannot see"
              " any of it")
        print("  proves NOT  : that Windows paints those pixels. The"
              " transparency key, the")
        print("                layered window alpha, the stacking order and"
              " click-through are")
        print("                only exercised by --on-screen.")
    else:
        print("  proves      : all of that against pixels that really went"
              " through the")
        print("                compositor and came back out of mss,"
              " click-through included")


def main():
    args = sys.argv[1:]
    on_screen = "--on-screen" in args

    geometry()

    if on_screen:
        mons = TS.monitors()
        screen = TS.RealScreen(topmost="--topmost" in args)
        at = (mons[0][0] + 300, mons[0][1] + max(0, (mons[0][3] - 664) // 2))
        stage = RealStage(screen, Renderer(shot("1"), REF_RECT), at)
    else:
        screen = TS.PaperScreen(1920, 1080)
        at = (300, 200)
        stage = PaperStage(screen, Renderer(shot("1"), REF_RECT), at)

    try:
        arrow_checks(stage)
        whole_game(stage)
        say_mode(screen, stage)
    finally:
        stage.close()
        screen.close()

    print("\n%d/%d passed" % (sum(R), len(R)))
    return 0 if all(R) else 1


if __name__ == "__main__":
    sys.exit(main())
