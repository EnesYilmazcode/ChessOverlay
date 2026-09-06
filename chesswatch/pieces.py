"""Identify which piece is on each square, from pixels alone.

The occupancy reader in watcher.py only tells white from black, which is enough
to follow a game move by move but not enough to read a position cold. This adds
the missing half: shape matching against piece templates.

A square is reduced to a small grid of grey levels, zero-meaned and divided by
its own spread, and matched to each template by correlation. Every picture of
the same thing under `a*x + b` then scores identically, so a screen turned up,
a capture that came out dim and a set that draws its white body grey are all
the same square to this. The reader this replaced spent five measured constants
and a per-board threshold pair trying to reach that by construction and did not:
contrast alone cost it 8.54% wrong squares.

Nothing is thresholded, so there is no cutoff to defend and no board theme to
be cut into by. The board is measured for two things only: its two square
colours, which say how much contrast a square with something drawn on it ought
to show, and the four corners of each square, which is where a board prints its
rank and file labels and where no piece ever reaches.

Deciding whether a square is empty is a separate measurement from deciding what
is on it, and deliberately the plainest one available: how far apart the
brightest and the darkest pixel in the middle of the square are. A piece that
loses its outline to a bad capture still moves that number; the last-move wash,
the check marker and the coordinate labels do not. Sharing thresholds between
the two questions is what used to make 46% of all wrong answers a piece read as
an empty square.

Templates come from `pieces.png` to start with, are relearned from your own
screen the moment a position we are sure of appears, and are cached next to it
so the next game starts where the last one left off. Each learned piece is kept
as several renderings: as it was cut, moved onto the other square colour where
the position never showed it there, drawn with a heavier and a lighter outline,
and reduced from several window sizes. learn() sees one size and one square
colour per piece and is then asked about every size a browser window can be,
and neither of those differences is one that normalising removes.

The awkward part is that this program paints its own coach arrow on the board it
is reading. overlay.py keeps that invisible to the occupancy reader by choosing
a brightness it ignores, which is a fact about cutoffs and buys a matcher with
no cutoffs nothing at all. The arrow is recognised instead: the overlay window
is layered at a known alpha, so a painted pixel is `a*C + (1-a)*u` for a known
colour C, and membership of that set is one interval per channel. Every cell the
arrow reaches is then weighted by how much of it survived, so a covered cell
contributes nothing rather than something invented. Filling the hole in instead
looks like it works and does not: the wrong answers score higher than the right
ones, because the score is then grading the reconstruction. See
_weighted_scores.

The reader this replaced also measured the blur on a capture and put it back
with a matched unsharp mask, which is gone. It was measured before it was
deleted rather than after: on every capture row piecetest builds it is worth
nothing at all now, since a board blurred by four pixels reads with nothing
wrong on it either way, and keeping it cost 15.6 points of whole boards on
bench.py's quick corpus and 21 ms of every soft capture. What it existed to
stop was a white piece read as an empty square, and the emptiness test above is
what stops that now.
"""

import base64
import hashlib
import json
import math
import operator
import os
import zlib

from PIL import Image, ImageChops, ImageFilter

APP_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_SHEET = os.path.join(APP_DIR, "pieces.png")
CACHE_DIR = os.path.join(APP_DIR, "learned")

ORDER = "KQRBNPkqrbnp"
TEMPLATE_PX = 96          # size each template is stored at

# The two sheet widths. PLAIN_SLOTS is one template per piece, which is what
# pieces.png ships as and what make_templates.py writes, and it says nothing
# about which square colour each piece was standing on. PAIRED_SLOTS is those
# twelve as they look on a light square followed by the same twelve on a dark
# one, which is what learn() keeps in memory and what enroll.py writes. See
# _read_sheet.
PLAIN_SLOTS = len(ORDER)
PAIRED_SLOTS = 2 * len(ORDER)

# The square is reduced to RES by RES grey levels before anything is compared.
# A wide plateau rather than a tuned number: 12 through 20 land within a tenth
# of a point of each other and it falls away above 24. Three earlier
# measurements disagreed about the right resolution and all three were reading
# an occupancy-threshold artifact rather than a resolution curve, so this is
# not a number to tune again.
RES = 16

# Area-averaging and not point sampling. A one pixel outline has to reach the
# reduced square at every board size or it drops out of half of them.
RESAMPLE = Image.BOX

# How far inside its square the crop sits, as a fraction of the square. A
# blurred neighbour bleeds across the boundary and a board draws gridlines on
# it; neither belongs to this piece.
INSET = 0.12

# How much of the square is looked at, as a radius from its centre in units of
# half a square. sqrt(2) keeps the whole square and 1.0 is the inscribed
# circle. The corners are where chess.com prints the rank down the a file and
# the file letters along rank 1, and are the one part of a square a piece never
# reaches, so they are dropped from the template and the crop alike.
CORNERS = 0.90

# Every piece is ranked on a COARSE by COARSE reduction first and only the best
# KEEP piece types are settled at full resolution. Speed bought for nothing:
# with the shortlist off the bench scores identically and takes half again as
# long.
COARSE = 8
KEEP = 3

# What a call has to clear. FLOOR is on the correlation itself, which is what
# refuses a square holding a piece from a set none of these templates describes;
# MARGIN is the gap to the best of any OTHER piece type, which is what refuses a
# square the pixels do not settle between two shapes.
#
# Over piece types and not over the twelve symbols, for the same reason as
# before: a white knight beating a black knight is one shape scored twice, not
# two things to be uncertain between.
FLOOR = 0.68
MARGIN = 0.02

# How much of a square the emptiness test looks at. The middle only: a square's
# own boundary is a step between two board colours, blur widens it, and the
# outer band would read as contrast on a square with nothing drawn on it.
#
# Half, because the corners of a real square are not empty. At 0.62 the rank
# and file glyphs reach into the window: over 690 empty squares of the three
# reference screenshots blurred at four sizes, 104 come up occupied, all on the
# a file, the first rank, the last-move highlight or the check marker. At 0.50
# none of them do. It costs nothing on the pieces, which are drawn inside 0.78
# of the square.
OCCUPIED_INSIDE = 0.50

# And how far apart the brightest and the darkest pixel in there have to be, as
# a fraction of the gap between the two square colours, before the square is
# holding something.
#
# A gap and not a threshold inside a distribution. The two mistakes are not the
# same size either: too low costs an unknown on an empty square, which the
# caller retries a second later, and too high reads a piece as an empty square,
# which is a wrong answer in a permanent game record. So it sits an order of
# magnitude under the occupied floor rather than midway. piecetest measures both
# ends of the gap on every capture it builds.
OCCUPIED_F = 0.10

# How near a pixel has to be to the colour of the square it is standing on
# before _transplant treats it as ground rather than as piece, as a fraction of
# the gap between the two square colours.
#
# It has to clear the noise on a flat square and stay under the nearest level
# any piece is drawn at. Measured over both fixtures, an empty square holds its
# own colour to within 0.020 of the span while the closest a piece comes to the
# square under it is 0.167, which is chess.com's white pawn on a light square:
# a body at 250 over a square at 233. The old formula put this at 0.18 and ate
# that pawn's body, so a white pawn moved onto a dark square became a rook and
# 12 squares of the misaligned crops read as one.
GROUND_TOL_F = 0.08

# How far outside the two square colours a flat square's own colour may sit
# and still be an empty square, as a fraction of the gap between them.
#
# Reaching the emptiness test is not enough on its own. A square something has
# painted over edge to edge shows no contrast either, and answering "." there
# puts a piece that is still standing into the game record. So an empty square
# also has to be flat at a colour this board paints squares with.
#
# The gap is enormous and that is why this is worth having. Over 1572 empty
# squares of both fixtures and four rendered sets, at four window sizes,
# blurred, contrast-shifted, with the last-move wash and with the rank and file
# labels drawn, not one has a colour outside the two the board is painted in at
# all: the worst reads 0.000. A square painted over with a popup's own grey
# reads 0.92. The wash passes because it is what the site draws, a tint of the
# square colour that lands between the two rather than outside them.
EMPTY_GROUND_F = 0.15

# What counts as ink either side of a square's own ground level, as a fraction
# of the gap between the two square colours. Only ever read to describe a piece
# set, never to identify a piece: see _signature. Anchored on the two square
# colours, which are stable and always present, rather than on the extremes the
# ink reaches, which are whatever the capture did to the picture.
SHARE_F = 0.25

# Extra renderings of each learned piece, so the bank covers a set that draws
# its outline heavier or lighter than the one it was taught from. A closing
# swallows a hairline dark edge and an opening eats a thin light body, which is
# most of what one piece set does to another.
AUGMENT = ("thin", "heavy")

# The square size those renderings are made at. A three pixel morphological
# filter is a different thing on a 35 pixel square than on a 103 pixel one, so
# it is applied at a stated size rather than at whatever size the learning
# board happened to be. At 96 a three pixel filter is half of one cell of the
# reduction and makes a copy rather than a variation; at 24 it is coarse enough
# to blur two pieces into each other.
AUGMENT_PX = 40

# And the square sizes each piece is taught at. learn() sees one window size
# and the reader is then asked about every size a browser can be, and a small
# board is not a big one scaled: resampling takes a hairline outline away
# rather than thinning it. Sizes at or above the size the piece was cut at are
# skipped, because upsampling a crop teaches nothing that was not already in
# it. See _renderings for what this costs and buys.
LEARN_PX = (48, 30)

# Templates learned on a board smaller than this were upsampled into the RES
# grid at birth and stay coarse however big the board later gets. RES * 8 is
# the board width at which one square holds exactly as many screen pixels as
# the grid it is compared on. See stale().
MIN_LEARN_PX = RES * 8

# Templates carry the signature of the board they came from. When the board
# being read has a different one the templates are somebody else's piece set,
# and a call that is only the best of a bad field is a guess, so the floor
# rises from FLOOR to this. See _trusted for what the signature is.
TRUST_TOL = 0.25
MISTRUST_FLOOR = 0.75

# Outline thickness cannot be compared between two captures whose squares
# differ by more than this. The smaller one has resampled the outline away
# rather than been drawn without one, and reads as a different set when it is
# the same one photographed worse.
TRUST_SCALE = 2.0

# Confirming one named piece rather than choosing between twelve. A question a
# pointer or a popup does not spoil, where _judge's is. See _contains.
#
# The margin is what refuses a square something has covered outright, and it is
# what stops a piece the square is not holding being confirmed at all. Over the
# 2419 (square, belief) pairs piecetest builds, where every square an
# obstruction cost the reader is offered all twelve wrong beliefs as well as
# the right one, the closest a wrong belief comes to beating the field is
# 0.0465, so 0.08 clears the worst of them by 0.0335 and 0.05 does not. Above
# 0.08 it starts costing: 0.10 gives up two of the four squares a belief puts
# back and buys nothing measured.
#
# The floor fired on none of it. It is kept for what those captures do not
# contain: on the margin alone a square holding almost nothing confirms as long
# as nothing else is there either, a believed piece at 0.2 against rivals at
# zero reading the same as one at 0.9 against 0.5. piecetest pins the distance
# between the two numbers so the floor cannot quietly be raised into the
# answers.
CONFIRM_CONTAIN = 0.60
CONFIRM_MARGIN = 0.08

# chess.com's green board converted to grey, its two square colours. Only a
# bootstrap: every read measures the board in front of it.
DEFAULT_LEVELS = (131, 233)

# The board's own levels are measured this far in from its edge. A crop that
# find_board got wrong by two pixels drags a column in from outside the
# picture. Nothing of a piece lives in the outer fiftieth of a board, since
# pieces sit centred in their squares.
LEVEL_INSET = 0.02

# Where each piece is on a board, shared with the occupancy reader.
from watcher import grid_of


# ----------------------------------------------------------- the board itself

def _levels(board_img, floor=0.04, apart=24):
    """The board's two square colours, darker first.

    A board is mostly board even with every piece on it, so the square colours
    are the two commonest grey levels that are far enough apart to be two
    colours rather than one colour and its antialiasing. A level has to hold a
    twenty-fifth of the board to be one at all, which no piece fill on any
    fixture does, and 24 apart is comfortably under the 102 that separates
    chess.com's own two and comfortably over the spread of one antialiased edge.

    Measured off the whole board and never off a single square, because one
    square's commonest level is as often the piece as the board under it: the
    white king's square on 1.png peaks at 249, which is its own fill.
    """
    wide, high = board_img.size
    inset = board_img.crop((int(wide * LEVEL_INSET), int(high * LEVEL_INSET),
                            int(wide * (1 - LEVEL_INSET)),
                            int(high * (1 - LEVEL_INSET))))
    hist = inset.resize((128, 128), Image.NEAREST).convert("L").histogram()
    total = sum(hist)
    found = []
    for level in sorted(range(256), key=lambda v: -hist[v]):
        if hist[level] < total * floor:
            break
        if all(abs(level - seen) >= apart for seen in found):
            found.append(level)
        if len(found) == 2:
            break
    if not found:
        return DEFAULT_LEVELS
    if len(found) == 1:
        found.append(found[0])
    return min(found), max(found)


def _span(levels):
    """How much contrast a square of this board is drawn with."""
    return max(1, levels[1] - levels[0])


# ------------------------------------------------------------- the reduction

def _inside(res, radius):
    """Which cells of a res by res reduction are inside the radius, or None
    when the radius keeps the whole square and the fast path is a plain list."""
    if radius is None or radius >= 1.4143:
        return None
    out = []
    for r in range(res):
        for c in range(res):
            dy = (2.0 * r + 1.0) / res - 1.0
            dx = (2.0 * c + 1.0) / res - 1.0
            if dx * dx + dy * dy <= radius * radius:
                out.append(r * res + c)
    return out


_IDX = _inside(RES, CORNERS)
_CIDX = _inside(COARSE, CORNERS)
_CELLS = len(_IDX) if _IDX else RES * RES


def _reduce(img, box, res, idx):
    """One rectangle of an image as a flat list of grey levels.

    Cropping and reducing in one call, with the box in floats, so a board whose
    squares do not land on whole pixels is cut where the squares actually are
    rather than where int() puts them.
    """
    out = list(img.resize((res, res), RESAMPLE, box).getdata())
    return [out[i] for i in idx] if idx else out


def _stats(v):
    """(mean, length of the zero-mean vector). The second over root n is the
    spread, and it is what the correlation divides by."""
    n = len(v)
    mean = sum(v) / n
    return mean, math.sqrt(sum((x - mean) ** 2 for x in v))


def _unit(v):
    """A vector as the dot product wants it: zero mean, unit length.

    None for a flat vector, which has no direction to compare. Storing
    templates this way is what makes the plain match one raw dot product: the
    template sums to zero, so subtracting the square's own mean changes
    nothing, and the square's own length is the same divisor for every
    candidate and comes out of the ranking altogether.
    """
    mean, norm = _stats(v)
    if norm < 1e-9:
        return None
    return [(x - mean) / norm for x in v]


def _moments(v, w):
    """(sum of weights, weighted sum of v, weighted variance of v). What a
    partly covered square's spread and correlation are both measured on."""
    sw = sum(w)
    if sw <= 1e-9:
        return 0.0, 0.0, 0.0
    swv = sum(map(operator.mul, w, v))
    swv2 = sum(a * b * b for a, b in zip(w, v))
    return sw, swv, swv2 / sw - (swv / sw) ** 2


def _square_box(size, row, col):
    """Where one square of an eight by eight board of this width sits."""
    step = size / 8.0
    return (int(col * step), int(row * step),
            int((col + 1) * step), int((row + 1) * step))


def _crop_boxes(size, inset=INSET):
    """The 64 crop rectangles in screen order, in floats."""
    step = size / 8.0
    pad = inset * step
    return [(c * step + pad, r * step + pad,
             (c + 1) * step - pad, (r + 1) * step - pad)
            for r in range(8) for c in range(8)]


def squares(board_img, size=None):
    """Yield (row, col, square image), row 0 being the top of the screen."""
    size = size or board_img.size[0]
    for r in range(8):
        for c in range(8):
            yield r, c, board_img.crop(_square_box(size, r, c))


# ---------------------------------------------- the program's own coach arrow

def _arrow_paint():
    """The colours the arrow is painted in and how solidly, read off overlay
    rather than copied into here, so a third colour cannot be added there
    without this seeing it.

    overlay is a GUI module. Importing it defines constants and functions and
    opens nothing, but a machine with no tkinter at all should still be able to
    read a board, so the fallback repeats the two colours and piecetest checks
    that the repeat still agrees with overlay.
    """
    try:
        import overlay as OV
        names, alpha = (OV.YOURS, OV.THEIRS), OV.ALPHA
    except Exception:
        names, alpha = ("#00E8FF", "#A64BFF"), 0.85
    out = []
    for name in names:
        h = name.lstrip("#")
        out.append(tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)))
    return tuple(out), alpha


ARROW_COLOURS, ARROW_ALPHA = _arrow_paint()

# Slack on the per-channel boxes below, in levels, for a screen that does not
# hand back exactly the colour that was painted. There is a lot of room: over
# four board themes and both real piece sets, the closest pixel any of them
# draws to either box sits 52 levels outside it. Widening this can cost a
# refusal and cannot cost a wrong answer, because a masked pixel is one the
# reader no longer votes on rather than one it invents.
ARROW_TOL = 8.0

# How much of a square may be under the arrow before the square is refused out
# of hand. A square the arrow covers outright has no correlation to be had, and
# the flatness left behind would otherwise read as an empty square.
ARROW_REFUSE = 0.75

# What a square with the arrow across it has to clear before it is believed.
# Higher than FLOOR and MARGIN on purpose: the square is being read off less
# evidence than a clear one, so it should be harder to convince.
ARROW_FLOOR = 0.85
ARROW_MARGIN = 0.05


def _arrow_mask(rgb, colours=ARROW_COLOURS, alpha=ARROW_ALPHA, tol=ARROW_TOL):
    """255 where a pixel could have been painted by the program's own arrow.

    The overlay window is layered, so a drawn pixel on a real screen is alpha
    of the arrow colour over whatever was underneath, and the set of pixels the
    arrow can produce is

        { a*C + (1-a)*u : a in [ALPHA, 1], u a colour }

    Membership collapses to one interval per channel. p = a*C + (1-a)*u with u
    in [0, 255] forces a <= p/C and a <= (255-p)/(255-C), both upper bounds, so
    the whole test is that every channel satisfies

        ALPHA*C <= p <= 255 - ALPHA*(255 - C)

    One lookup table per channel per colour and two whole-image boolean ops,
    all of it inside the imaging library, because this runs on every frame.
    """
    bands = rgb.split()
    found = None
    for colour in colours:
        hit = None
        for band, level in zip(bands, colour):
            lo = alpha * level - tol
            hi = 255 - alpha * (255 - level) + tol
            here = band.point([255 if lo <= v <= hi else 0 for v in range(256)])
            hit = here if hit is None else ImageChops.multiply(hit, here)
        found = hit if found is None else ImageChops.lighter(found, hit)
    return found


class _Ink:
    """Where this program's own arrow is on the board it is about to read.

    Only that corner of the board is turned into floats. Two float images the
    size of the whole board were the most expensive thing this reader did, and
    the arrow is never more than a few squares wide. Which squares fall inside
    that corner is a rectangle test; how much of each is actually covered is
    measured exactly afterwards, off the weights themselves.
    """

    __slots__ = ("lit", "seen", "high", "low", "alive", "touched", "at")

    def __init__(self, board_img, grey):
        rgb = board_img.convert("RGB")
        wide, high = rgb.size
        # Looked for on every other pixel first. Six lookup tables and four
        # whole-image boolean ops over an 824px board is 7.3 ms, and the great
        # majority of frames have no arrow on them at all, so the search runs
        # on a quarter of the picture and the exact mask is then built only
        # over the corner it found. The shaft is a sixth of a square wide and
        # never under two pixels, so a line drawn at all covers two adjacent
        # columns of every row it crosses and cannot fall between the samples.
        rough = _arrow_mask(rgb.resize((max(1, wide // 2), max(1, high // 2)),
                                       Image.NEAREST)).getbbox()
        if rough is None:
            raise ValueError("no arrow on this board")
        bbox = (max(0, rough[0] * 2 - 2), max(0, rough[1] * 2 - 2),
                min(wide, rough[2] * 2 + 2), min(high, rough[3] * 2 + 2))
        # Widened to whole squares, because everything below is indexed by
        # square. A square the widening picks up that the arrow does not
        # actually touch measures as untouched and is read the ordinary way.
        step = grey.size[0] / 8.0
        x0 = int(math.floor(int(bbox[0] / step) * step))
        y0 = int(math.floor(int(bbox[1] / step) * step))
        x1 = int(math.ceil(min(8, math.ceil(bbox[2] / step)) * step))
        y1 = int(math.ceil(min(8, math.ceil(bbox[3] / step)) * step))
        window = (x0, y0, min(x1, grey.size[0]), min(y1, grey.size[1]))
        self.at = (x0, y0)
        self.touched = [x0 <= (i % 8) * step and (i % 8 + 1) * step <= x1 + 1
                        and y0 <= (i // 8) * step and (i // 8 + 1) * step <= y1 + 1
                        for i in range(64)]
        cut = _arrow_mask(rgb.crop(window))
        here = grey.crop(window)
        self.seen = Image.new("F", cut.size, 1.0)
        self.seen.paste(0.0, (0, 0), cut)
        self.lit = here.convert("F")
        self.lit.paste(0.0, (0, 0), cut)
        # The emptiness test reads extremes rather than an average, so it needs
        # the arrow taken out of the picture in both directions: a covered
        # pixel must not be the brightest thing in the square and must not be
        # the darkest either.
        self.high = here.copy()
        self.high.paste(0, (0, 0), cut)
        self.low = here.copy()
        self.low.paste(255, (0, 0), cut)
        self.alive = ImageChops.invert(cut)

    def shift(self, box):
        return (box[0] - self.at[0], box[1] - self.at[1],
                box[2] - self.at[0], box[3] - self.at[1])

    def cells(self, box):
        """One square as the mean level of each cell's surviving pixels, and
        how much of each cell survived.

        Nothing is filled in. A cell the arrow covers outright comes back with
        a weight of zero and takes no part in anything after this: not the
        emptiness test, not the correlation, not the margin.
        """
        box = self.shift(box)
        num = _reduce(self.lit, box, RES, _IDX)
        den = _reduce(self.seen, box, RES, _IDX)
        total = sum(den)
        if total <= 1e-6:
            return None, None, 1.0
        return ([n / d if d > 1e-6 else 0.0 for n, d in zip(num, den)],
                den, 1.0 - total / len(den))

    def reach(self, mid, span):
        """How far the surviving pixels in the middle of this square reach.

        None when the arrow has taken so much of the middle that the question
        cannot be asked, which is a refusal rather than an empty square. A
        fifth of the window is enough to answer it: the test is an extreme
        rather than an average, so it wants some of the piece rather than most
        of it.
        """
        box = self.shift(mid)
        left = self.alive.crop(box).histogram()[255]
        if left < 0.2 * (box[2] - box[0]) * (box[3] - box[1]):
            return None
        top = self.high.crop(box).getextrema()[1]
        low = self.low.crop(box).getextrema()[0]
        return max(0, top - low) / span


# ---------------------------------------------------------------- one square

class _Square:
    """One square reduced to what the matcher compares.

    The raw levels, the same zero-meaned and scaled to unit length, a coarse
    copy for the shortlist, and how far the middle of the square reaches, which
    is the whole of the empty-or-not decision and is deliberately measured on
    the picture rather than on the reduction.

    weights is None for an ordinary square. When the program's own arrow
    crosses the square it holds how much of each cell survived, and every
    number below that depends on the levels is then measured over what is left.

    Two of these are worked out only if something asks. The unit vector is what
    a template is stored as and what enroll compares two squares with, and the
    matcher never touches it: the square's own length is the same divisor for
    every candidate, so it divides out of the ranking and is applied once at
    the end. The ink shares describe a piece set and are read on a learn and on
    a fingerprint, not on a read. Both were 3.6 ms of every board when they
    were worked out up front for all 64 squares whether or not anyone asked.
    """

    __slots__ = ("cells", "rough", "spread", "reach", "weights", "lost",
                 "levels", "_unit", "_shares", "_ground")

    def __init__(self, cells, reach, rough=None, weights=None, lost=0.0,
                 levels=DEFAULT_LEVELS, shares=None):
        self.cells = cells
        self.reach = reach
        self.weights = weights
        self.lost = lost
        self.rough = rough
        self.levels = levels
        self._unit = None
        self._shares = shares
        self._ground = None
        if weights is None:
            self.spread = _stats(cells)[1]
        else:
            # In the same units as the plain path, so one emptiness cutoff
            # serves both: the spread of what survived, times root cells.
            self.spread = (math.sqrt(max(_moments(cells, weights)[2], 0.0))
                           * math.sqrt(len(cells)))

    @property
    def unit(self):
        if self._unit is None:
            self._unit = _unit(self.cells)
        return self._unit

    @property
    def flat(self):
        return self.spread < 1e-9

    @property
    def span(self):
        return _span(self.levels)

    @property
    def ground(self):
        """The square's own colour, as its median cell. A piece drags a mean
        and cannot drag a median: over half of any square is board."""
        if self._ground is None:
            self._ground = sorted(self.cells)[len(self.cells) // 2]
        return self._ground

    @property
    def occupied(self):
        return self.reach is not None and self.reach >= OCCUPIED_F

    @property
    def painted(self):
        """True when this square is flat at a colour the board does not paint
        squares with, which is something drawn over it rather than an empty
        square. See EMPTY_GROUND_F."""
        lo, hi = self.levels
        bar = EMPTY_GROUND_F * self.span
        return self.ground < lo - bar or self.ground > hi + bar

    @property
    def bright(self):
        return self.shares[0]

    @property
    def dark(self):
        return self.shares[1]

    @property
    def shares(self):
        if self._shares is None:
            self._shares = _shares(self.cells, self.span)
        return self._shares

    @property
    def coverage(self):
        """How much of the square is ink either side of its own ground level.

        A description of what is drawn here and never the empty-or-not
        decision, which is `occupied`. Sharing one measurement between the two
        questions is what used to delete a piece whenever anything damaged its
        ink.
        """
        return sum(self.shares) / max(1, len(self.cells))


def _shares(cells, span):
    """How many cells sit clear of this square's own ground level, either way.

    The ground is the median rather than the mean, because a piece drags a mean
    and cannot drag a median: over half of any square is board. The distance is
    a fraction of the gap between the two square colours, which is the one
    absolute number left in this file and is anchored on the pair of levels a
    board always has rather than on whatever extremes its ink reached.
    """
    mid = sorted(cells)[len(cells) // 2]
    bar = span * SHARE_F
    bright = sum(1 for v in cells if v - mid > bar)
    dark = sum(1 for v in cells if mid - v > bar)
    return bright, dark


def _mid_box(size, row, col):
    """The middle of one square, which is the only part the emptiness test
    looks at. See OCCUPIED_INSIDE."""
    step = size / 8.0
    pad = step * (1 - OCCUPIED_INSIDE) / 2
    return (int(col * step + pad), int(row * step + pad),
            int((col + 1) * step - pad), int((row + 1) * step - pad))


def _reach_of(grey, box, span):
    """How far apart the brightest and the darkest pixel in the middle of one
    square are, as a fraction of the board's own contrast."""
    low, top = grey.crop(box).getextrema()
    return (top - low) / span


def _reduce_board(board_img, levels=None, ink=None):
    """Every square of a board reduced at once.

    One pass, because two of the numbers a square is judged on are about the
    board as a whole: the contrast the emptiness test is a fraction of, and
    whether the program's own arrow is on screen.
    """
    levels = levels or _levels(board_img)
    span = _span(levels)
    grey = board_img.convert("L")
    size = grey.size[0]
    boxes = _crop_boxes(size)
    out = []
    for i, box in enumerate(boxes):
        row, col = divmod(i, 8)
        mid = _mid_box(size, row, col)
        if ink is None or not ink.touched[i]:
            cells = _reduce(grey, box, RES, _IDX)
            out.append(_Square(cells, _reach_of(grey, mid, span),
                               rough=_unit(_reduce(grey, box, COARSE, _CIDX)),
                               levels=levels))
            continue
        cells, weights, lost = ink.cells(box)
        if cells is None:
            out.append(_Square([0.0] * _CELLS, None, lost=1.0,
                               levels=levels))
            continue
        if lost <= 1e-4:
            # Inside the arrow's bounding box but not actually under it, so it
            # is an ordinary square and is held to the ordinary bar.
            out.append(_Square(cells, _reach_of(grey, mid, span),
                               rough=_unit(_reduce(grey, box, COARSE, _CIDX)),
                               levels=levels))
            continue
        out.append(_Square(cells, ink.reach(mid, span), weights=weights,
                           lost=lost, levels=levels))
    return out


def _features(square_img, levels=None, span=None):
    """Reduce one already cut square image to a _Square.

    The board-wide route is _reduce_board; this is for a caller holding one
    square, and it cuts the same crop out of it. `levels` is accepted so that a
    caller who has already measured the board can hand that in instead of a
    span.
    """
    levels = levels or DEFAULT_LEVELS
    span = _span(levels) if span is None else span
    grey = square_img.convert("L")
    wide, high = grey.size
    pad_x, pad_y = wide * INSET, high * INSET
    box = (pad_x, pad_y, wide - pad_x, high - pad_y)
    cells = _reduce(grey, box, RES, _IDX)
    mx, my = wide * (1 - OCCUPIED_INSIDE) / 2, high * (1 - OCCUPIED_INSIDE) / 2
    low, top = grey.crop((int(mx), int(my),
                          int(wide - mx), int(high - my))).getextrema()
    return _Square(cells, (top - low) / span,
                   rough=_unit(_reduce(grey, box, COARSE, _CIDX)), levels=levels)


def _board_features(board_img, levels=None):
    """Every square of a board reduced at once, in screen order."""
    return _reduce_board(board_img, levels)


# ------------------------------------------------------------- the templates

def _weighted(img, op):
    """A copy of one square drawn with a heavier or a lighter outline."""
    if op == "thin":
        return img.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    return img.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))


# How much of a template's own spread a cell has to be clear of its ground
# level to count as part of the piece. This decides the denominator that a
# cursor or a popup cannot move. Three quarters of a spread keeps the body
# and the outline of every piece on both fixtures and drops the board around
# them.
INK_SIGMA = 0.75

class _Bank:
    """One rendering of one piece, in the four shapes the matcher wants it in.

    fine is the unit vector the plain dot product takes. rough is the same at
    the shortlist's resolution, and only a rendering that was cut or
    transplanted carries one. sq is fine squared, which the weighted path
    needs. ink is which cells this piece drew on and sign is which side of
    its own ground level each of those sits, which is what _agreement asks
    about.

    fine doubles as the template's raw levels. Every use of a template here is
    invariant to `a*x + b`, so any affine image of the levels it was cut from
    will do, and the zero-mean unit vector is the one already in hand.
    """

    __slots__ = ("fine", "rough", "sq", "ink", "sign")

    def __init__(self, fine, rough):
        self.fine = fine
        self.rough = rough
        self.sq = [x * x for x in fine]
        # fine is zero-mean, so its own ground level is near zero and a cell is
        # ink when it is clear of that either way.
        bar = INK_SIGMA * math.sqrt(sum(self.sq) / len(fine))
        self.ink = [i for i, x in enumerate(fine) if abs(x) > bar]
        # Every cell and not only the ink ones, because a rival is asked about
        # the believed piece's cells and has to be able to answer "I draw
        # nothing there", which is the whole of what separates it.
        self.sign = [0 if -bar <= x <= bar else (1 if x > 0 else -1)
                     for x in fine]


def _bank_of(img, box=None):
    """One square image as a _Bank, or None when it is flat."""
    if box is None:
        pad = INSET * img.size[0]
        box = (pad, pad, img.size[0] - pad, img.size[1] - pad)
    fine = _unit(_reduce(img, box, RES, _IDX))
    if fine is None:
        return None
    return _Bank(fine, _unit(_reduce(img, box, COARSE, _CIDX)))


class _Template:
    """A piece as one thing the reader was shown, and every rendering of it.

    img is the square as it was cut, kept at TEMPLATE_PX so it can be rendered
    again. banks is what the matcher actually compares: that square, the same
    piece moved onto the other colour of square when the position never showed
    it there, each of those reduced from several window sizes, and each drawn
    with a heavier and a lighter outline. Only the square itself was seen; the
    rest are guesses about how another set or another window might draw the
    same piece.

    feat is that square reduced, which is what enroll compares two squares with
    and what _signature describes the set from.

    The renderings are built on first use and thrown away by fold(), because
    observe() folds a burst of frames in at once and rebuilding once at the end
    of the burst costs a tenth of rebuilding on each of them.
    """

    __slots__ = ("feat", "light", "n", "img", "shade", "shades", "synth",
                 "_banks")

    def __init__(self, feat, light=None, img=None, shades=None, shade=None,
                 synth=True):
        self.feat = feat
        self.light = light
        self.n = 1
        self.img = img
        self.shades = shades
        # Which colour of square the piece is standing on. `light` is what the
        # sheet or the position declared and is part of this class's contract;
        # this is what the pixels say, which a plain twelve slot sheet cannot
        # declare and _transplant has to know.
        self.shade = light if shade is None else shade
        # Whether the piece has to be made up on the other colour of square. A
        # made-up rendering is only worth having where there is no real one: a
        # starting position shows eight of the twelve on both colours, and for
        # those eight a transplant would be a worse copy of a template already
        # in hand and one more thing to score every square against.
        self.synth = synth
        self._banks = None

    @property
    def banks(self):
        if self._banks is None:
            if self.img is None:
                self._banks = ([] if self.feat.flat
                               else [_Bank(self.feat.unit, self.feat.rough)])
            else:
                self._banks = _renderings(self.img, self.shade, self.shades,
                                          self.synth)
        return self._banks

    def fold(self, crop, span):
        """Average one more sighting of this piece into the template.

        One snapshot is one rendering: one antialiasing phase, one moment of
        the board's animation, one crop. Averaging over several confident
        frames keeps the parts of a piece that were there every time and fades
        the parts that were not.

        The pictures are averaged and not the reduced vectors, so that every
        rendering below is remade from the average rather than only the one the
        matcher compares first.
        """
        if self.img is None:
            return
        if crop.size != self.img.size:
            crop = crop.resize(self.img.size, Image.LANCZOS)
        self.n += 1
        self.img = Image.blend(self.img, crop, 1.0 / self.n)
        self.feat = _features(self.img, span=span)
        self._banks = None


def _renderings(crop, shade, shades, synth=True):
    """Every bank one taught square turns into.

    Three axes, each of them a thing correlation is not already blind to. The
    square colour, because a piece standing on a light square and the same
    piece on a dark one are not related by any change of intensity: the body
    keeps its own level while the ground under it moves. The outline weight,
    because a set drawn heavier or lighter than this one is not an intensity
    change either. The window size, because a small board is not a big one
    scaled down: resampling takes a hairline outline away rather than thinning
    it, and learn() sees exactly one size and is then asked about every size a
    browser window can be.

    Only the square as it stands, and the transplant of it, carry a shortlist
    entry. The shortlist runs over every rendering of every piece on every
    square and is most of what a board costs, and at eight cells across a
    thinned copy of a piece and a smaller copy of it are the same picture, so
    the coarse pass is paid twice for nothing. The full comparison keeps them
    all.

    A bad rendering costs a comparison and nothing else. It is one more
    candidate to be beaten, never a replacement for a square that was seen.
    """
    bases = [crop]
    if synth:
        other = _transplant(crop, shade, shades)
        if other is not None:
            bases.append(other)
    out = []
    for base in bases:
        made = [base]
        made += [base.resize((px, px), RESAMPLE)
                 for px in LEARN_PX if px < base.size[0]]
        made += [_weighted(base.resize((AUGMENT_PX, AUGMENT_PX), RESAMPLE), op)
                 for op in AUGMENT]
        for i, img in enumerate(made):
            bank = _bank_of(img)
            if bank is None:
                continue
            if i:
                bank.rough = None
            out.append(bank)
    return out


def _ground_shade(crop, levels):
    """Which of the board's two square colours this square is standing on.

    The median level rather than the mean, because a piece drags a mean and
    cannot drag a median: over half of any square is board. A plain twelve slot
    sheet records nothing about square colour, and without this its templates
    could not be moved onto the other one.
    """
    grey = crop.convert("L")
    hist = grey.histogram()
    half = sum(hist) // 2
    run = 0
    for level in range(256):
        run += hist[level]
        if run >= half:
            break
    return abs(level - levels[1]) <= abs(level - levels[0])


def _transplant(crop, light, shades):
    """The same piece, drawn on the other colour of square.

    The board has two flat colours and both have been measured, so a pixel
    close to the one this piece is standing on is ground and every other pixel
    is the piece. Repaint the ground and the piece is left where it was.

    None when there is nothing to say which colour it is standing on, which is
    a plain twelve slot sheet: see _read_sheet.
    """
    if light is None or not shades:
        return None
    here, there = shades[bool(light)], shades[not light]
    if here is None or there is None or here == there:
        return None
    tol = max(2.0, abs(here - there) * GROUND_TOL_F)
    level = int(round(there))
    out = Image.new("L", crop.size)
    out.putdata([level if abs(p - here) <= tol else p for p in crop.getdata()])
    return out


# ------------------------------------------------------------- the comparison

def _score(a, b):
    """How alike two squares are, as a correlation in -1 to 1.

    Zero-meaned and scaled to unit length on both sides, so it says nothing
    about how bright either was and everything about the shape drawn on it.
    """
    if a is None or b is None or a.flat or b.flat:
        return 0.0
    return sum(map(operator.mul, a.unit, b.unit))


def _shortlist(feat, templates, keep=KEEP):
    """The piece types worth settling at full resolution.

    Scored on a COARSE by COARSE reduction, which is most of what reading a
    board costs, so only the renderings that are distinguishable at that size
    have an entry here. See _renderings.
    """
    if feat.rough is None:
        return None
    best = {}
    for symbol, variants in templates.items():
        top = -2.0
        for t in variants:
            for bank in t.banks:
                if bank.rough is None:
                    continue
                dot = sum(map(operator.mul, feat.rough, bank.rough))
                if dot > top:
                    top = dot
        best[symbol] = top
    return set(sorted(best, key=lambda s: -best[s])[:keep])


def _plain_scores(feat, templates, only=None):
    """The best correlation each piece type reaches on this square.

    The square's own length is the same divisor for every template, so the
    comparison is over raw dot products and the division happens once per piece
    type at the end rather than once per template.
    """
    out = {}
    for symbol, variants in templates.items():
        if only is not None and symbol not in only:
            continue
        top = -1e18
        for t in variants:
            for bank in t.banks:
                dot = sum(map(operator.mul, feat.cells, bank.fine))
                if dot > top:
                    top = dot
        out[symbol] = top / feat.spread
    return out


def _weighted_scores(feat, templates, only=None):
    """The same over the part of a square the arrow has left visible, each cell
    counting for as much of it as survived.

    The same zero-mean normalised correlation with a weight on every term:

        num = sum(w.v.t) - sum(w.v).sum(w.t)/sum(w)
        den = sqrt( [sum(w.v.v) - sum(w.v)^2/sum(w)]
                  . [sum(w.t.t) - sum(w.t)^2/sum(w)] )

    so a covered cell contributes nothing at all rather than contributing
    something invented. It matters that nothing is filled in. Masking the arrow
    and filling each covered cell from the square's surround halves the wrong
    squares and leaves the survivors scoring HIGHER than the correct answers,
    because the score is then grading the reconstruction rather than the
    picture. Weighted, when the shaft takes the part of a piece that said which
    piece it was, the runner up closes up and the square is refused, which is
    the answer.

    Three dot products a template instead of one, and only on the dozen or so
    squares an arrow actually crosses.
    """
    w = feat.weights
    sw, swv, var = _moments(feat.cells, w)
    dv = var * sw
    if dv <= 1e-9:
        return {}
    wv = [a * b for a, b in zip(w, feat.cells)]
    out = {}
    for symbol, variants in templates.items():
        if only is not None and symbol not in only:
            continue
        top = -1e18
        for t in variants:
            for bank in t.banks:
                swt = sum(map(operator.mul, w, bank.fine))
                dt = sum(map(operator.mul, w, bank.sq)) - swt * swt / sw
                if dt <= 1e-9:
                    continue
                num = sum(map(operator.mul, wv, bank.fine)) - swv * swt / sw
                score = num / math.sqrt(dv * dt)
                if score > top:
                    top = score
        if top > -1e17:
            out[symbol] = top
    return out


def _scores(feat, templates, only=None):
    """Every piece type this square is worth scoring against, and how well.

    One entry per piece type and not per template. A piece learned on a light
    square, the same piece moved onto a dark one and the same piece drawn with
    a heavier outline are renderings of one type and their scores are maxed,
    never compared: which of them the square happens to look most like is not a
    thing to be uncertain between.
    """
    if feat.weights is None:
        if feat.spread < 1e-9:
            return {}
        return _plain_scores(feat, templates, only)
    return _weighted_scores(feat, templates, only)


def _best(scores, twins=False):
    """(symbol, its score, the best score it has to beat).

    Over piece types rather than over the twelve symbols: a white knight
    beating a black knight is one shape scored twice, not two things to be
    uncertain between, and letting that thin the margin threw away most of a
    foreign piece set.

    That reasoning holds only while the square still shows which colour the
    piece is. On one the program's own arrow has painted over it may not: what
    separates a white piece from a black one is a light body in a dark edge
    against a dark body, and the shaft can be lying across exactly that. So a
    covered square gets its own colour twin back as a rival, and a piece that
    cannot beat its own opposite comes back "?" rather than the wrong colour.
    Measured: over the quick corpus with an arrow drawn it is the whole of the
    difference between 12 wrong squares and none, all twelve of them a pawn
    read in the wrong colour under the shaft, and it costs 39 refusals.
    """
    if not scores:
        return "", 0.0, 0.0
    ranked = sorted(scores.items(), key=lambda pair: -pair[1])
    symbol, top = ranked[0]
    if twins:
        second = ranked[1][1] if len(ranked) > 1 else 0.0
    else:
        second = next((s for sym, s in ranked[1:]
                       if sym.lower() != symbol.lower()), 0.0)
    return symbol, top, second


def ranking(feat, templates, keep=KEEP):
    """Every piece type scored against this square, best first.

    keep is the shortlist, which is why this is short. Pass None for all
    twelve, which is what a caller measuring the reader rather than using it
    wants.
    """
    only = _shortlist(feat, templates, keep) if keep else None
    return sorted(((v, k) for k, v in _scores(feat, templates, only).items()),
                  reverse=True)


def _judge(feat, templates, floor=FLOOR, margin=MARGIN):
    """The piece on one already reduced square. Returns (symbol, score), where
    symbol is a piece letter, "." for an empty square, or None when the pixels
    do not settle it.

    Empty first and separately. How far the middle of the square reaches is a
    question about the picture that no template is involved in, so a piece
    whose ink a bad capture has damaged is refused rather than deleted. That
    was 46% of every wrong answer the reader before this made.

    Every template is scored, both colours. Colour is not decided by anything
    outside the correlation: a white piece on this board and the black piece of
    the same shape differ by more than a change of brightness, since one is a
    light body in a dark edge and the other a dark body, and normalising each
    square by its own spread does not make them the same picture.
    """
    if feat is None:
        return ".", 1.0
    if feat.lost > ARROW_REFUSE:
        return None, 0.0
    if not feat.occupied:
        # Two ways a flat square is not an empty one. The program's own arrow
        # can have taken the middle of it, and then it reads flat because the
        # evidence was painted over. And something else can have painted the
        # whole square, which shows no contrast either but is not a colour this
        # board paints squares with. Both are refusals; neither is a ".".
        if feat.reach is None or feat.painted:
            return None, 0.0
        return ".", 1.0
    if feat.weights is not None:
        floor, margin = max(floor, ARROW_FLOOR), max(margin, ARROW_MARGIN)
    elif feat.flat:
        return None, 0.0
    symbol, best, second = _best(_scores(feat, templates,
                                         _shortlist(feat, templates)),
                                 twins=feat.weights is not None)
    if not symbol or best < floor or best - second < margin:
        return None, max(0.0, best)
    return symbol, best


# How far a cell has to be from a square's own ground level before it counts
# as ink one way or the other, in units of that square's spread. Cells inside
# the band are neither and count against every candidate alike, so a square
# something has painted flat is evidence for nothing rather than evidence for
# whichever piece is smallest.
DEAD_SIGMA = 0.35


def _ink_bank(feat, variants):
    """Which rendering of the believed piece decides where its ink is.

    The one this square looks most like over the whole square. A piece is
    taught on one colour of square and made up on the other, and asking about
    the ink of the wrong one would be asking about a picture the board is not
    showing. Only the renderings that were cut or transplanted are candidates:
    a thinned or a shrunk copy has thinner ink, which is a worse description of
    where the piece is than the thing it was made from.
    """
    best, top = None, -2.0
    for t in variants:
        for bank in t.banks:
            if bank.rough is None or not bank.ink:
                continue
            got = sum(map(operator.mul, feat.cells, bank.fine))
            if got > top:
                best, top = bank, got
    return best


def _signs(feat):
    """Which side of its own ground level each cell of this square sits.

    Zero for a cell too near the middle to say, which is what a square painted
    flat comes back as everywhere. The ground is the median rather than the
    mean, because a piece drags a mean and cannot drag a median: over half of
    any square is board.
    """
    cells = feat.cells
    mid = sorted(cells)[len(cells) // 2]
    bar = DEAD_SIGMA * feat.spread / math.sqrt(len(cells))
    return [0 if -bar <= x - mid <= bar else (1 if x > mid else -1)
            for x in cells]


def _agreement(feat, signs, idx, sign):
    """How much of one piece's ink the square is still drawing, over the cells
    that piece drew on and nothing else.

    A count and not a correlation, and that is the whole point. The correlation
    is what _judge already asks and what an overlay spoils: a cursor across a
    piece moves the square's own spread, which is the divisor, so all twelve
    scores sink together and the winner stops beating the runner up. This asks
    the plainest question that survives it. Of the cells this piece draws ink
    on, how many is the square still drawing ink on, the same side of its own
    ground level. The denominator is the piece's own size and nothing on the
    square can move it.
    """
    n = len(idx)
    if not n:
        return 0.0
    if feat.weights is None:
        return sum(1 for i in idx if signs[i] == sign[i]) / n
    # A cell the arrow covers counts for as much of it as survived, on both
    # sides of the fraction, so it neither agrees nor disagrees.
    w = feat.weights
    seen = sum(w[i] for i in idx)
    if seen <= 1e-9:
        return 0.0
    return sum(w[i] for i in idx if signs[i] == sign[i]) / seen


def _contains(feat, templates, symbol):
    """How much of each piece's ink the square still holds, measured over the
    cells the BELIEVED piece is drawn on, and which cells those are.

    Every candidate is scored on the same cells, and that is the half of this
    that had to be got right. Scoring each piece over its own ink instead reads
    a pawn believed under a panel where a queen stands as a confident yes: the
    pawn's cells are a corner of the queen's body and undamaged, the queen's
    own cells take in the part the panel covered, and the small shape wins a
    comparison it was never in. On one cell set the queen's body draws those
    cells as well as the pawn does, no margin opens, and the square is refused.
    Over the obstructed captures piecetest builds that is the difference
    between 54 wrong beliefs confirmed and none.
    """
    bank = _ink_bank(feat, templates[symbol])
    if bank is None:
        return {}, None
    signs = _signs(feat)
    return ({sym: max((_agreement(feat, signs, bank.ink, b.sign)
                       for t in variants for b in t.banks
                       if b.rough is not None), default=0.0)
             for sym, variants in templates.items()}, bank.ink)


def _confirms(feat, templates, symbol):
    """Could this square still be holding exactly this piece, with something
    drawn on top of it? Yes or no, and never a guess at what else it is.

    A narrower question than _judge's, and answerable where that one is not.
    _agreement counts the believed piece's own ink rather than dividing by a
    spread the overlay has moved, so a piece a cursor only partly hides can
    still clear the field where the twelve scores no longer can.

    The trap is that a blob agrees with every shape smaller than itself, and a
    pawn is smaller than everything. Hence the second half: the named piece has
    to be the only one the square agrees with and not merely one of them.

    It may only ever confirm a piece already believed to be there, never name
    one. Which of two pieces of one colour last stood on a square it cannot
    see, and the belief is where that comes from.
    """
    if feat is None or symbol == "." or symbol not in templates:
        return False
    if feat.reach is not None and not feat.occupied:
        return False
    held, _ = _contains(feat, templates, symbol)
    if not held:
        return False
    got = held[symbol]
    # Over all eleven others and not over piece types, which is where this
    # parts company with _judge. There the colour twin is one shape scored
    # twice; here it is the failure the whole test exists to refuse. A pawn
    # taken by the other side's pawn leaves a pawn standing on the square, and
    # a comparison that will not look at the opposite colour confirms the piece
    # that was captured. Measured on a clean board: four of the thirty two
    # pieces confirm as their own opposite over piece types, and none does over
    # all eleven.
    rival = max((v for sym, v in held.items() if sym != symbol), default=0.0)
    return got >= CONFIRM_CONTAIN and got - rival >= CONFIRM_MARGIN


def _decide(square_img, templates, levels=None, floor=FLOOR):
    """_judge on a square that has not been reduced yet."""
    return _judge(_features(square_img, levels=levels), templates, floor)


# ---------------------------------------------------- which set is this again

def _signature(feats, levels, square_px):
    """How the pieces on this board are drawn, and how well it was captured.

    How much of a piece is outline rather than fill, averaged over the light
    pieces and again over the dark ones. That is a property of the set and not
    of the position, which is what both the cache key and the trust test need:
    chess.com's own reads about the same on an opening, on a mate and on a
    three piece endgame, while a flat set reads differently whatever is
    standing on it.

    Then how far a piece reaches out of its square, averaged. That is unchanged
    by a brightness or a contrast knob, which scales every level together and
    is divided out by the board's own span, and it collapses under blur, which
    does not. A foreign set and a smeared capture both mean the templates
    describe something this board is not, and both want the same answer.

    The scale comes along because the measurement needs it. A capture whose
    squares are much smaller has resampled the outline away and then reads as a
    heavier or a lighter set than it is.
    """
    light, dark, reach = [], [], []
    for feat in feats:
        if feat is None or not feat.occupied:
            continue
        total = feat.bright + feat.dark
        if not total:
            continue
        reach.append(feat.reach)
        if feat.bright > feat.dark:
            light.append(feat.dark / total)
        else:
            dark.append(feat.bright / total)
    return (sum(light) / len(light) if light else None,
            sum(dark) / len(dark) if dark else None,
            sum(reach) / len(reach) if reach else None,
            square_px)


def _trusted(templates_sig, board_sig):
    """Whether these templates were drawn from the set now on the board.

    A set that is not the one in hand still scores something, and a call that
    is only the best of a bad field is a guess. The correlation floor catches
    most of that on its own, and this is what catches the rest: a foreign set
    whose pieces happen to be drawn the way this one draws a different piece.

    Outline thickness cannot be compared across a big change of scale, so a
    capture whose squares are more than TRUST_SCALE from the ones the templates
    were cut at is trusted rather than judged. The smaller of the two has
    resampled the outline away rather than been drawn without one.
    """
    if templates_sig is None or board_sig is None:
        return True
    a, b = templates_sig[3], board_sig[3]
    if a and b and max(a, b) > TRUST_SCALE * min(a, b):
        return True
    apart = [abs(x - y) for x, y in zip(templates_sig[:3], board_sig[:3])
             if x is not None and y is not None]
    return not apart or max(apart) <= TRUST_TOL


def _fingerprint(levels, signature):
    """The cache key: the board theme and the signature, quantised coarsely.

    Coarse on purpose. Two sets drawn alike land in one bucket, and templates
    from either of those read the other, so sharing an entry is the answer
    rather than a collision.
    """
    key = "%d-%d-%.1f-%.1f" % (
        levels[0], levels[1],
        signature[0] if signature[0] is not None else -1.0,
        signature[1] if signature[1] is not None else -1.0)
    return hashlib.sha1(key.encode()).hexdigest()[:12]


# What a cache file has to say it is before it is loaded. Bumped whenever the
# shape of what is written changes.
CACHE_VERSION = 3


def _pack_img(img):
    """The taught square as bytes.

    The picture is stored rather than the vectors made from it, so a restored
    set is rendered again exactly the way a freshly learned one is and cannot
    quietly come back holding fewer renderings than it was written with.
    """
    small = img if img.size == (TEMPLATE_PX, TEMPLATE_PX) else img.resize(
        (TEMPLATE_PX, TEMPLATE_PX), Image.LANCZOS)
    return base64.b64encode(zlib.compress(small.tobytes(), 6)).decode()


def _unpack_img(blob):
    return Image.frombytes("L", (TEMPLATE_PX, TEMPLATE_PX),
                           zlib.decompress(base64.b64decode(blob)))


class PieceReader:
    def __init__(self, sheet=TEMPLATE_SHEET, cache_dir=CACHE_DIR):
        self.sheet = sheet
        self.cache_dir = cache_dir
        self.templates = {}
        self.source = "none"
        self.learned_size = None
        self.signature = None
        self._folds = 0
        self._saved = 0
        self.use_bundled()

    @property
    def ready(self):
        return len(self.templates) == 12

    def _read_sheet(self, path):
        """Load a sheet of either width. See PLAIN_SLOTS and PAIRED_SLOTS.

        The reader has always held a list of templates per piece and scored a
        square against the best of them, so a paired sheet needs nothing new
        below this: it is only the file that could not say it. What it buys is
        the square colour, which is the one difference between two squares that
        no amount of normalising can remove, since the ground moves and the
        piece on it does not.

        A plain sheet does not record which colour anything stood on, so its
        templates are not moved onto the other colour: there is nothing to say
        which one that would be. It is read as it is and learn() replaces it
        the first time a position we are sure of appears.
        """
        img = Image.open(path).convert("L")
        # crop() pads out of bounds with black and a black block is a shape
        # like any other, so a truncated sheet would load as twelve templates
        # of nothing rather than fail. Check the width instead.
        whole = img.size[0] // TEMPLATE_PX
        if whole >= PAIRED_SLOTS:
            slots = PAIRED_SLOTS
        elif whole >= PLAIN_SLOTS:
            slots = PLAIN_SLOTS
        else:
            raise ValueError("template sheet holds fewer than twelve pieces")
        # The sheet is real squares side by side, board colours and all, so its
        # own two greys measure the same way a board's do.
        levels = _levels(img)
        shades = {True: levels[1], False: levels[0]}
        span = _span(levels)
        out = {}
        feats = []
        for slot in range(slots):
            symbol = ORDER[slot % PLAIN_SLOTS]
            crop = img.crop((slot * TEMPLATE_PX, 0,
                             (slot + 1) * TEMPLATE_PX, TEMPLATE_PX))
            feat = _features(crop, span=span)
            if feat is None or feat.flat or not feat.occupied:
                raise ValueError("template sheet holds a blank slot")
            light = slot < PLAIN_SLOTS if slots == PAIRED_SLOTS else None
            # A paired sheet holds both colours already, so nothing has to be
            # made up. A plain one holds each piece on one colour and does not
            # say which, so the colour is measured off the slot and the other
            # is made from it.
            out.setdefault(symbol, []).append(
                _Template(feat, light, crop, shades,
                          shade=_ground_shade(crop, levels),
                          synth=slots == PLAIN_SLOTS))
            feats.append(feat)
        return out, _signature(feats, levels, TEMPLATE_PX)

    def use_bundled(self):
        """Go back to the templates that ship with the program. Loading is all
        or nothing, so a sheet that will not read leaves whatever is already in
        use alone instead of half replacing it."""
        try:
            loaded, signature = self._read_sheet(self.sheet)
        except Exception:
            return False
        self.templates = loaded
        self.signature = signature
        self.source = "bundled"
        self.learned_size = None
        return True

    def learn(self, board_img, board, flipped=False):
        """Relearn the templates from a position known to be on screen. Used at
        the start of every game, so the templates match your own rendering.

        Whatever piece types the position holds are learned and the rest stay
        on the sheet. Returning True still means all twelve, because the
        callers that ask for a refit mid game have a full set in hand already
        and want to know.

        Both square colours are learned separately where the position shows
        both, and the ones it does not are made up by moving the piece onto the
        other colour. An opening only ever shows eight of the twelve on both:
        a king starts on one colour and a queen on the other.

        """
        grid = grid_of(board, flipped)
        levels = _levels(board_img)
        shades = {True: levels[1], False: levels[0]}
        span = _span(levels)
        grey = board_img.convert("L")
        size = board_img.size[0]
        feats = _reduce_board(board_img, levels)
        seen = {}
        for i, feat in enumerate(feats):
            row, col = divmod(i, 8)
            symbol = grid[row][col]
            if symbol == "." or feat is None or feat.flat:
                continue
            # a8 and h1 are both light, so screen parity says which colour a
            # square is whichever way round the board is being viewed.
            light = (row + col) % 2 == 0
            seen.setdefault(symbol, {}).setdefault(light, (feat, row, col))
        found = {}
        for symbol, variants in seen.items():
            for light, (feat, row, col) in variants.items():
                found.setdefault(symbol, []).append(_Template(
                    feat, light, grey.crop(_square_box(size, row, col)),
                    shades, synth=(not light) not in variants))
        if not found:
            return False
        self.templates = dict(self.templates)
        self.templates.update(found)
        self.learned_size = size
        self.signature = _signature(feats, levels, size / 8.0)
        if len(found) == 12:
            self.source = "learned from your screen"
            self._cache_write(levels)
            return True
        self.source = "learned in part from your screen"
        return False

    def observe(self, board_img, board, flipped=False):
        """Fold one more frame of a position we are sure about into the
        templates. Returns how many squares it took.

        learn() is one snapshot per piece. The tracker knows the position on
        every frame it is following, so every one of those frames is labelled
        data going spare, and averaging them is what turns a template from one
        rendering of a piece into what that piece looks like on this screen.

        A square the reader already reads as some other piece is refused. That
        is the one signal available here that the picture and the label have
        come apart, which is exactly the failure a frame the tracker has fallen
        a ply behind on produces.
        """
        if not self.ready:
            return 0
        grid = grid_of(board, flipped)
        levels = _levels(board_img)
        shades = {True: levels[1], False: levels[0]}
        span = _span(levels)
        grey = board_img.convert("L")
        size = board_img.size[0]
        feats = _reduce_board(board_img, levels)
        folded = 0
        for i, feat in enumerate(feats):
            row, col = divmod(i, 8)
            symbol = grid[row][col]
            if symbol == "." or symbol not in self.templates:
                continue
            if feat is None or feat.flat or not feat.occupied:
                continue
            if ranking(feat, self.templates)[0][1] != symbol:
                continue
            light = (row + col) % 2 == 0
            crop = grey.crop(_square_box(size, row, col))
            variants = self.templates[symbol]
            match = next((t for t in variants if t.light == light), None)
            if match is None:
                variants.append(_Template(feat, light, crop, shades,
                                          synth=False))
            else:
                match.fold(crop, span)
            folded += 1
        if folded:
            self.learned_size = board_img.size[0]
            self.signature = _signature(feats, levels, board_img.size[0] / 8.0)
            if self.source == "bundled":
                self.source = "learned in part from your screen"
            self._folds += folded
            # The average moves less the longer it runs, so the cache is
            # rewritten on a doubling rather than on a frame.
            if self._folds >= 2 * self._saved:
                self._saved = self._folds
                self._cache_write(levels)
        return folded

    def relearn(self, board_img, board, flipped=False):
        """Learn again part way through a game, after the window changed size.

        All or nothing, unlike learn(). A resize arrives on whatever position
        happens to be up, so refitting from the four piece types an endgame has
        left would trade twelve fitted templates for four, and the caller has
        no way to tell that happened.
        """
        held = dict(self.templates)
        source, size, sig = self.source, self.learned_size, self.signature
        if self.learn(board_img, board, flipped):
            return True
        self.templates, self.source = held, source
        self.learned_size, self.signature = size, sig
        if self.stale(board_img.size[0]):
            self.use_bundled()
        return False

    def stale(self, board_size):
        """True when the templates in hand are worse than the bundled sheet.

        Not "the window changed size". A set learned at or above MIN_LEARN_PX
        is taught at several smaller sizes as well, so it covers a window that
        later shrinks; one learned below that floor was an upsample the moment
        it was cut and cannot be taught its way out of it.
        """
        if self.learned_size is None:
            return False
        return self.learned_size < MIN_LEARN_PX and board_size > self.learned_size

    # ------------------------------------------------------ the disk cache

    def _cache_path(self, width, levels, signature):
        return os.path.join(self.cache_dir, "%d-%s.json" % (
            width, _fingerprint(levels, signature)))

    def _cache_write(self, levels):
        """Keep the templates for the next game on this board and piece set.

        Learning needs a position we are certain about, which in practice means
        catching a game from its first move. Cached, the second game on the
        same window and the same set starts fitted instead of on the sheet.

        Only the square each piece was really seen on is written. Every other
        rendering is made from it, and making them back costs less than a
        thirtieth of a second where writing them would be six times the file.

        Written whole and renamed into place, and every failure swallowed: a
        cache that cannot be written is a slower next game and nothing worse,
        and this runs inside a live capture loop.
        """
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            # A directory that ignores itself, so a cache written into a clone
            # never turns up as something to commit.
            keep = os.path.join(self.cache_dir, ".gitignore")
            if not os.path.exists(keep):
                with open(keep, "w") as fh:
                    fh.write("*\n")
            body = {"version": CACHE_VERSION, "res": RES, "coarse": COARSE,
                    "signature": self.signature, "pieces": [
                {"symbol": symbol, "light": t.light, "shade": t.shade,
                 "synth": t.synth, "n": t.n, "img": _pack_img(t.img)}
                for symbol, variants in self.templates.items()
                for t in variants if t.img is not None]}
            path = self._cache_path(self.learned_size, levels, self.signature)
            with open(path + ".part", "w") as fh:
                json.dump(body, fh)
            os.replace(path + ".part", path)
            return True
        except Exception:
            return False

    def restore(self, board_img):
        """Load templates cached from an earlier game on this board and set.

        The board on screen is fingerprinted and the cache looked up by that
        and the board's width, so a set that was never learned here, or was
        learned at another window size, simply misses and leaves the sheet in
        place.
        """
        levels = _levels(board_img)
        shades = {True: levels[1], False: levels[0]}
        span = _span(levels)
        here = _signature(_reduce_board(board_img, levels), levels,
                          board_img.size[0] / 8.0)
        try:
            with open(self._cache_path(board_img.size[0], levels, here)) as fh:
                body = json.load(fh)
            if (body.get("version") != CACHE_VERSION or body["res"] != RES
                    or body["coarse"] != COARSE):
                return False
            loaded = {}
            for item in body["pieces"]:
                crop = _unpack_img(item["img"])
                t = _Template(_features(crop, span=span), item["light"], crop,
                              shades, shade=item["shade"],
                              synth=item["synth"])
                t.n = item["n"]
                loaded.setdefault(item["symbol"], []).append(t)
            if len(loaded) != 12:
                return False
        except Exception:
            return False
        self.templates = loaded
        # The signature that was cached, not the one just measured. They agree
        # here by construction, since the fingerprint is what found the file,
        # but the cached one is what those templates were actually drawn from.
        self.signature = tuple(body["signature"])
        self.source = "learned in an earlier game"
        self.learned_size = board_img.size[0]
        return True

    # ---------------------------------------------------------- reading

    def _floor(self, feats, levels, square_px):
        """FLOOR, or the mistrusted floor when these templates describe
        something this board is not."""
        here = _signature(feats, levels, square_px)
        return FLOOR if _trusted(self.signature, here) else MISTRUST_FLOOR

    def classify(self, board_img, believed=None):
        """Read the whole board. Returns 8 rows of piece letters and dots, plus
        the weakest match score, which says how much to trust it.

        Every square is reduced first and scored after, rather than one at a
        time, because two of the things a square is judged on are about the
        board as a whole: the contrast the emptiness test is a fraction of, and
        the trust floor, which has to have seen every square before any one of
        them is named.

        `believed` is the grid the caller already thinks is on screen, in the
        same screen order, and is the whole of the second question this asks. A
        square the scores refuse is put back as "is this one named piece still
        here", which _confirms can answer through a pointer or a popup where
        choosing between twelve cannot. It only ever confirms, so a square the
        caller has no belief about, or the wrong belief about, stays "?".
        """
        rows = [["."] * 8 for _ in range(8)]
        weakest = 1.0
        if not self.ready:
            return rows, 0.0

        levels = _levels(board_img)
        grey = board_img.convert("L")
        try:
            ink = _Ink(board_img, grey)
        except ValueError:
            ink = None
        feats = _reduce_board(board_img, levels, ink)
        floor = self._floor(feats, levels, board_img.size[0] / 8.0)
        for i, feat in enumerate(feats):
            row, col = divmod(i, 8)
            symbol, score = _judge(feat, self.templates, floor)
            if symbol == ".":
                continue
            if symbol is None:
                held = believed[row][col] if believed else "."
                rows[row][col] = (held if _confirms(feat, self.templates, held)
                                  else "?")
                # Zero either way. A confirmed square was refused by the
                # scores, so a caller reading the weakest score to decide how
                # far to trust the board must not be told this one scored well.
                weakest = 0.0
            else:
                rows[row][col] = symbol
                weakest = min(weakest, score)
        return rows, weakest

    def piece_at(self, board_img, row, col):
        """Identify the piece on one square of the board, by screen position.
        Returns a piece letter, "." for empty, or None when unsure."""
        if not self.ready:
            return None
        # A whole board pass for one square, because the trust test needs one.
        # This is called on a promotion and nowhere else, so once a game.
        levels = _levels(board_img)
        try:
            ink = _Ink(board_img, board_img.convert("L"))
        except ValueError:
            ink = None
        feats = _reduce_board(board_img, levels, ink)
        return _judge(feats[row * 8 + col], self.templates,
                      self._floor(feats, levels, board_img.size[0] / 8.0))[0]
