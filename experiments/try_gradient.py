"""Telling chess pieces apart by which way their edges run.

The reader in the tree cuts a square into a bright layer and a dark layer and
scores how much those overlap a template's. That asks where the ink is. This
asks how it is arranged, which is the classical answer to "same piece, drawn
by somebody else", and it turns out to be a much easier question.

On the full sweep, 196,608 squares over 3,072 boards, two thirds of them with
the program's own coach arrow painted across the board:

                          right    WRONG   unknown | boards whole  refused  ACCEPTED WRONG
    reader in the tree    86.01%    2.78%   11.22% |        17.8%    80.2%           2.02%
    this file             99.03%    0.00%    0.97% |        58.1%    41.9%           0.00%

and with the arrow left off, which is the same 1,024 boards the first version
of this file was measured on:

    reader in the tree    86.48%    2.74%   10.78% |        27.2%    70.3%           2.44%
    this file             99.60%    0.00%    0.40% |        85.4%    14.6%           0.00%

The board columns are what watcher.check() actually consumes. It takes a frame
all or nothing, so one "?" throws the whole board away and a wrong square only
reaches a game record on a board with no blanks at all. Not one wrong square
survives anywhere in either sweep.

How it works. The whole board is brought to one canonical size and convolved
with four directional derivatives, each clipped so that only its positive half
survives. A kernel whose negative half clips to zero is exactly one signed
orientation bin, so Pillow's own convolution does the binning and the four
channels cost four C-speed passes over the board rather than an atan2 per
pixel. Each channel is then area-pooled into an 8x8 grid of cells per square,
and the 4 x 8 x 8 numbers, scaled to unit length, are the square. Squares are
named by cosine against templates learned from one board of a known position,
and a square whose best match is not clear of the runner up by `margin` comes
back "?" rather than as a confident wrong piece.

What the sweeps said, all of it off bench.py and all of it on the full
1024-board sweep unless it says otherwise. Numbers are top-1 errors out of
29,696 occupied squares, before any margin is applied.

  Cells per square, which is the only parameter that really matters:
  on the quick sweep 2 -> 124 errors, 3 -> 178, 4 -> 46, 5 -> 28, 6 -> 42,
  8 -> 2, 10 -> 0, 16 -> 0; on the full sweep 6 -> 178, 8 -> 40, 10 -> 58,
  12 -> 166, 16 -> 126. Eight is the floor of that curve and it is not close.

  Orientation bins: 4 -> 40, 6 -> 64, 8 -> 80, 16 -> 46. Four directions is
  as good as sixteen and costs a quarter as much, which is the opposite of
  what a HOG paper would lead you to expect and is worth saying plainly.

  Signed against unsigned: folding opposite directions together costs
  46 errors -> 167 on the quick sweep. The sign is where the colour of the
  piece lives, so it cannot be folded away.

  Block normalisation, the thing HOG is famous for, HURTS: 46 errors -> 318 on
  the quick sweep for per-cell L2 before the global one. On a flat background
  a weak cell is weak because it is nearly empty, and rescaling it to match
  the cells the piece is drawn in amplifies nothing but resampling noise.
  Plain global L2 is what works; L1 and no normalisation at all are
  catastrophic, 1837 and 3840 errors.

  The rim: cropping the outer `pad` canonical pixels off each square is the
  difference between working and not. 0 -> 8107 errors, 1 -> 1112, 2 -> 291,
  4 -> 107, 6 -> 70, 8 -> 40, 10 -> 194, 12 -> 419. Two things are going on.
  The seam between two squares is a full-contrast step that no piece owns, and
  under blur it bleeds two or three pixels into both of them. And the outer
  edge of a piece is the only part of it whose contrast depends on which
  colour square it is standing on, which matters because a position holds one
  queen and one king a side, so those four templates are only ever learned on
  one of the two square colours. Half the square, centred, is the optimum.

  Canonical square size: 24 -> 58 errors, 32 -> 40, 40 -> 208, 48 -> 246.
  Resampling filter into it: box, bilinear, bicubic and lanczos all 40,
  hamming 46. It does not matter.

  Derivative kernel: sobel 40, scharr 64, central difference 66. Barely.

  A radial polarity block, computed for free out of the four channels because
  a rectified pair sums back to the signed gradient, on the theory that the
  colour confusions needed help: 0.25 weight -> 40 errors, 0.5 -> 70,
  1.0 -> 82, 4.0 -> 114. It buys nothing. The channels already carry it.

  Learning the taught board again at 560, 400 and 280 pixels, and again with
  its outline thinned and thickened, so the templates would carry more than
  one sharpness: 40 errors -> 38 to 46, at two to seven times the cost.
  Nothing. The descriptor is already scale-stable enough that a second look
  at the same board says nothing new.

  What the descriptor would be without the parts that cost the most, which
  is the interesting comparison. Magnitude only, directions thrown away
  (64 numbers): 557 errors. The same at 16 cells (256 numbers, the same
  length as the winner): 234. Each channel collapsed onto its row and column
  marginals, so a cell is known by its row and by its column but never by both
  (128 numbers): 220, and 132 at 16 cells. The silhouette alone, where the ink
  starts down each column and how wide it is across each row: 10,551 errors of
  29,696, which is not a descriptor at all. Direction in place beats
  resolution at the same descriptor length by six to one.

  Where it still loses: every one of the 40 remaining top-1 errors is at 280
  or 400 pixels, 30 of them are a queen read as the other side's queen, and
  the margin refuses all 40 before it refuses a single right answer.

The arrow, which is the part this reader had to be told about. overlay.py
paints the coach arrow on the very board being read, and its safety argument
is a brightness one: the colour greys to 165, inside the band read_occupancy
ignores, so the shipped reader cannot see its own arrow. A gradient does not
care what level a step sits at, only that there is one, so that argument is
worth nothing here. Unmasked, on clean 824px plain boards, this reader went
from 32 of 32 whole to 0 of 64 whole with two frames confidently wrong, while
the shipped reader did not move. The same property is available by colour
instead of by brightness; see the block above _arrow_mask for how, and
_unpaint and _keep for what is done with it. Afterwards, on that same slice:

                        no arrow                      arrow drawn
    reader in the tree  24 whole  8 refused  0 wrong  28 whole  36 refused  0 wrong
    this file, before   32 whole  0 refused  0 wrong   0 whole  62 refused  2 WRONG
    this file, after    32 whole  0 refused  0 wrong  34 whole  30 refused  0 wrong

What was tried on the arrow and did not work:

  Weighting a cell by how covered it is instead of dropping it whole, which
  ought to recover the evidence a touched cell still holds. It recovers whole
  boards, 58.1% to 75.2%, and it buys them with 7.29% of boards ACCEPTED
  WRONG. The covered part of a cell is the refill, and the refill is not
  evidence, it is this reader's own paint. Off by default; arrow_soft.

  Scaling the partial margin by how many cells are left rather than one flat
  bar, so a square the arrow clips is not held to the same doubt as one it
  cuts in half: 58.1% whole boards against 58.4%, inside the noise. Nearly
  every covered square in the sweep loses exactly half its cells, so the scale
  factor is a constant wearing a disguise. Off by default; arrow_power.

  Coverage as the signal for whether a covered square that reads empty is
  empty or is a piece the refill erased. It cannot be done: the genuinely
  empty ones cover 0.258 to 0.449 of their square and the erased pieces 0.375
  to 0.438, which is inside it. Energy separates them completely instead, and
  that is what arrow_occ is.

Three more things this file had wrong, none of them about gradients:

  Untaught it answered a whole board of ".", which is not an abstention, it is
  a confident claim that 64 squares are empty, and 45.31% of it was wrong. It
  now loads the bundled sheet the way pieces.py does, holds anything off that
  sheet to the mistrust margin, and answers "?" if even the sheet will not
  load: 0.00% wrong, against the tree reader's 1.74% on the same boards.

  `ready` meant bool(self.templates) where PieceReader means all twelve. Taught
  from a king and rook endgame it left ready True on three templates and
  watcher.check() went ahead off them. Now: 0.00% wrong there, against 14.19%.

  Cross-teaching, taught on one set and shown another, was the weak axis at
  9.86% wrong against the tree reader's 3.02%. See _bar: the scores answer it
  themselves, and it is now 0.00%.
"""

import math
import sys

from PIL import Image, ImageChops, ImageFilter

from overlay import THEIRS, YOURS
from pieces import ORDER, TEMPLATE_PX, TEMPLATE_SHEET
from watcher import grid_of


def _rgb(text):
    text = text.lstrip("#")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


# The two colours this program paints on the board it is reading, read off
# overlay.py rather than copied, so a third one cannot be added there without
# this following it.
ARROW_COLOURS = (_rgb(YOURS), _rgb(THEIRS))

# ---------------------------------------------------------------- kernels

SOBEL_X = (-1.0, 0.0, 1.0, -2.0, 0.0, 2.0, -1.0, 0.0, 1.0)
SOBEL_Y = (-1.0, -2.0, -1.0, 0.0, 0.0, 0.0, 1.0, 2.0, 1.0)
DIFF_X = (0.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 0.0)
DIFF_Y = (0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)
SCHARR_X = (-3.0, 0.0, 3.0, -10.0, 0.0, 10.0, -3.0, 0.0, 3.0)
SCHARR_Y = (-3.0, -10.0, -3.0, 0.0, 0.0, 0.0, 3.0, 10.0, 3.0)

BASES = {"sobel": (SOBEL_X, SOBEL_Y),
         "diff": (DIFF_X, DIFF_Y),
         "scharr": (SCHARR_X, SCHARR_Y)}

RESAMPLE = {"box": None, "lanczos": Image.LANCZOS, "bilinear": Image.BILINEAR,
            "bicubic": Image.BICUBIC, "hamming": Image.HAMMING,
            "nearest": Image.NEAREST}


def _kernels(k, base="sobel"):
    """K half-wave rectified directional derivatives, as Pillow kernels.

    Pillow clips a mode "L" convolution to 0..255, so a kernel with no offset
    keeps only the half of the gradient that points its way and throws the
    other half to zero. That is exactly one signed orientation bin, done in C.
    Each kernel is divided by its own positive weight, so a full black to white
    step perpendicular to it answers 255 whichever way round it points and the
    K channels are on one scale.
    """
    bx, by = BASES[base]
    out = []
    for i in range(k):
        a = 2.0 * math.pi * i / k
        ca, sa = math.cos(a), math.sin(a)
        w = [ca * x + sa * y for x, y in zip(bx, by)]
        gain = sum(v for v in w if v > 0) or 1.0
        out.append(ImageFilter.Kernel((3, 3), w, scale=gain, offset=0))
    return out


_LUTS = {}


def _lut(sharp):
    """value -> value ** sharp on the 0..255 scale, as a byte table."""
    if sharp not in _LUTS:
        _LUTS[sharp] = bytes(int(255.0 * (v / 255.0) ** sharp + 0.5)
                             for v in range(256))
    return _LUTS[sharp]


def _mean(rows):
    """One unit-length descriptor averaging several."""
    n = len(rows)
    v = [sum(col) / n for col in zip(*rows)]
    s = math.sqrt(sum(x * x for x in v)) or 1.0
    return tuple(x / s for x in v)


# ---------------------------------------------------------------- config
#
# Every one of these was swept on bench.py and the curve is in the module
# docstring. The defaults are the best measured combination; the losers are
# kept because a claim about a parameter is worth no more than the run behind
# it, and any of them can be handed to factory() to make that run again.

DEFAULTS = dict(
    px=32,            # canonical pixels per square
    cells=8,          # cells per square, each way
    bins=4,           # gradient directions, over the full circle
    base="sobel",     # which derivative kernel
    pad=8,            # canonical pixels ignored at the square's rim
    sharp=1.0,        # exponent applied to each channel before pooling
    signed=True,      # False folds opposite directions together
    blur=0.0,         # pre-blur of the canonical board, in canonical pixels
    resample="box",   # how the board is brought to canonical scale
    shape="hog",      # hog, mag, profile or edge; see _reduce
    norm="l2",        # descriptor normalisation: l2, l1, cell, none
    clip=0.0,         # cap on any one normalised value, 0 for none
    mag=0.0,          # weight of an added gradient-magnitude block
    flux=0.0,         # weight of an added radial-polarity block
    aug=(),           # extra captures of the taught board to learn from
    aug_mode="keep",  # keep those as templates, or mean them into one
    dedup=0.999,      # drop a template this close in cosine to one held
    sheet=TEMPLATE_SHEET,  # the fallback set, or None to start with nothing
    arrow_alpha=0.80,  # the least opaque the program's own arrow can be
    arrow_tol=0,      # grey levels of slack on each side of the blend box
    arrow_grow=1,     # canonical pixels the mask is widened by
    arrow_soft=False,  # weight cells by how covered they are, not drop them
    arrow_cell=32,    # a cell this covered by arrow is not evidence, of 255
    arrow_keep=0.40,  # and under this share of cells left the square is not
    arrow_refuse=1.01,  # ink under this much arrow is unreadable outright
    occ=0.045,        # a square is empty below this share of the board's peak
    arrow_occ=0.004,  # and this much lower a bar where the arrow has been
    arrow_margin=0.16,  # the margin a partly covered square is held to
    arrow_power=0.0,  # or scale the plain margin by (all cells / left) ** this
    floor=0.45,       # a call under this cosine is refused
    margin=0.06,      # and one this close to another piece is refused
    trust_bar=0.70,   # under this median best score the templates are foreign
    mistrust=0.40,    # the margin templates not off this screen are held to
)

# The trust pair. trust_bar is the median best score under which the templates
# are describing some other screen, and mistrust is the margin they are then
# held to. It separates, and not narrowly: over the full sweep a taught reader
# never drops below 0.7335 on 3,072 boards, and a cross-taught one never
# reaches above 0.6631 on 384. 0.70 sits in that gap. A misfire is cheap by
# construction, since all it can do is refuse squares.
#
# It settles cross-teaching and nothing else. The bundled sheet does not belong
# in it: the sheet was cut from chess.com, so on a chess.com board it scores
# 0.9650 and is right to, and pooling the two questions is what made this look
# unseparable the first time it was measured. Whether templates came off this
# screen is a fact, so it is read off learn() instead.
#
# mistrust at 0.40: the worst wrong call the sheet makes anywhere in the full
# sweep clears its runner up by 0.3130 and the worst a cross-taught reader
# makes by 0.2805, so 0.40 is 28% clear of the nearer of the two. Both come
# out at 0.00% wrong.
#
# dedup is speed only. A starting position holds eight pawns a side and four of
# each stand on each square colour, so learning every instance keeps 32
# templates where 20 different ones exist. Collapsing the duplicates leaves
# every number on the full sweep bit for bit unchanged at 0.999 and at 0.99,
# costs 8 more errors at 0.95, and takes 37% off the matching loop, which is
# where nearly all the time goes.
#
# What the last three are set against, all off the full 65,536 square sweep.
#
# occ: with the rim cropped away an empty square holds no gradient at all, so
# the two populations do not overlap, they do not even come close. The highest
# energy any empty square reaches anywhere in the sweep is 0.0000 of the
# board's peak and the lowest any occupied square reaches is 0.0755. 0.045
# sits in the middle of a gap that wide, and no square in the sweep is decided
# by exactly where in it the number goes. This is the one place the gradient
# answer is not merely better than thresholding pixel values but a different
# kind of answer: there is nothing left to tune.
#
# floor: free rather than useful. The worst-scoring right answer in the sweep
# is 0.5085 and six wrong ones score 0.4169, so 0.45 costs nothing measured
# and catches a little. Raised into the answers it is expensive: 0.90 gives up
# 5.6% of the right calls to buy nothing the margin had not already bought.
#
# margin: this is the whole safety rule. Right/wrong/unknown across it, full
# sweep: 0.03 -> 99.81/0.02/0.17, 0.04 -> 99.79/0.01/0.20,
# 0.05 -> 99.75/0.00/0.25, 0.06 -> 99.60/0.00/0.40, 0.08 -> 99.41/0.00/0.59,
# 0.12 -> 98.54/0.00/1.46. The worst wrong answer in the sweep is clear by
# 0.0473, so 0.05 is exactly the last error and nothing more; 0.06 keeps a
# quarter as much again in reserve for the sets nobody has shown it yet, and
# gives up 0.15% of the right answers to do it.


class GradientReader:
    """The entrant. learn() once per set, classify() per board."""

    def __init__(self, **cfg):
        self.cfg = dict(DEFAULTS)
        self.cfg.update(cfg)
        self.kernels = _kernels(self.cfg["bins"], self.cfg["base"])
        self.templates = {}
        self._plan = None
        self.source = "nothing"
        self.trusted = False
        if self.cfg["sheet"]:
            self.use_bundled()

    @property
    def ready(self):
        """All twelve pieces known, which is what PieceReader.ready means.

        It was bool(self.templates), and that is a different promise: taught
        from a king and rook endgame, learn() returned False while ready stayed
        True on three templates, and watcher.check() went ahead and named a
        quarter of the board wrong off them. Nine of the twelve pieces it could
        be asked about were not in the reader at all.
        """
        return len(self.templates) == 12

    def use_bundled(self):
        """Fill in from the sheet that ships with the program.

        Without this an untaught reader has nothing at all, which is worse than
        it sounds: over 2,048 untaught squares it answered a whole board of "."
        and scored 45.31% wrong, because an empty board is a confident claim
        and not an abstention. PieceReader scores 0.00% there, and the reason
        is this sheet. It is also the fallback under a set the reader was not
        taught, which is where a lone learned set is weakest.

        Loading is all or nothing, the way use_bundled is in pieces.py: a sheet
        that will not read leaves whatever is already held alone rather than
        half replacing it.
        """
        try:
            sheet = Image.open(self.cfg["sheet"]).convert("RGB")
            slots = sheet.size[0] // TEMPLATE_PX
            if slots < len(ORDER):
                raise ValueError("sheet holds fewer than twelve pieces")
            found = {}
            for slot in range(min(slots, 2 * len(ORDER))):
                crop = sheet.crop((slot * TEMPLATE_PX, 0,
                                   (slot + 1) * TEMPLATE_PX, TEMPLATE_PX))
                found.setdefault(ORDER[slot % len(ORDER)],
                                 []).append(self._square_desc(crop))
        except Exception:
            return False
        self.templates = {k: tuple(v) for k, v in found.items()}
        self.source = "bundled"
        self.trusted = False
        return True

    # ------------------------------------------------------------ pixels

    # ------------------------------------------------- the program's arrow
    #
    # overlay.py paints the coach arrow on the very board this is reading, and
    # its safety argument is a brightness one: the colour greys to 165, inside
    # the 70 to 244 band read_occupancy ignores, so the shipped reader cannot
    # see its own arrow. A gradient does not care what level a step sits at,
    # only that there is one, so that argument protects this reader from
    # nothing. Unmasked it reads no board whole at all, and two frames come
    # back confidently wrong.
    #
    # The same property is available by colour instead of by brightness. The
    # arrow is two exact constants, drawn by this program and nothing else,
    # and the window is layered at ALPHA, so a painted pixel is
    #
    #     p = a * arrow + (1 - a) * whatever was underneath
    #
    # for some a at or above that alpha. Underneath is unknown but bounded, so
    # per channel the whole family collapses to one interval,
    # [a*C, a*C + (1-a)*255], and those are nested in a: the interval for the
    # smallest alpha worth allowing contains every larger one. The test is
    # three interval checks, which is three lookup tables and no arithmetic.
    #
    # It is a relaxation, since it lets each channel pick its own a rather than
    # making them agree, and it is still tight enough to be free. Over all 192
    # arrow-free boards of the quick sweep, 12.6 million canonical pixels, it
    # claims zero at 0.85 and zero at 0.70. It only starts costing at 0.55,
    # where the violet box takes 16,685 of them. 0.80 is where it is set: far
    # enough under the 0.85 the overlay uses to survive compositing rounding
    # and a screenshot resample, and nowhere near the 0.55 where real pixels
    # begin.

    def _arrow_mask(self, rgb):
        """255 where a pixel could be this program's own arrow over anything.

        Measured at canonical scale rather than native, which is ten times less
        work and loses nothing: the arrow is 16% of a square wide by
        construction, so it is about five canonical pixels across at every
        board size, its core survives the downsample as arrow colour outright,
        and the rim of it is what arrow_grow is for.
        """
        c = self.cfg
        amin, tol = c["arrow_alpha"], c["arrow_tol"]
        if not amin:
            return None
        solid = bytes(255 if v > 250 else 0 for v in range(256))
        mask = None
        for colour in ARROW_COLOURS:
            lut = []
            for level in colour:
                lo = amin * level - tol
                hi = amin * level + (1.0 - amin) * 255.0 + tol
                lut += [255 if lo <= v <= hi else 0 for v in range(256)]
            # A band that fails its interval is zeroed, and greying a picture
            # whose bands are only 0 or 255 reaches 255 exactly when all three
            # passed, so the three-way and is one convert.
            one = rgb.point(lut).convert("L").point(solid)
            mask = one if mask is None else ImageChops.lighter(mask, one)
        for _ in range(c["arrow_grow"]):
            mask = mask.filter(ImageFilter.MaxFilter(3))
        return mask

    def _unpaint(self, grey, mask):
        """Put the board colour back where the arrow was, square by square.

        Zeroing the arrow away would leave a hole, and a hole is an edge, which
        is the thing this reader is made of. Filling with the square's own
        commonest unmasked level leaves no edge at all where the arrow crossed
        empty board, which is most of where it goes, and leaves a piece the
        shaft crosses looking like a piece with a bite out of it. That is the
        wanted behaviour on both counts: the empty squares come back empty, and
        the bitten piece matches nothing well enough to clear the margin.

        The mode, not the mean and not the median, because the board under the
        arrow is one flat level while a piece is spread over many, so the mode
        still finds the board on a square a piece is filling half of.
        """
        px, pad = self.cfg["px"], self.cfg["pad"]
        keep = ImageChops.invert(mask)
        out = grey.copy()
        cover = [0.0] * 64
        inner = float(max(1, (px - 2 * pad) ** 2))
        for r in range(8):
            for col in range(8):
                box = (col * px, r * px, (col + 1) * px, (r + 1) * px)
                sub = mask.crop(box)
                if sub.getbbox() is None:
                    continue
                seen = sub.crop((pad, pad, px - pad, px - pad)).histogram()
                cover[r * 8 + col] = seen[255] / inner
                hist = grey.crop(box).histogram(keep.crop(box))
                if not sum(hist):
                    continue
                out.paste(Image.new("L", (px, px), hist.index(max(hist))),
                          box, sub)
        return out, cover

    # ------------------------------------------------------------ pixels

    def _convolve(self, work):
        """One canonical-scale picture, as K rectified direction channels."""
        c = self.cfg
        if c["blur"]:
            work = work.filter(ImageFilter.GaussianBlur(c["blur"]))
        lut = None if c["sharp"] == 1.0 else _lut(c["sharp"])
        chans = []
        for k in self.kernels:
            ch = work.filter(k)
            if lut is not None:
                ch = ch.point(lut)
            chans.append(ch)
        return chans

    def _board(self, board_img):
        """The board's K channels, and how much arrow covers each square.

        The resize happens in colour rather than in grey, because the arrow has
        to be found before the colour it is made of is thrown away.
        """
        c = self.cfg
        side = c["px"] * 8
        rgb = board_img.convert("RGB")
        how = RESAMPLE[c["resample"]]
        if how is None:
            how = Image.BOX if rgb.size[0] >= side else Image.BILINEAR
        work = rgb.resize((side, side), how)
        mask = self._arrow_mask(work)
        grey = work.convert("L")
        cover = None
        if mask is None or mask.getbbox() is None:
            mask = None
        else:
            grey, cover = self._unpaint(grey, mask)
        return self._convolve(grey), cover, mask

    def _descs(self, board_img):
        """One raw descriptor per square, in screen order.

        The convolution is done once for the whole board and the pooling per
        square out of the result, because four passes over one 256 pixel image
        cost less than 256 passes over a 32 pixel one, and because the rim each
        square drops is then a crop box rather than a second image.
        """
        c = self.cfg
        px, pad, cells = c["px"], c["pad"], c["cells"]
        chans, cover, mask = self._board(board_img)
        out, cellcov = [], []
        for r in range(8):
            for col in range(8):
                box = (col * px + pad, r * px + pad,
                       (col + 1) * px - pad, (r + 1) * px - pad)
                out.append(self._pool(chans, box))
                # The arrow pooled into the very cells the descriptor is, so a
                # cell can be asked whether it is evidence or paint.
                cellcov.append(None if mask is None else
                               list(mask.resize((cells, cells), Image.BOX, box)
                                    .get_flattened_data()))
        return out, cover or [0.0] * 64, cellcov

    def _pool(self, chans, box):
        """One region of the channels, area-pooled into the cell grid."""
        c = self.cfg
        cells = c["cells"]
        step = cells * cells
        v = []
        for ch in chans:
            v.extend(ch.resize((cells, cells), Image.BOX, box)
                     .get_flattened_data())
        if not c["signed"]:
            half = len(chans) // 2
            v = [v[i] + v[i + half * step]
                 for j in range(half)
                 for i in range(j * step, (j + 1) * step)]
        return v

    def _square_desc(self, square_img):
        """One cropped square, straight to a finished descriptor.

        Only the bundled sheet needs this; a board goes through _descs. The
        3x3 kernel reaching off the edge does not matter, because pad throws
        the rim away before anything is pooled out of it.
        """
        c = self.cfg
        px, pad = c["px"], c["pad"]
        grey = square_img.convert("L")
        how = RESAMPLE[c["resample"]]
        if how is None:
            how = Image.BOX if grey.size[0] >= px else Image.BILINEAR
        chans = self._convolve(grey.resize((px, px), how))
        return self._finish(self._pool(chans, (pad, pad, px - pad, px - pad)))

    # ---------------------------------------------------- the descriptor

    def _flux(self, v):
        """How the ink steps as you move out from the middle of the square.

        A rectified channel is one half of the gradient, so summing the K
        channels back up against their own direction vectors recovers the mean
        gradient in a cell exactly, at no extra convolution. Projected onto the
        direction out of the square's centre it says which way the ink steps
        there: outward-brightening for a dark piece on a lighter board, the
        other way round inside a light piece's fill.

        Built because every colour confusion left on the bench is a queen or a
        king read as the other side's, and measured to be worth nothing: at its
        best weight it leaves the error count exactly where it was and every
        heavier weight makes it worse. The four channels already carry the
        polarity, and weighting one linear combination of them up adds none.
        """
        plan = self._plan
        if plan is None:
            c = self.cfg
            n, cells = c["bins"], c["cells"]
            step = cells * cells
            mid = (cells - 1) / 2.0
            plan = []
            for i in range(step):
                dx, dy = i % cells - mid, i // cells - mid
                r = math.hypot(dx, dy) or 1.0
                plan.append(tuple(
                    (k * step + i,
                     c["flux"] * (math.cos(2.0 * math.pi * k / n) * dx +
                                  math.sin(2.0 * math.pi * k / n) * dy) / r)
                    for k in range(n)))
            self._plan = plan
        return [sum(v[j] * w for j, w in cell) for cell in plan]

    def _reduce(self, v):
        """The cheaper descriptions of the same channels, for comparison.

        "hog" keeps every direction in every cell, which is what this file is
        about. The other three are the ideas worth knowing the price of, and
        the price is in the module docstring.

        "mag" throws the directions away and keeps only how much edge each cell
        holds, which is gradient magnitude correlation rather than intensity
        correlation. It is not enough on its own.

        "profile" keeps the directions but collapses each channel onto its two
        marginals, so a cell is known by its row and by its column and never by
        both. A third of the numbers and five times the errors.

        "edge" is the silhouette by itself: how far down the square the ink
        starts in each column and how wide it is across each row. Centred
        before it is normalised, since every profile carries the same offset
        and only its shape says which piece it is. It fails outright.
        """
        c = self.cfg
        cells = c["cells"]
        step = cells * cells
        blocks = len(v) // step
        m = [sum(v[j * step + i] for j in range(blocks)) for i in range(step)]
        if c["shape"] == "mag":
            return m
        if c["shape"] == "profile":
            out = []
            for j in range(blocks):
                b = v[j * step:(j + 1) * step]
                out.extend(sum(b[r * cells:(r + 1) * cells])
                           for r in range(cells))
                out.extend(sum(b[r * cells + col] for r in range(cells))
                           for col in range(cells))
            return out
        bar = max(m) * 0.15
        top, wide = [], []
        for col in range(cells):
            hit = [r for r in range(cells) if m[r * cells + col] > bar]
            top.append((hit[0] if hit else cells) / cells)
        for r in range(cells):
            hit = [col for col in range(cells) if m[r * cells + col] > bar]
            wide.append(((hit[-1] - hit[0] + 1) if hit else 0) / cells)
        out = top + wide
        mid = sum(out) / len(out)
        return [x - mid for x in out]

    def _finish(self, v):
        """One raw descriptor, scaled the way the config asks.

        Unit length and nothing else, by default. Both of the obvious extras
        cost accuracy rather than buying it: per-cell normalisation before the
        global one, which is HOG's own block step, takes the quick sweep from
        46 errors to 318, and capping any one value takes it to 84. They do the
        same damage, which is to hand a square's empty cells the same weight as
        the cells the piece is actually drawn in.
        """
        c = self.cfg
        if c["shape"] != "hog":
            v = self._reduce(v)
        if c["flux"]:
            v = list(v) + self._flux(v)
        if c["mag"]:
            step = c["cells"] * c["cells"]
            blocks = len(v) // step
            v = list(v) + [c["mag"] * sum(v[j * step + i]
                                          for j in range(blocks))
                           for i in range(step)]
        if c["norm"] == "cell":
            step = c["cells"] * c["cells"]
            blocks = len(v) // step
            w = list(v)
            for i in range(step):
                s = math.sqrt(sum(v[j * step + i] ** 2
                                  for j in range(blocks))) or 1.0
                for j in range(blocks):
                    w[j * step + i] = v[j * step + i] / s
            v = w
        if c["clip"]:
            s = math.sqrt(sum(x * x for x in v)) or 1.0
            v = [min(x / s, c["clip"]) for x in v]
        if c["norm"] == "l1":
            s = sum(abs(x) for x in v) or 1.0
        elif c["norm"] == "none":
            s = 1.0
        else:
            s = math.sqrt(sum(x * x for x in v)) or 1.0
        return tuple(x / s for x in v)

    # ------------------------------------------------------------ entrant

    def _captures(self, board_img):
        """The taught board as it might come back off a screen later.

        A set is taught once, at whatever size the window happened to be, and
        then asked about every other size, so it is worth asking whether a
        template should carry more than that one capture's sharpness.
        Measured: no. Learning the same board again at 560, 400 and 280 pixels,
        or blurred, or with its outline thinned and thickened, leaves the error
        count where it was and costs two to seven times as much. Off by
        default, and kept in the file because that is the run that says so.
        """
        yield board_img
        for how in self.cfg["aug"]:
            if isinstance(how, int):
                yield board_img.resize((how, how), Image.LANCZOS)
            elif how == "blur":
                yield board_img.filter(ImageFilter.GaussianBlur(
                    board_img.size[0] / 800.0))
            elif how == "thin":
                yield board_img.filter(ImageFilter.MinFilter(3)).filter(
                    ImageFilter.MaxFilter(3))
            elif how == "heavy":
                yield board_img.filter(ImageFilter.MaxFilter(3)).filter(
                    ImageFilter.MinFilter(3))

    def learn(self, board_img, board, flipped=False):
        """Learn the templates from a position known to be on screen.

        Both square colours are learned separately, because the rim of a piece
        is the part whose contrast the square under it decides. A starting
        position holds every piece type on both colours except the king and the
        queen, and that is where the errors that are left live.
        """
        grid = grid_of(board, flipped)
        found = {}
        for shot in self._captures(board_img):
            raws = self._descs(shot)[0]
            for r in range(8):
                for col in range(8):
                    sym = grid[r][col]
                    if sym == ".":
                        continue
                    d = self._finish(raws[r * 8 + col])
                    # a8 and h1 are both light, so screen parity says which
                    # colour a square is whichever way round the board is.
                    light = (r + col) % 2 == 0
                    found.setdefault(sym, {}).setdefault(light, []).append(d)
        learned = {}
        for sym, sides in found.items():
            kept = []
            for shots in sides.values():
                if self.cfg["aug_mode"] == "mean":
                    kept.append(_mean(shots))
                    continue
                for d in shots:
                    if all(sum(a * b for a, b in zip(d, t)) < self.cfg["dedup"]
                           for t in kept):
                        kept.append(d)
            learned[sym] = tuple(kept)
        if not learned:
            return False
        # Replace per symbol and never merge, so a bundled template cannot win
        # the max against a piece this screen actually draws differently. The
        # pieces the position did not hold keep whatever they had, which is
        # what PieceReader does and is why a game joined half way through is
        # not stuck on the sheet for its whole life.
        self.templates = dict(self.templates)
        self.templates.update(learned)
        self.source = ("learned from your screen" if len(learned) == 12
                       else "learned in part from your screen")
        self.trusted = len(learned) == 12
        return self.trusted

    def classify(self, board_img):
        """Read the whole board. Eight rows of piece letters, "." or "?".

        The strongest square on the board sets the bar for an empty one, which
        is safe here only because the rim is cropped: with the seam gone an
        empty square holds no gradient at all, and the two populations are
        separated by the whole scale rather than by a threshold.

        A square the scores do not settle comes back "?", never as the best of
        a bad field, because a wrong piece goes into a permanent record and an
        unreadable square is asked again a second later.
        """
        if not self.ready:
            # "?" and not ".", because a board of dots is a claim that the
            # squares are empty and it is believed as one. Untaught, and with
            # the sheet refused as well, there is nothing here to claim it
            # with, and 45.31% of those dots were wrong.
            return [["?"] * 8 for _ in range(8)]
        rows = [["."] * 8 for _ in range(8)]
        c = self.cfg
        raws, cover, cellcov = self._descs(board_img)
        peak = max(sum(v) for v in raws) or 1
        bar = peak * c["occ"]
        items = list(self.templates.items())
        width = c["bins"] * c["cells"] * c["cells"]
        scored = []
        for r in range(8):
            for col in range(8):
                i = r * 8 + col
                raw = raws[i]
                painted = cover[i] > 0.0
                # A square the arrow has been over gets a far lower bar for
                # being empty, because the refill has already taken most of
                # what was there away. Six pawns on the full sweep were erased
                # down under the ordinary bar and came back as empty board,
                # which is the worst answer available: a confident claim that
                # a square holds nothing. The two populations do separate, and
                # not narrowly. Over 4,230 covered squares that read empty, all
                # 4,224 genuinely empty ones carry exactly no gradient at all,
                # and the six erased pawns carry 0.0111 to 0.0403. The bar sits
                # eleven times under the lowest of those, which is as much room
                # as there is anywhere to put it.
                if sum(raw) < (c["arrow_occ"] if painted else c["occ"]) * peak:
                    continue
                if sum(raw) < bar and not painted:
                    continue
                d = self._finish(raw)
                keep = None
                if painted:
                    if cover[i] > c["arrow_refuse"] or len(d) != width:
                        rows[r][col] = "?"
                        continue
                    keep = self._keep(cellcov[i])
                    if keep is None:
                        rows[r][col] = "?"
                        continue
                got = self._rank(d, items, keep)
                if got is None:
                    rows[r][col] = "?"
                    continue
                pick, best, second = got
                share = (1.0 if keep is None else
                         len(keep) / float(len(d)))
                scored.append((r, col, pick, best, best - second,
                               painted, share))
        floor, margin = c["floor"], self._bar(scored)
        # A partly covered square is judged on fewer cells, and a cosine over
        # fewer dimensions is a higher number for everything, so the same
        # margin is not the same test. Held to 0.06 like a whole square, seven
        # squares on the full sweep clear it with the wrong piece, by as much
        # as 0.13; at 0.15 none of them do.
        painted_margin = max(margin, c["arrow_margin"])
        for r, col, pick, best, gap, painted, share in scored:
            if not painted:
                want = margin
            elif c["arrow_power"]:
                # Scaling with how much is left rather than one flat bar, so a
                # square the arrow only clips is not held to the same doubt as
                # one it cuts in half. Measured below, and it is not worth it.
                want = max(margin,
                           margin * (1.0 / max(share, 1e-6)) ** c["arrow_power"])
            else:
                want = painted_margin
            rows[r][col] = "?" if best < floor or gap < want else pick
        return rows

    def _keep(self, cellcov):
        """Which descriptor slots are still evidence, or None for too few.

        A square the arrow crosses is not unreadable, it is partly covered, and
        the part it is not covering is a perfectly good piece of a piece. So
        the cells the paint went through are dropped and the cosine is taken
        over what is left, both sides renormalised onto the same cells. That is
        the whole idea of the descriptor applied one level down: compare where
        there is evidence, and refuse when there is not enough of it.

        Refusing the square outright instead is the safe answer and it is also
        the useless one. The arrow always starts on the square the piece it is
        advising about is standing on, so refusing means every coached frame
        loses at least one square, and watcher.check() throws a frame away
        whole for one blank. Measured, that is the difference between reading
        39.2% of the boards and reading 96.9% of them, at no cost in wrong
        answers.
        """
        c = self.cfg
        step = c["cells"] * c["cells"]
        if c["arrow_soft"]:
            # A cell a third covered still holds two thirds of a piece. Tried
            # because the squares still being refused are pawns with exactly
            # half their cells dropped, and dropping a cell for being touched
            # throws away everything else in it.
            w = [max(0.0, 1.0 - v / 255.0) for v in cellcov]
            if sum(w) < c["arrow_keep"] * step:
                return None
            return [w[i] for j in range(c["bins"]) for i in range(step)]
        live = [i for i in range(step) if cellcov[i] <= c["arrow_cell"]]
        if len(live) < c["arrow_keep"] * step:
            return None
        return tuple(j * step + i for j in range(c["bins"]) for i in live)

    def _rank(self, d, items, keep=None):
        """Best piece, its score and the runner up, over all cells or some.

        Restricted to `keep` the two sides are renormalised onto those cells
        only, because a template that happens to put more of its ink outside
        the visible part would otherwise lose for having been covered up.
        """
        soft = keep is not None and self.cfg["arrow_soft"]
        if soft:
            wd = [w * x for w, x in zip(keep, d)]
            scale = math.sqrt(sum(x * y for x, y in zip(wd, d)))
            if not scale:
                return None
            wd = [x / scale for x in wd]
        elif keep is not None:
            d = [d[k] for k in keep]
            scale = math.sqrt(sum(x * x for x in d))
            if not scale:
                return None
            d = [x / scale for x in d]
        best = second = -1.0
        pick = None
        for sym, variants in items:
            top = -1.0
            for t in variants:
                if keep is None:
                    v = sum(a * b for a, b in zip(d, t))
                elif soft:
                    norm = math.sqrt(sum(w * x * x
                                         for w, x in zip(keep, t)))
                    v = (sum(a * b for a, b in zip(wd, t)) / norm
                         if norm else 0.0)
                else:
                    t = [t[k] for k in keep]
                    scale = math.sqrt(sum(x * x for x in t))
                    v = (sum(a * b for a, b in zip(d, t)) / scale
                         if scale else 0.0)
                top = max(top, v)
            if top > best:
                second, best, pick = best, top, sym
            elif top > second:
                second = top
        return pick, best, second

    def _bar(self, scored):
        """The margin this board earns, which is not the same on every board.

        Templates that came off the bundled sheet describe somebody else's
        screen, and the best of a bad field out of them is a guess. Measured on
        the untaught sheet alone: at the taught margin of 0.06 it names 60.49%
        of squares right and 12.72% WRONG, and the wrong ones do not go away
        until 0.20. 0.30 is where it is set, which leaves 15.80% right and
        nothing wrong, and clears the worst wrong call in that run by a third.
        That is the trade pieces.py makes with MISTRUST_OVERLAP and it is the
        right one: a fallback exists to keep the reader honest when it has not
        been taught, not to read the board for it.

        Whether the templates are off this screen is a fact and not a guess, so
        it is read off learn() rather than inferred. The inference was tried:
        take the median best score over the occupied squares, on the theory
        that foreign templates fit nothing well. It does not separate. Over the
        full sweep a taught reader drops to 0.7335 on the hardest captures
        while an untaught one reaches 0.9650, which is the bundled sheet
        looking at chess.com, the set it was cut from, and matching it
        properly. Two different questions land on one number. trust_bar keeps
        the test available and is off, because the measurement says it cannot
        be set anywhere useful.
        """
        c = self.cfg
        if not self.trusted:
            return c["mistrust"]
        if c["trust_bar"] and scored:
            # Covered squares are scored on fewer cells and their cosines are
            # not on the same scale, so they are left out of the vote rather
            # than allowed to decide it.
            best = sorted(x[3] for x in scored if not x[5]) or [1.0]
            if best[len(best) // 2] < c["trust_bar"]:
                return c["mistrust"]
        return c["margin"]


def factory(**cfg):
    """An entrant bench.score can call, with any of DEFAULTS overridden."""
    return lambda: GradientReader(**cfg)


def entrant():
    return GradientReader()


if __name__ == "__main__":
    import bench
    print("scoring the gradient reader")
    bench.score(entrant, quick="--quick" in sys.argv)
