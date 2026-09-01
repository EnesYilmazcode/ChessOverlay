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

Both halves are done twice, once per arrow colour, because the coach answers
for whoever is to move and the second colour is the opponent's.

By default the arrow is modelled: the same path from overlay.py, the same
colours, the same width, composited in PIL the way a layered window at ALPHA
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
import inspect
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


def cyan_pixels(img):
    """Count pixels that can only be the cyan arrow. Cyan at 85 per cent over
    either square colour leaves red low with green and blue both high. No
    chess.com pixel and no piece does that.

    Only used on a bare board, to show there is nothing there for a later
    reading to mistake for an arrow. Everything that measures a drawn arrow
    goes through arrow_marks instead, which needs to know no colour at all.
    """
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


def paint_arrow(board, region, move, flipped=False, mine=True):
    """What the overlay window puts on the screen, worked out in PIL instead.

    Same path, same colour, same width, blended the way a layered window at
    ALPHA blends over what is under it. It is a model of the compositor, not
    the compositor, so it settles the colour and the geometry and nothing else.
    """
    colour = OV.colour_for(mine)
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

    layer = Image.new("RGB", board.size, colour)
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
        self.mine = True

    @property
    def region(self):
        return (self.at[0], self.at[1], self.size, self.size)

    def board(self, position, size):
        self.size = size
        self.picture = self.renderer.render(position).resize((size, size),
                                                             Image.LANCZOS)
        self.move = None
        self._paint()

    def arrow(self, move, mine=True):
        self.move, self.mine = move, mine
        self._paint()

    def _paint(self):
        img = self.picture
        if self.move is not None:
            img = paint_arrow(img, self.region, self.move, mine=self.mine)
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
        time.sleep(self.screen.settle)

    def arrow(self, move, mine=True):
        self.win.show(self.region, move, False, mine)
        self.screen.root.update()
        time.sleep(self.screen.pause)

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

    # The colours are the whole safety argument, and it has to be made once per
    # colour rather than once. The reader converts to grey and counts only
    # pixels brighter than BRIGHT or darker than DARK, so each colour has to
    # land between them, and it has to still land between them after the
    # window's alpha has mixed it with whatever is underneath. The two extremes
    # bracket every board there could ever be.
    for spec, who in ((OV.YOURS, "yours "), (OV.THEIRS, "theirs")):
        grey = Image.new("RGB", (1, 1), spec).convert("L").getpixel((0, 0))
        pure = Image.new("RGB", (1, 1), spec).getpixel((0, 0))
        worst = []
        for under in ((255, 255, 255), (0, 0, 0)):
            mix = tuple(int(round(OV.ALPHA * c + (1 - OV.ALPHA) * u))
                        for c, u in zip(pure, under))
            worst.append(Image.new("RGB", (1, 1),
                                   mix).convert("L").getpixel((0, 0)))
        check("%s is invisible to the reader, blended over anything" % who,
              W.DARK < min(worst) and max(worst) < W.BRIGHT, True)
        print("      %s %s greys to %3d, blended %3d..%3d, clear of %d..%d"
              " by %d and %d"
              % (who, spec, grey, min(worst), max(worst), W.DARK, W.BRIGHT,
                 min(worst) - W.DARK, W.BRIGHT - max(worst)))


def one_mapping():
    """The modelled arrow has to measure the app's colour choice rather than a
    second copy of it. Swap the app's mapping and the model would swap with it,
    and the sweeps below would go on passing while the wrong colour reached the
    board."""
    named = [name for name, fn in (("colour_for", OV.colour_for),
                                   ("Arrow.show", OV.Arrow.show),
                                   ("Arrow._draw", OV.Arrow._draw),
                                   ("paint_arrow", paint_arrow))
             if "YOURS" in inspect.getsource(fn)
             or "THEIRS" in inspect.getsource(fn)]
    check("one place decides which colour a side gets", named, ["colour_for"])


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
    check("and there is nothing arrow-coloured on it yet",
          cyan_pixels(plain), 0)

    # Once per colour, and the captures are kept so the two runs can be
    # compared against each other afterwards.
    shots = {}
    for mine, who in ((True, "yours "), (False, "theirs")):
        clean = True
        drawn = []
        # None rather than an empty range. Seeded 255 and 0, an overlay that
        # drew nothing at all left the band check comparing DARK < 255 and
        # 0 < BRIGHT, which passes, and printed an impossible 255..0 while
        # doing it.
        lo, hi = None, None
        for uci in MOVES:
            stage.arrow(chess.Move.from_uci(uci), mine)
            painted = stage.grab()
            shots[(mine, uci)] = painted
            count, dark, bright = arrow_marks(plain, painted)
            drawn.append(count)
            if count == 0:
                print("FAIL  nothing was drawn for %s in %s" % (uci, who))
                clean = False
                continue
            lo = dark if lo is None else min(lo, dark)
            hi = bright if hi is None else max(hi, bright)
            occ = W.read_occupancy(painted)
            if occ != base:
                print("FAIL  %s in %s changed the reading" % (uci, who))
                for a, b in zip(base, occ):
                    if a != b:
                        print("        %s -> %s" % (a, b))
                clean = False
        check("%d arrows in %s, not one changed a single square"
              % (len(MOVES), who), clean, True)
        print("      %s covers %d to %d pixels of a %dpx board"
              % (who, min(drawn), max(drawn), size))
        check("  and every pixel it changed stayed between the cutoffs",
              lo is not None and W.DARK < lo and hi < W.BRIGHT, True)
        if lo is None:
            print("      nothing was ever drawn, so there was nothing to"
                  " measure")
        else:
            print("      %s lands on greys %d..%d, the reader ignores %d..%d"
                  % (who, lo, hi, W.DARK, W.BRIGHT))

    # Both sweeps above would pass on one colour drawn twice, and then
    # everything this file claims about a second colour would be worth nothing.
    # Comparing the two captures of the same move settles it without either
    # sweep having to know which colour it asked for.
    same = [uci for uci in MOVES
            if arrow_marks(shots[(True, uci)], shots[(False, uci)])[0] == 0]
    check("the same move in the two colours is not the same picture", same, [])

    # Again on a small board, where the arrow is a bigger share of what it
    # covers and a piece is only a few pixels across.
    small = 240
    stage.board(chess.Board(), small)
    plain2 = stage.grab()
    base2 = W.read_occupancy(plain2)
    ok_small = base2 == W.START_WHITE_VIEW
    if not ok_small:
        print("        the small board does not read as the start position")
    for mine, who in ((True, "yours"), (False, "theirs")):
        for uci in ("a1h8", "e1e8", "e2e4", "g1f3"):
            stage.arrow(chess.Move.from_uci(uci), mine)
            got = stage.grab()
            if arrow_marks(plain2, got)[0] == 0:
                print("        nothing was drawn for %s in %s at %dpx"
                      % (uci, who, small))
                ok_small = False
            elif W.read_occupancy(got) != base2:
                print("        %s in %s changed the reading at %dpx"
                      % (uci, who, small))
                ok_small = False
    check("both colours hold on a %dpx board too" % small, ok_small, True)

    # Only a real window can fail this. PaperStage draws the arrow by
    # compositing it onto the bare board, so taking it away is a repaint of a
    # board that never had one on it, and a hide() that did nothing at all
    # would still pass. Diffed against the bare board rather than counted by
    # colour, because the last arrow drawn above is the second colour and a
    # check that only knows the first one would pass on anything left on
    # screen.
    stage.arrow(None)
    if stage.kind == "real":
        check("hiding it leaves the board as it was",
              arrow_marks(plain2, stage.grab())[0], 0)
    else:
        print("SKIP  hiding the arrow, there is no window to hide")


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
        # single frame the worker reads. White is at the bottom here, so the
        # colour changes under the reader every half move, which is the case
        # this file exists to cover.
        stage.arrow(list(board.legal_moves)[0], board.turn == chess.WHITE)
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
        print("  proves      : the arrow's path, both colours, what they blend"
              " to over the board,")
        print("                that the reader and the recorder cannot see any"
              " of it, and")
        print("                that grab() still decodes mss bytes as BGRA")
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
    one_mapping()

    # Before any screen is opened, because PaperScreen replaces chesswatch.grab
    # and after that nothing in the run touches the real one.
    grabs = TS.grab_reads_bgra()
    if grabs is None:
        print("SKIP  grab(), chesswatch no longer exposes the mss seam")
    else:
        check("grab() still decodes mss bytes as BGRA", grabs, True)

    # Measured, never hand copied: the rectangle typed in here was one pixel out
    # for as long as this file has existed.
    ref = shot("1")
    rect = W.find_board(Image.open(ref))
    if not rect:
        print("FAIL  no board found in " + os.path.basename(ref))
        return 1
    renderer = Renderer(ref, rect)
    print("      sprites cut from %s at %s" % (os.path.basename(ref), rect))

    if on_screen:
        mons = TS.monitors()
        screen = TS.RealScreen(topmost="--topmost" in args)
        at = (mons[0][0] + 300, mons[0][1] + max(0, (mons[0][3] - 664) // 2))
        stage = RealStage(screen, renderer, at)
    else:
        screen = TS.PaperScreen(1920, 1080)
        at = (300, 200)
        stage = PaperStage(screen, renderer, at)

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
