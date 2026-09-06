"""Telling pieces apart by what the piece IS, not by what it looks like.

Every piece set draws the same twelve things. A rook is crenellated and flat
topped, a bishop is one peak with a slit, a knight faces sideways and is the
only piece that is not mirror symmetric, a pawn is the smallest and roundest
thing on the board. Those are facts about the object. The pixels that carry
them are not, and a template match compares the pixels.

So this entrant throws the pixels away. A square is reduced to a silhouette,
the silhouette is registered onto its own bounding box, and what comes out is
a few dozen numbers about the shape: how tall against how wide, how much of
its box it fills, where its weight sits, its second moments, the height of
its top edge across eight columns, its width down eight rows, how far its ink
reaches left and right of centre, how asymmetric it is under a mirror, and
its Euler number. Classification is nearest prototype over those numbers with
a margin, so a square whose shape does not settle comes back "?".

Two segmentations are on offer and both are measured, because the first thing
the numbers said was that most of the damage is not identification at all.

  "shared"  pieces.py's own cutoffs, imported rather than copied, so that the
            only difference between this entrant and the baseline is the
            matcher. Its cutoffs are anchored on the darkest and brightest ink
            the board actually carries, which is what makes them survive a
            brightness knob, and it is also what makes them collapse under
            blur: a blurred board's ink no longer reaches, both layers empty
            and 206 pieces of the quick corpus reduce to nothing at all.

  "band"    the same two-sided idea anchored on the two square colours
            instead: ink is anything more than BAND of the gap between the
            light and dark square away from both of them. Affine in pixel
            values exactly as the shared one is, so it survives the same
            brightness and contrast changes, but nothing about it depends on
            how far the ink reaches, so blur costs it a thinner silhouette
            rather than no silhouette. Seams stay safe by construction: a
            pixel on the join between two squares is a blend of the two
            square colours and so sits between the cutoffs, never outside
            them.

Every number in this file comes from bench.py and from nothing else.

    quick corpus, 12,288 squares      right    WRONG   unknown
      pieces.py, the reader in tree   89.18     2.05      8.77
      this file                       96.68     0.00      3.32

    full corpus, 65,536 squares       right    WRONG   unknown
      pieces.py, the reader in tree   86.48     2.74     10.78
      this file                       94.17     0.34      5.49

The full corpus is the honest test of the two: five of its eight variants and
two of its four sizes were never looked at while any of this was being tuned.
It costs 1.5 to 1.7 times a board, 29.4 ms against 19.1 ms at 824px, and 63 to
119 ms once per game to learn.

The findings, including the two the shape argument does not survive, are at
the bottom of this file.
"""

from PIL import Image, ImageChops, ImageFilter

import pieces as P
from watcher import MIN_COVERAGE, grid_of

# ---------------------------------------------------------------- geometry

R = 32                    # the grid every silhouette is compared on
RB = R // 8
BITS = R * R
BANDS = 8                 # columns/rows a profile is summarised into
WIDE = R // BANDS

# A grid cell counts as piece when this much of the native pixels under it
# were. Thresholding happens at native resolution and the binary layer is
# area-downsampled, so a cell holds a real coverage fraction rather than one
# sampled pixel. Well under half because an outline is the part worth keeping
# and an outline two native pixels wide covers well under half a cell.
FILL = 96
# A second, much lighter reading of the same coverage, used only for holes.
# At FILL an antialiased edge shatters into specks and the hole count becomes
# a count of the antialiasing.
SOLID = 32

# How far from both square colours a pixel has to be to count as ink, as a
# fraction of the gap between them. See the "band" note above. It has a floor
# under it: a LANCZOS downsample rings at every seam, overshooting past both
# square colours, and pieces.py measures that ringing at 0.108 of the gap on a
# board with nothing on it.
BAND = 0.23

# and the margin is capped by the room there actually is on that side of the
# board colour. Without the cap the bright cutoff on chess.com's own green
# lands at 256.5 on a 0-255 scale, which no pixel can reach, and every white
# piece is then found only by its dark outline: blurred at 400px that outline
# has washed out to grey 153 against a dark cutoff of 107, so 156 white pieces
# of the quick corpus reduce to an empty square. The headroom is measured
# against the encoding rather than against the ink the board carries, because
# the ink is exactly what blur takes away.
CAP = 0.55

# How little ink a square can hold and still be called empty. Lower than
# watcher's MIN_COVERAGE on purpose. Measured over the quick corpus, a square
# that really is empty never exceeds 0.0010 of the grid while pieces the
# segmentation has half eaten sit anywhere from 0 to 0.0146, so the watcher
# floor reads a hundred real pieces as empty squares, and an empty square is a
# wrong answer where "?" would not be.
EMPTY = 0.0025

# Whether a square is empty is a different question from what is on it, and
# it wants a different measurement. Ink needs a threshold far enough from the
# board colour to be sure it is ink; occupancy only needs to know the square
# is not one flat colour, which is much easier to see. So the middle of the
# square is downsampled and its greys compared end to end: a piece the ink
# cutoffs have eaten still leaves structure behind, and "?" on a square that
# holds something unreadable is the cheap answer where "empty" is the
# expensive one.
#
# Only the middle, at OCC_INSET in from each edge, for two reasons that both
# matter off this bench. A board draws its rank and file labels in the corner
# of the outer squares, and the join between two squares is a gradient from
# one square colour to the other; both are structure and neither is a piece.
# A last-move highlight washes the whole square in one flat colour and so does
# not move this at all, which is the point of measuring spread rather than
# level.
# 0.10 is the middle of the gap the measurement leaves. Over the whole 65,536
# square corpus every square that really is empty measures 0.0000 and every
# piece the ink cutoffs ate measures at least 0.2157, so anything in between
# separates them outright and the middle is the most room on both sides.
OCC_INSET = 0.16
OCC_RANGE = 0.10

_FILL_TABLE = bytes(255 if v >= FILL else 0 for v in range(256))
_SOLID_TABLE = bytes(255 if v >= SOLID else 0 for v in range(256))

# Column bands held as full-height masks, so the height of the topmost piece
# pixel in a band is one AND and one bit_length rather than a scan.
_COL_BANDS = []
for _j in range(BANDS):
    _cols = 0
    for _c in range(_j * WIDE, (_j + 1) * WIDE):
        _cols |= 1 << (R - 1 - _c)
    _band = 0
    for _r in range(R):
        _band |= _cols << ((R - 1 - _r) * R)
    _COL_BANDS.append(_band)

_ROW_ALL = (1 << R) - 1
_WINDOW = 0
for _r in range(R - 1):
    _WINDOW |= (_ROW_ALL ^ 1) << ((R - 1 - _r) * R)

# x*y as an image, so the cross moment is one C multiply and one C reduce
# instead of a thousand Python steps.
_RAMP_XY = Image.frombytes("L", (R, R), bytes(
    max(0, min(255, int(round(255.0 * (x / (R - 1.0)) * (y / (R - 1.0))))))
    for y in range(R) for x in range(R)))


def _euler(mask):
    """Objects minus holes, from the 2x2 patterns the grid contains.

    Same counting as pieces._euler, and the same warning applies. At 32 cells
    across this does not count the hole in a king's cross, it counts the gap
    between a piece's fill and its outline, which is a property of the drawing
    as much as of the piece.
    """
    a = mask & _WINDOW
    b = (mask << 1) & _WINDOW
    c = (mask << R) & _WINDOW
    d = (mask << (R + 1)) & _WINDOW
    na, nb, nc, nd = a ^ _WINDOW, b ^ _WINDOW, c ^ _WINDOW, d ^ _WINDOW
    one = ((a & nb & nc & nd) | (na & b & nc & nd) |
           (na & nb & c & nd) | (na & nb & nc & d))
    three = ((a & b & c & nd) | (a & b & nc & d) |
             (a & nb & c & d) | (na & b & c & d))
    diag = (a & nb & nc & d) | (na & b & c & nd)
    return (one.bit_count() - three.bit_count() - 2 * diag.bit_count()) // 4


# --------------------------------------------------------- the descriptors
#
# NAMES, SCALES and GROUPS run in step with the vector _shape builds. A scale
# is the difference at which that descriptor stops saying anything, so the
# distance in each dimension is |a-b|/scale clipped at 1. GROUPS is what the
# ablation switches on and off.

NAMES = []
SCALES = []
GROUPS = []


def _slot(name, scale, group, n=1):
    for i in range(n):
        NAMES.append(name if n == 1 else "%s%d" % (name, i))
        SCALES.append(scale)
        GROUPS.append(group)


_slot("aspect", 0.35, "box")
_slot("tall", 0.25, "square")
_slot("wide", 0.25, "square")
_slot("baseline", 0.20, "square")
_slot("fill", 0.20, "box")
_slot("cx", 0.10, "moment")
_slot("cy", 0.12, "moment")
_slot("mu20", 0.030, "moment")
_slot("mu02", 0.030, "moment")
_slot("mu11", 0.020, "moment")
_slot("top", 0.22, "top", BANDS)
_slot("bot", 0.22, "bot", BANDS)
_slot("wid", 0.25, "widthprof", BANDS)
_slot("lef", 0.25, "sideprof", BANDS)
_slot("rig", 0.25, "sideprof", BANDS)
_slot("rowfill", 0.28, "rowfill", BANDS)
_slot("colfill", 0.28, "colfill", BANDS)
_slot("asym", 0.16, "asym")
_slot("euler", 14.0, "euler")
# The colour four. None of these is a shape, and none of them is compared
# against a fixed number either: which drawing of a shape is the light one is
# a property of the set, so they are only ever compared against this set's own
# prototypes. See _shape for what each is.
_slot("share", 0.40, "colour")
_slot("tone", 0.45, "colour")
_slot("core", 0.45, "colour")
_slot("inkmax", 0.40, "colour")
_slot("inkmin", 0.40, "colour")

NAMES = tuple(NAMES)
SCALES = tuple(SCALES)
GROUPS = tuple(GROUPS)
DIM = len(NAMES)
ALL_GROUPS = tuple(sorted(set(GROUPS)))


class Shape:
    """One square as the numbers.

    vec is None when nothing could be measured. `busy` says whether the square
    holds structure at all, which is what separates "empty" from "something is
    here that I cannot read".
    """

    __slots__ = ("vec", "coverage", "busy", "colour")

    def __init__(self, vec, coverage, busy, colour):
        self.vec = vec
        self.coverage = coverage
        self.busy = busy
        self.colour = colour


def _cut(img, table):
    return Image.frombytes("L", img.size, img.tobytes().translate(table))


def _bits(cut):
    return cut.convert("1", dither=Image.Dither.NONE).tobytes()


def _mean(img):
    return img.resize((1, 1), Image.BOX).getpixel((0, 0))


def _profiles(packed, whole):
    """Top edge, bottom edge, row width and how far the ink reaches from each
    side, each summarised into BANDS numbers in units of the registered grid.

    The top edge across the width and the width down the height are the two
    signals the shape argument rests on. A rook's top edge is flat with
    notches, a bishop's is one peak, a knight's runs diagonally; a pawn's
    width profile is a narrow neck over a small foot where a queen's flares.
    """
    top, bot = [], []
    for band in _COL_BANDS:
        hit = whole & band
        if hit:
            top.append(((BITS - hit.bit_length()) // R) / R)
            bot.append((R - 1 - ((hit & -hit).bit_length() - 1) // R) / R)
        else:
            top.append(1.0)
            bot.append(1.0)
    lef, rig, wid = [], [], []
    for j in range(BANDS):
        row = 0
        for r in range(j * WIDE, (j + 1) * WIDE):
            row |= int.from_bytes(packed[r * RB:(r + 1) * RB], "big")
        if row:
            left = R - row.bit_length()
            right = R - 1 - ((row & -row).bit_length() - 1)
            lef.append(left / R)
            rig.append((R - 1 - right) / R)
            wid.append((right - left + 1) / R)
        else:
            lef.append(0.5)
            rig.append(0.5)
            wid.append(0.0)
    return top, bot, wid, lef, rig


def _shape(bright, dark, grey, side, lo, hi):
    """Reduce one square's two ink layers to a Shape."""
    span = float(max(1, hi - lo))

    # Is anything here at all? Spread rather than level, over the middle of
    # the square only. See OCC_INSET.
    fine = grey.resize((R, R), Image.BOX)
    edge = int(round(R * OCC_INSET))
    middle = fine.crop((edge, edge, R - edge, R - edge))
    lowest, highest = middle.getextrema()
    busy = (highest - lowest) / span

    plain = bright.getbbox()
    other = dark.getbbox()
    if plain is None:
        plain = other
    elif other is not None:
        plain = (min(plain[0], other[0]), min(plain[1], other[1]),
                 max(plain[2], other[2]), max(plain[3], other[3]))
    if plain is None:
        return Shape(None, 0.0, busy, None)

    # Coverage and the ink share come off the whole square, not off the box.
    bg = _cut(bright.resize((R, R), Image.BOX), _FILL_TABLE)
    dg = _cut(dark.resize((R, R), Image.BOX), _FILL_TABLE)
    nb = int.from_bytes(_bits(bg), "big").bit_count()
    nd = int.from_bytes(_bits(dg), "big").bit_count()
    coverage = (nb + nd) / BITS
    share = nb / (nb + nd) if nb + nd else 0.0
    if coverage < EMPTY:
        return Shape(None, coverage, busy, None)

    # Where the piece is in its square. A box taken straight off the native
    # layers is at the mercy of one stray pixel, so cells of the coarse grid
    # that are barely covered are dropped and the box is re-measured at native
    # resolution inside what survives.
    coarse = ImageChops.lighter(bg, dg)
    solid = ImageChops.multiply(ImageChops.lighter(bright, dark),
                                coarse.resize(bright.size, Image.NEAREST))
    box = solid.getbbox() or plain
    width = max(1, box[2] - box[0])
    height = max(1, box[3] - box[1])

    # Registered: the silhouette on its own bounding box and nothing else.
    reg = solid.crop(box).resize((R, R), Image.BOX)
    cut = _cut(reg, _FILL_TABLE)
    packed = _bits(cut)
    whole = int.from_bytes(packed, "big")
    area = whole.bit_count()
    if not area:
        return Shape(None, coverage, busy, None)

    # How light or dark the piece's own ink is, on the scale the two square
    # colours set, measured four ways because no one of them survives
    # everything. `tone` is the mean grey under the whole silhouette; `core`
    # the same under a silhouette eroded by one cell, which drops the outline
    # and leaves the body; `inkmax` and `inkmin` the ends of the range the ink
    # reaches. Counting bright pixels against dark ones, which is what the
    # baseline does, is the one blur takes away outright: a blurred white rook
    # keeps no pixel brighter than the light square at all and reads as solid
    # black.
    greyreg = grey.crop(box).resize((R, R), Image.BOX)
    inside = ImageChops.multiply(greyreg, cut)
    covered = area / float(BITS)
    tone = ((_mean(inside) / covered) - lo) / span
    core = cut.filter(ImageFilter.MinFilter(3))
    ncore = int.from_bytes(_bits(core), "big").bit_count()
    if ncore:
        core_tone = ((_mean(ImageChops.multiply(greyreg, core))
                      / (ncore / float(BITS))) - lo) / span
    else:
        core_tone = tone
    inkmax = (inside.getextrema()[1] - lo) / span
    inkmin = (ImageChops.lighter(greyreg, ImageChops.invert(cut))
              .getextrema()[0] - lo) / span

    rows = [int.from_bytes(packed[r * RB:(r + 1) * RB], "big").bit_count()
            for r in range(R)]
    cols = list(cut.resize((R, 1), Image.BOX).getdata())
    colsum = sum(cols) or 1

    m00 = float(area)
    cy = sum(r * n for r, n in enumerate(rows)) / m00 / (R - 1)
    cx = sum(c * v for c, v in enumerate(cols)) / colsum / (R - 1)
    m02 = sum(r * r * n for r, n in enumerate(rows)) / m00 / (R - 1) ** 2
    m20 = sum(c * c * v for c, v in enumerate(cols)) / colsum / (R - 1) ** 2
    m11 = (_mean(ImageChops.multiply(cut, _RAMP_XY)) / 255.0) * BITS / m00
    mu20 = max(0.0, m20 - cx * cx)
    mu02 = max(0.0, m02 - cy * cy)
    mu11 = m11 - cx * cy

    top, bot, wid, lef, rig = _profiles(packed, whole)
    rowfill = [v / 255.0 for v in cut.resize((1, BANDS), Image.BOX).getdata()]
    colfill = [v / 255.0 for v in cut.resize((BANDS, 1), Image.BOX).getdata()]

    # The knight is the only piece of the twelve that is not mirror
    # symmetric, so one number ought to isolate it outright. Measured on the
    # registered silhouette, because the bounding box is what makes a mirror
    # meaningful without a centre of mass to guess at.
    mirror = int.from_bytes(
        _bits(cut.transpose(Image.Transpose.FLIP_LEFT_RIGHT)), "big")
    union = (whole | mirror).bit_count()
    asym = ((whole ^ mirror).bit_count() / union) if union else 0.0

    holes = _euler(int.from_bytes(_bits(_cut(reg, _SOLID_TABLE)), "big"))

    vec = ([height / width,
            height / side,
            width / side,
            (side - box[3]) / side,
            area / BITS,
            cx, cy, mu20, mu02, mu11]
           + top + bot + wid + lef + rig + rowfill + colfill
           + [asym, float(holes), share, tone, core_tone, inkmax, inkmin])
    return Shape(tuple(vec), coverage, busy, None)


def _layers(board_img, seg="dark", band=BAND, cap=CAP):
    """The board's two ink layers and its two square greys."""
    levels = P._levels(board_img)
    lo, hi = levels[0], levels[1]
    grey = board_img.convert("L")
    raw = grey.tobytes()
    if seg == "shared":
        bright_table, dark_table = P._tables(levels)
    else:
        margin = band * max(1, hi - lo)
        top = hi + min(margin, cap * (255 - hi))
        bottom = lo - min(margin, cap * lo)
        # "dark": the silhouette is the piece's dark ink alone and the bright
        # layer is dropped. Not an economy, a measured result. Every set here
        # draws a light piece as a light body inside a dark edge, so the dark
        # layer holds a ring for a light piece and a solid body for a dark
        # one, and that difference is most of what tells the two apart.
        # Unioning the bright layer back in merges the ring and the solid into
        # the same blob: 95.07 right and 0.14 wrong becomes 91.40 and 1.85,
        # and deciding colour separately from the four colour descriptors only
        # gets it back to 93.29 and 0.29. The cost is a set that draws its
        # light pieces with no dark edge at all, which would leave them
        # invisible; the flatness test is what stops that being a wrong
        # answer rather than a "?".
        bright_table = bytes(256) if seg == "dark" else bytes(
            255 if v > top else 0 for v in range(256))
        dark_table = bytes(255 if v < bottom else 0 for v in range(256))
    return (Image.frombytes("L", grey.size, raw.translate(bright_table)),
            Image.frombytes("L", grey.size, raw.translate(dark_table)),
            grey, lo, hi)


def board_shapes(board_img, seg="dark", band=BAND, cap=CAP):
    """Every square of a board reduced at once."""
    bright, dark, grey, lo, hi = _layers(board_img, seg, band, cap)
    step = board_img.size[0] / 8.0
    out = []
    for r in range(8):
        for c in range(8):
            box = (int(c * step), int(r * step),
                   int((c + 1) * step), int((r + 1) * step))
            out.append(_shape(bright.crop(box), dark.crop(box),
                              grey.crop(box), max(1, box[3] - box[1]), lo, hi))
    return out


# ---------------------------------------------------------- the classifier

# How close two shapes have to be, and how much clearer the winner has to be
# than the best *other piece type*, before a square is named at all. Both on
# the same 0-to-1 scale as the distance. A king beating a queen is the reader
# being unsure what it is looking at; a white knight beating a black one is
# one shape scored twice and is not.
# The margin turns out to do almost nothing once the prototypes cover the
# scales: over the quick corpus every value from 0 to 0.04 gives the same
# three numbers to two decimal places, and the floor is what does the
# refusing. It is kept because it is the only thing standing between two
# genuinely alike shapes, and that case is not in this corpus.
MAX_DIST = 0.24
MIN_MARGIN = 0.02

# The taught board seen at smaller window sizes, as fractions of the size it
# arrived at. The bench teaches one starting position at the largest size and
# then asks about every size, "because that is what happens when a window is
# resized", so the reader may as well look at the piece it was shown the way
# it will be asked about it. It is the single biggest thing in this file: with
# one view the reader reads 95.07 right and 0.14 wrong, with these four it
# reads 96.09 right and not one wrong answer in 12,288 squares. One prototype
# is kept per view and per square colour, so twelve piece types come to at
# most ninety-six prototypes.
VIEWS = (1.0, 0.68, 0.48, 0.34)

# What the descriptors are actually worth, measured rather than argued. Each
# group was dropped in turn and the elimination kept the drop that cost least,
# and it ran all the way down to these two: how much of each horizontal band
# of the registered box is filled, and how far the ink reaches in from the
# left and from the right in each band. Twenty-four numbers, and they beat all
# seventy-three, 96.68 right with no wrong answer against 96.18 and 0.02. The
# Euler number, the mirror asymmetry, the moments, the aspect ratio and the
# top-edge profile are all either carried by these two or are noise; see the
# findings at the bottom of this file.
LEAN = ("rowfill", "sideprof")


def live(groups):
    """The dimensions these groups switch on, and one over their scales."""
    keep = [i for i, g in enumerate(GROUPS) if g in groups]
    return tuple(keep), tuple(1.0 / SCALES[i] for i in keep)


def distance(a, b, inv):
    """Mean over the live dimensions of |a-b|/scale, each clipped at one.

    Clipped, so a descriptor one bad capture has ruined outright costs its own
    dimension and no more. Averaged rather than summed, so the answer stays on
    a 0-to-1 scale whatever is switched on and MAX_DIST means the same thing
    across every ablation in the report.
    """
    tot = 0.0
    for x, y, s in zip(a, b, inv):
        d = (x - y) * s
        if d < 0:
            d = -d
        tot += d if d < 1.0 else 1.0
    return tot / len(inv)


class ShapeReader:
    """Nearest prototype over the shape descriptors, with a refusal.

    Prototypes come from learn(), which is the one starting position the
    program sees when a game begins, seen at four window sizes: up to eight
    per piece type, one per size and square colour. Their distances are
    minimised over rather than compared, because which size the window was and
    which colour a piece happens to be standing on are not things to be
    uncertain between.
    """

    def __init__(self, groups=LEAN, max_dist=MAX_DIST, margin=MIN_MARGIN,
                 seg="dark", band=BAND, cap=CAP, occ_range=OCC_RANGE,
                 views=VIEWS, prototypes=None):
        self.groups = tuple(groups)
        self.views = tuple(views)
        self.max_dist = max_dist
        self.margin = margin
        self.seg = seg
        self.band = band
        self.cap = cap
        self.occ_range = occ_range
        self.keep, self.inv = live(self.groups)
        self._trimmed = None
        self.protos = prototypes or {}

    @property
    def ready(self):
        return len(self.protos) == 12

    @property
    def trimmed(self):
        """The prototypes cut down to the live dimensions, done once rather
        than once per square."""
        got = self._trimmed
        if got is None or got[0] is not self.protos:
            self._trimmed = got = (self.protos, {
                sym: [tuple(p[i] for i in self.keep) for p in variants]
                for sym, variants in self.protos.items()})
        return got[1]

    def learn(self, board_img, board, flipped=False):
        """Fit the prototypes to one position known to be on screen.

        The board is looked at once at the size it arrived and once at each of
        VIEWS, because a piece drawn into fifty pixels is not the same
        silhouette as the same piece drawn into a hundred and the reader will
        be asked about both. One prototype per view and per square colour: a
        piece learned on a light square and the same piece learned on a dark
        one are two renderings of one thing and are never compared against
        each other.
        """
        grid = grid_of(board, flipped)
        wide = board_img.size[0]
        found = {}
        for j, fraction in enumerate(self.views):
            if fraction >= 1.0:
                view = board_img
            else:
                side = max(64, int(wide * fraction) // 8 * 8)
                view = board_img.resize((side, side), Image.LANCZOS)
            for i, shape in enumerate(
                    board_shapes(view, self.seg, self.band, self.cap)):
                symbol = grid[i // 8][i % 8]
                if symbol == "." or shape is None or shape.vec is None:
                    continue
                light = ((i // 8) + (i % 8)) % 2 == 0
                found.setdefault(symbol, {}).setdefault((j, light), shape)
        self.protos = {s: [t.vec for t in v.values()]
                       for s, v in found.items() if v}
        return len(self.protos) == 12

    def _name(self, vec):
        v = tuple(vec[i] for i in self.keep)
        ranked = sorted(
            (min(distance(v, p, self.inv) for p in variants), symbol)
            for symbol, variants in self.trimmed.items())
        dist, best = ranked[0]
        # The runner up is the best score belonging to a different piece
        # type. A bishop beating a knight is the reader being unsure what it
        # is looking at; a white knight beating a black one is one shape
        # scored twice.
        rival = next((d for d, sym in ranked[1:]
                      if sym.lower() != best.lower()), 1.0)
        if dist > self.max_dist or rival - dist < self.margin:
            return None
        return best

    def classify(self, board_img):
        rows = [["."] * 8 for _ in range(8)]
        if not self.ready:
            return rows
        for i, shape in enumerate(
                board_shapes(board_img, self.seg, self.band, self.cap)):
            if shape is None:
                continue
            if shape.vec is None:
                # Nothing readable. Empty only if the square is also flat;
                # otherwise something is there that could not be reduced, and
                # that is a "?" rather than an empty square.
                if shape.busy >= self.occ_range:
                    rows[i // 8][i % 8] = "?"
                continue
            rows[i // 8][i % 8] = self._name(shape.vec) or "?"
        return rows


def entrant():
    return ShapeReader()


# ------------------------------------------------------------- the findings
#
# What beat the baseline, in the order the measurements said it.
#
#   quick corpus, 12,288 squares                right   WRONG  unknown
#     pieces.py, the reader in the tree         89.18    2.05     8.77
#     these descriptors on pieces.py's cutoffs  88.41    2.18     9.41
#     the band cutoffs, both ink layers         92.39    3.39     4.22
#     the dark layer alone                      95.07    0.24     4.69
#       + the taught board at four sizes        96.68    0.00     3.32
#       + all 73 descriptors instead of 24      96.18    0.02     3.81
#       final, minus the flatness test          96.68    1.32     2.00
#
# Read the second row first. Holding the segmentation fixed and swapping a
# template match for these descriptors LOSES, 88.41 against 89.18 and slightly
# more wrong answers. Nothing in this file beats the baseline on the strength
# of the shape argument. What beats it is three things that are not about
# shape at all: where the ink cutoffs go, keeping only the dark layer, and
# showing the reader the piece at the sizes it will be asked about.
#
# The descriptors, one group at a time, scored on the 5,568 OCCUPIED squares
# of the quick corpus so that the empty half cannot flatter them:
#
#     rowfill    86.85 right   1.13 wrong    row bands of the registered box
#     colfill    84.95         2.60          column bands of it
#     moment     78.27         3.84          cx cy mu20 mu02 mu11
#     box        76.10         9.07          aspect ratio and box fill
#     sideprof   64.83        27.98          how far ink reaches from each side
#     widthprof  63.09        29.36          row width down the height
#     top        53.88        33.10          the top-edge height profile
#     square     54.02        28.77          span of the square, and baseline
#     colour     21.30        18.21          share, tone, core, inkmax, inkmin
#     asym       18.52        30.84          mirror disagreement
#     bot        14.55         8.57          the bottom-edge profile
#     euler       4.53        13.40          holes
#     ---------------------------------
#     rowfill + sideprof  92.85 right  0.00 wrong    <- what this file uses
#     all twelve groups   91.74        0.04
#
# So the two carrying it are the silhouette's own coarse raster: how full each
# horizontal band is, and where its left and right edges are. That is not an
# abstract invariant, it is a 24-cell picture of the piece. The abstract ones
# are dead weight and two of them are worse than that:
#
#   TOPOLOGY DOES NOT SURVIVE THE PIXELS. The Euler number alone reads 4.53%
#   of occupied squares right and 13.40% wrong, and dropping it costs nothing
#   anywhere. At 32 cells across it is not counting the hole in a king's cross,
#   it is counting how many fragments the antialiasing between fill and outline
#   broke into: the same piece type comes out at -1.1 on one set and -10.5 on
#   another. pieces.py says the same thing about its own copy of this number
#   and it was right.
#
#   THE KNIGHT IS NOT ISOLATED BY ITS ASYMMETRY. It is the most asymmetric
#   piece, median 0.418 against 0.170 for everything else, but the two
#   distributions overlap badly: the best single threshold anywhere gives 92%
#   knight recall at 33% precision, F1 0.487. Every piece is asymmetric once
#   antialiasing and a two-pixel outline are in the silhouette, and a bishop
#   drawn with its slit off centre is as asymmetric as a knight. Within one set
#   it is better but still not separable: msgothic's knights sit at 0.425 and
#   the 95th percentile of its other five pieces is 0.453.
#
# TEMPLATE-FREE DOES NOT WORK, and this is the clearest negative here. Reading
# each set with prototypes pooled from the other three, so that no template
# from the set on the board is in hand:
#
#     set on board   type right (6 way)   with its own prototypes
#     chesscom            80.4%                  95.5%
#     flat                90.7%                  99.9%
#     msgothic            70.4%                  97.0%
#     seguisym            56.5%                  99.0%
#
# and naming the piece outright rather than the type, at the settings this
# file ships, foreign prototypes give 72.7 to 84.8 right and 1.6 to 9.4 WRONG
# where its own give 96.68 and 0.00. The failure is not subtle and it is not
# noise: seguisym's pawns land on the other sets' bishops 446 times. A pawn is
# the smallest and roundest thing on the board, but how small and how round is
# a decision the artist makes, and the number that comes out of the silhouette
# is that decision as much as it is the piece. These descriptors are not set
# independent, and one board of the set in hand is worth more than three whole
# other sets.
#
# WHERE IT STILL LOSES, on the full corpus, 225 wrong answers in 65,536.
# 148 of them are at 280px and 8 at 824px, and 179 are on two variants:
#
#   thin and heavy, 179 of the 225. Those two variants are a morphological
#   opening and closing, which is exactly "the same set drawn with a lighter
#   or a heavier outline". The dark layer of a light piece is a ring; thinning
#   breaks the ring and thickening fills it into the solid that a dark piece
#   is, and the reader then calls the colour wrong. It is the direct cost of
#   the choice that bought the most, and the four colour descriptors cannot
#   cover it: under this segmentation they get colour wrong on 12.5% of
#   occupied squares on their own, so vetoing with them would refuse more
#   right answers than it saves wrong ones.
#
#   the smallest size, which is two thirds of everything left. seguisym's king
#   reads as its queen at 280px in six different variants, thin and plain and
#   dim alike; at 35 pixels a square the two silhouettes are the same 24
#   numbers. The reader is asked about sizes it was taught four views of, and
#   the smallest of those views is 280px of an 824px board, so 280px is the
#   edge of what it has been shown rather than a place it degrades gently.
#
#   blur is still where the unknowns are, 202 of chesscom's 1,024 blurred
#   squares at every size. They are refused rather than guessed, which is the
#   trade this file is set up to make.
#
# WHAT THE FLATNESS TEST IS FOR. Occupancy and identity want different
# measurements. 162 pieces of the quick corpus and 1,605 of the full one have
# no ink at all after thresholding, and calling those squares empty is a wrong
# answer that goes into a permanent record. Asking instead whether the middle
# of the square is one flat colour separates them outright: over all 65,536
# squares every one of the 35,775 that really is empty measures 0.0000, and
# every eaten piece measures at least 0.2157. That one test is 1.32 points of
# wrong answers turned into "?" for nothing, and it is the only part of this
# file that a last-move highlight cannot move, because a highlight washes a
# square in one flat colour and this measures spread rather than level.
