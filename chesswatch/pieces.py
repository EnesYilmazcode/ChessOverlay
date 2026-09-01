"""Identify which piece is on each square, from pixels alone.

The occupancy reader in watcher.py only tells white from black, which is enough
to follow a game move by move but not enough to read a position cold. This adds
the missing half: shape matching against piece templates.

A square is reduced to two binary masks, one of the pixels far brighter than the
board underneath and one of the pixels far darker. Keeping the two apart is the
whole trick. Merged into a single silhouette a white piece's fill and its dark
outline become one solid blob, and then every piece is the same blob in the
lower middle of the square: on a piece set the templates were not drawn from,
the right answer came top of the ranking 5 times in 32 and the margin threw
away all but one of those. Scored as two layers an outline stays an outline,
which is most of what tells a knight from a king, and the same 32 squares come
top 23 times.

Board colours, the last-move highlight, the check marker and the coordinate
labels all sit between the two cutoffs, so the layers are the piece and nothing
else. Both ends of both cutoffs are measured off the board, its two square
colours and the darkest and brightest its ink reaches, so a board theme that is
not chess.com's green moves them instead of being cut into, and so does a
screen turned up or down: brightness and contrast are affine on pixel values,
which moves all four measurements together and leaves the mask where it was.

Masks are compared twice, once where they sit in the square and once registered
onto their own bounding box, and six descriptors that survive a retexture are
scored alongside. Every template is tried and the winner has to beat the best
*other piece type* by a margin, so a square the pixels do not settle comes back
as "?" rather than as a confident wrong piece.

Templates come from `pieces.png` to start with, are relearned from your own
screen the moment a position we are sure of appears, and are cached next to it
so the next game starts where the last one left off. They also carry the
signature of the board they came from, and a board whose pieces are drawn
differently is read with a higher floor, because templates from the wrong set
produce confident wrong names rather than obvious nonsense.
"""

import base64
import hashlib
import json
import os
import zlib

from PIL import Image, ImageChops

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
NORM = 40                 # size every mask is compared at
MIN_OVERLAP = 0.30        # below this, call it unrecognised rather than guess

# And the winner has to be this far clear of the best *different* piece type.
# Same 0.05 as before the mask was split, and for the same reason: on a clean
# board the closest correct call clears it several times over, while a bad crop
# or the wrong screen brightness lands two candidates on top of each other.
# What the split changed is which pairs are close. A white knight against a
# black knight is no longer one of them, because the runner up is now taken
# over piece types rather than over the twelve symbols: two readings of one
# shape thinning the margin cost most of a foreign set for nothing, since the
# colour veto is what decides colour and the shape margin never could.
MIN_MARGIN = 0.05

# Templates carry the signature of the board they came from. When the board
# being read has a different one the templates are somebody else's piece set,
# and a call that is only the best of a bad field is a guess, so the floor
# rises from MIN_OVERLAP to this.
#
# 0.40 is where it has to be. Over 227 confident calls made by 6.png's
# templates on every size, crop and brightness variant of the reference boards,
# the highest scoring WRONG one is 0.398 and no lower floor clears them: 0.37
# still leaves five. It costs right answers too, 40 of 157 surviving, which is
# the trade this file has always made.
#
# 0.16 is the tightest tolerance that costs no correct answer on any capture of
# the right set. See _trusted for what it does and does not separate.
#
# 0.40 is also the lowest floor that leaves the mistrusted reference row with
# no wrong piece at all: 0.37 leaves three and 0.34 leaves five. Higher costs
# right answers without buying much, 0.45 giving up 9 of the 13 that row names
# and one wrong piece over the wider set of captures.
TRUST_TOL = 0.16
MISTRUST_OVERLAP = 0.40

# Outline thickness cannot be compared between two captures whose squares
# differ by more than this. The smaller one has resampled the outline away
# rather than been drawn without one, and reads as a different set when it is
# the same one photographed worse. See _trusted.
TRUST_SCALE = 2.0

# Templates learned on a board smaller than this were upsampled into the NORM
# grid at birth, and stay coarse however big the board later gets. See stale().
MIN_LEARN_PX = NORM * 8

# Confirming one named piece rather than choosing between twelve. A question a
# pointer or a popup does not spoil, where _judge's is. See _contains.
#
# The margin is what refuses a square something has covered outright. A cover
# fills one layer of the square outright, so all six pieces of that colour come
# back contained to the last cell and none of them clears the others; the other
# colour scores about 0.2 and is nowhere near. Contained by everything is the
# square having stopped being evidence, and a containment score alone cannot
# see that.
#
# Two measurements. Over 2525 refused squares from three games read under a
# pointer at two sizes, a one to four square hover popup light and dark, whole
# squares painted over and a piece a third, half and two thirds of the way
# through its move, 0.15 confirms nothing the square is not holding and 0.10
# confirms three. Over the 349 refused squares of the 264 obstructed captures
# piecetest builds, where every one is offered all twelve wrong answers as well
# as the right one, the closest a wrong belief comes is 0.1235, so 0.15 clears
# the worst of them by 0.0195 and 0.12 does not. Above 0.15 it starts costing:
# 0.20 gives up 58 of the 429 confirms on the games and buys nothing measured.
#
# The floor fired on none of it. Over 1197 obstructed captures it refused no
# confirm the margin allowed, and the lowest containment the margin was ever
# satisfied with is 0.5354. It is kept for what those captures do not contain:
# on the margin alone a square holding almost nothing confirms as long as
# nothing else is there either, a believed piece at 0.16 against rivals at zero
# reading the same as one at 0.9 against 0.2. That is a gap in the measurement
# rather than a case it caught, and piecetest pins the distance between the two
# numbers so the floor cannot quietly be raised into the answers.
CONFIRM_CONTAIN = 0.40
CONFIRM_MARGIN = 0.15

# What counts as an empty square, and where each piece is, both shared with the
# occupancy reader. Its BRIGHT and DARK are not imported any more, because what
# counts as a piece pixel is measured off the board here rather than fixed.
from watcher import MIN_COVERAGE, grid_of

# chess.com's green board converted to grey, its two square colours and the
# levels its pieces reach. Only a bootstrap: every read measures the board in
# front of it.
DEFAULT_LEVELS = (131, 233, 32, 254)

# A piece pixel is this far from the board colour towards the darkest or the
# brightest the ink on this board actually gets.
#
# Anchoring on the ink rather than on 0 and 255 is what makes the mask survive
# a change of screen brightness or contrast. Both are affine on pixel values,
# so the two board colours and the two ink extremes move together, the cutoffs
# move with them and the mask lands on the same cells. Anchored on 0 and 255 it
# did not: 1.png at 0.80 contrast has ink reaching only 238 while the fixed
# rule put the bright cutoff at 238, so the bright layer emptied and every
# white piece on the board read as a dark one.
#
# 0.54 rather than the midpoint, and this one is tuned. Below it the mask keeps
# more of the antialiasing between fill and outline: at 0.50 the distorted set
# costs 69 wrong pieces against 55 here, and small windows name 480 squares
# against 487. Above it the bundled sheet stops reading the flat set on 6.png
# at all, 8 pieces named at 0.54 and none at 0.55.
PIECE_F = 0.544

# There is only ink on a side of the board colour if it reaches this much of
# the way from one square colour to the other. Without it a board with nothing
# on it, whose ink extremes are its own square colours, would put both cutoffs
# on the board colour and read every pixel as a piece. Relative to the board's
# contrast rather than in grey levels, so that it survives the same affine
# change the cutoffs do.
#
# It cannot separate cleanly, and the measurement says so: a downsampled empty
# board reaches 0.108 on seam ringing alone while a starting position at 1.25
# contrast reaches only 0.063 on its white pieces, because the fill clips at
# 255 and leaves eight levels of headroom. What settles it is that 0.13 to 0.15
# all behave identically, and over 66 captures of six sparse positions that
# band invents nothing at all where 0.12 invents 26 squares and 0.06 invents
# 427. Below the band the distorted set gets better and the sparse boards get
# much worse; 0.16 doubles the distorted set's wrong pieces.
INK_F = 0.14

# The board's own levels are measured this far in from its edge. A crop that
# find_board got wrong by two pixels drags a black column in from outside the
# picture, and black past the ink extreme moves every cutoff on the board: it
# cost 21 named squares over the misaligned crops. Nothing of a piece lives in
# the outer fiftieth of a board, since pieces sit centred in their squares.
LEVEL_INSET = 0.02

# A cell of the NORM grid counts as piece when this much of the native pixels
# under it were. Thresholding happens at native resolution and the binary mask
# is then area-downsampled, so a cell holds a real coverage fraction rather
# than one sampled pixel. 96 of 255 is under a third because outlines are the
# part worth keeping and an outline two native pixels wide covers well under
# half of a cell that spans five.
FILL = 96

# Two more readings of the same coverage grid. A cell has to be this well
# covered to help decide where the piece is, which is what keeps one stray
# pixel out of the bounding box, and this lightly covered for the hole count,
# which is what stops antialiasing along an edge reading as a row of holes.
BOX_FILL = 128
SOLID_FILL = 32

# How the two comparisons and the descriptors are weighted. The registered
# comparison is what reads an unfamiliar set, since it throws away where in the
# square a piece sits and keeps only its shape; the unregistered one is what
# keeps a familiar set exact, since where a piece sits is real information when
# the templates came off the same renderer.
REG_WEIGHT = 0.5
DESC_WEIGHT = 0.5

# Descriptor tolerances, each the difference at which that descriptor stops
# saying anything. Loose on purpose: they break ties between shapes the overlap
# has already put close together, and a descriptor that vetoes on its own would
# cost more than it buys.
TOP_TOL = 0.30            # mean disagreement of the top-edge profile
ASPECT_TOL = 0.60         # bounding box height over width
FILL_TOL = 0.30           # piece pixels as a fraction of the bounding box
CY_TOL = 0.25             # centroid height within the bounding box
HEIGHT_TOL = 0.30         # bounding box height as a fraction of the square
EULER_TOL = 20.0          # difference in enclosed holes

TOP_BANDS = 8

_BITS = NORM * NORM
_ROW_ALL = (1 << NORM) - 1
_ROW_BYTES = NORM // 8

# Thresholds are bytes.translate tables and not Image.point tables. point()
# rounds all 256 entries of its table on every single call, and at six calls a
# square that came to nine of the nineteen milliseconds a board took; translate
# takes the table it is handed. Both routes produce identical packed bytes.
_FILL_TABLE = bytes(255 if v >= FILL else 0 for v in range(256))
_BOX_TABLE = bytes(255 if v >= BOX_FILL else 0 for v in range(256))
_SOLID_TABLE = bytes(255 if v >= SOLID_FILL else 0 for v in range(256))

# Column bands for the top-edge profile. Held as full-height masks so the
# height of the topmost piece pixel in a band is one AND and one bit_length
# rather than a scan down forty rows.
_BANDS = []
for _j in range(TOP_BANDS):
    _wide = NORM // TOP_BANDS
    _cols = 0
    for _c in range(_j * _wide, (_j + 1) * _wide):
        _cols |= 1 << (NORM - 1 - _c)
    _band = 0
    for _r in range(NORM):
        _band |= _cols << ((NORM - 1 - _r) * NORM)
    _BANDS.append(_band)

# Every 2x2 window of the grid, addressed by its top left cell, which is every
# cell that has a right and a lower neighbour.
_WINDOW = 0
for _r in range(NORM - 1):
    _WINDOW |= (_ROW_ALL ^ 1) << ((NORM - 1 - _r) * NORM)

_TABLE_CACHE = {}


def _tables(levels):
    """Bright and dark threshold tables for a board with these greys and ink."""
    got = _TABLE_CACHE.get(levels)
    if got is None:
        lo, hi, floor, ceil = levels
        bar = (hi - lo) * INK_F
        # 255 and 0 select nothing, which is the right answer for a board with
        # no light pieces or no dark ones left on it.
        bright = (hi + (ceil - hi) * PIECE_F) if ceil - hi >= bar else 255
        dark = (lo - (lo - floor) * PIECE_F) if lo - floor >= bar else 0
        got = (bytes(255 if v > bright else 0 for v in range(256)),
               bytes(255 if v < dark else 0 for v in range(256)))
        if len(_TABLE_CACHE) > 32:
            _TABLE_CACHE.clear()
        _TABLE_CACHE[levels] = got
    return got


def _cut(img, table):
    """One "L" image thresholded to 0 or 255 through a translate table."""
    return Image.frombytes("L", img.size, img.tobytes().translate(table))


def _pack(grid, table):
    """A NORM grid thresholded and packed into one integer, a bit per cell.

    Rows of a mode "1" image pad up to a whole byte and Pillow zeroes the
    padding, so a NORM that is not a multiple of 8 would change what the
    integer is without changing any score: zero bits add nothing to an and, an
    or or a popcount.
    """
    return _cut(grid, table).convert("1", dither=Image.Dither.NONE).tobytes()


def _levels(board_img, floor=0.04, apart=24, ink=0.001):
    """The board's two square colours and the levels its ink reaches.

    Returns (darker square, lighter square, darkest ink, brightest ink).

    A board is mostly board even with every piece on it, so the square colours
    are the two commonest grey levels that are far enough apart to be two
    colours rather than one colour and its antialiasing. A level has to hold a
    twenty-fifth of the board to be one at all, which no piece fill on any
    fixture does, and 24 apart is comfortably under the 102 that separates
    chess.com's own two and comfortably over the spread of one antialiased edge.

    Measured off the whole board and never off a single square, because one
    square's commonest level is as often the piece as the board under it: the
    white king's square on 1.png peaks at 249, which is its own fill, and
    treating that as board colour would threshold the king away.

    The ink extremes are a thousandth of the board in from each end rather than
    the outright darkest and brightest pixel, so that one stray pixel from a
    window edge or an overlay cannot set them. A single king still covers three
    thousandths of a board, so nothing real is trimmed away.
    """
    wide, high = board_img.size
    inset = board_img.crop((int(wide * LEVEL_INSET), int(high * LEVEL_INSET),
                            int(wide * (1 - LEVEL_INSET)),
                            int(high * (1 - LEVEL_INSET))))
    hist = inset.resize((128, 128), Image.NEAREST).convert("L").histogram()
    total = sum(hist)
    edge = max(1, int(total * ink))
    dark_ink = bright_ink = 0
    run = 0
    for v in range(256):
        run += hist[v]
        if run >= edge:
            dark_ink = v
            break
    run = 0
    for v in range(255, -1, -1):
        run += hist[v]
        if run >= edge:
            bright_ink = v
            break
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
    return min(found), max(found), dark_ink, bright_ink


def _euler(mask):
    """Objects minus holes, from the 2x2 patterns the grid contains.

    Counting patterns rather than flood filling keeps it to twenty operations
    on one 1600 bit integer instead of 1600 steps of Python.

    Be careful what this is. At forty cells across it does not count the hole
    in a king's cross, it counts the gap between a piece's fill and its
    outline, which antialiasing breaks into fragments: chess.com's white queen
    comes out at 32 holes and the flat set's at 3. Within one set the number
    repeats to the hole, both rooks at 18 and both knights at 23, so it does
    separate pieces. Across sets it separates the renderings and not the
    shapes, which is why EULER_TOL is 20 and not 3. That is the weakest of the
    six descriptors and it is doing the least here.
    """
    a = mask & _WINDOW
    b = (mask << 1) & _WINDOW
    c = (mask << NORM) & _WINDOW
    d = (mask << (NORM + 1)) & _WINDOW
    na, nb, nc, nd = a ^ _WINDOW, b ^ _WINDOW, c ^ _WINDOW, d ^ _WINDOW
    one = ((a & nb & nc & nd) | (na & b & nc & nd) |
           (na & nb & c & nd) | (na & nb & nc & d))
    three = ((a & b & c & nd) | (a & b & nc & d) |
             (a & nb & c & d) | (na & b & c & d))
    diag = (a & nb & nc & d) | (na & b & c & nd)
    return (one.bit_count() - three.bit_count() - 2 * diag.bit_count()) // 4


class _Square:
    """One square reduced to what the matcher compares.

    Four grids: the bright and the dark layer where they sit in the square, and
    the same two registered onto the piece's own bounding box. Each is held as
    a coverage image and as a bitset, the image because learning averages them
    and the bitset because & and | then intersect all 1600 cells at once.
    """

    __slots__ = ("grids", "masks", "tall", "wide", "aspect", "bright", "dark",
                 "fill", "cy", "top", "euler", "coverage")

    def __init__(self, grids, tall, wide):
        self.grids = grids
        packed = [_pack(g, _FILL_TABLE) for g in grids]
        self.masks = [int.from_bytes(b, "big") for b in packed]
        self.bright = self.masks[0].bit_count()
        self.dark = self.masks[1].bit_count()
        self.coverage = (self.bright + self.dark) / _BITS
        # How much of the square the piece's bounding box spans, each way.
        self.tall = tall
        self.wide = wide
        self.aspect = tall / wide

        shape = self.masks[2] | self.masks[3]
        # Rows come off the packed bytes rather than out of the integer. Each
        # row is its own five byte slice, where shifting the whole 1600 bit
        # integer down forty times touches all of it forty times over.
        rows = bytes(a | b for a, b in zip(packed[2], packed[3]))
        counts = [int.from_bytes(rows[r * _ROW_BYTES:(r + 1) * _ROW_BYTES],
                                 "big").bit_count() for r in range(NORM)]
        area = sum(counts) or 1
        self.fill = area / _BITS
        self.cy = sum(r * n for r, n in enumerate(counts)) / area / (NORM - 1)
        # An empty band reads as a top edge at the bottom of the grid, which is
        # what it is: nothing of the piece reaches into that column.
        self.top = tuple((_BITS - (shape & band).bit_length()) // NORM / NORM
                         for band in _BANDS)
        # Holes are counted off the lightly covered reading, not this one. At
        # FILL an antialiased edge breaks into specks and a rook came out with
        # seventeen holes against its own template's five.
        self.euler = _euler(int.from_bytes(
            _pack(ImageChops.lighter(grids[2], grids[3]), _SOLID_TABLE), "big"))


def _union(a, b):
    if a is None or b is None:
        return a or b
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _shrink(layer):
    """Area-downsample a native-resolution binary layer into the NORM grid.

    This way round on purpose. Subsampling first and thresholding after, which
    is what this used to do, decided a two pixel outline by which native pixel
    NEAREST happened to land on, so the same piece kept or lost its outline with
    subpixel phase. Thresholding first and averaging after turns that outline
    into a coverage fraction that moves smoothly instead. Measured with the two
    orders as the only difference, the worst correct margin on a clean board
    goes from 0.101 to 0.158, and over the misaligned crops piecetest builds the
    reader names 902 squares of 1024 rather than 819, neither of them wrong.
    """
    return layer.resize((NORM, NORM), Image.BOX)


def _box(bright, dark, coarse, plain):
    """Where the piece is, decided by coverage rather than by any one pixel.

    A box taken straight off the native layers is at the mercy of a single
    stray pixel, and the bundled sheet has some: the LANCZOS resize that built
    it rings along the crop edge, which put dark pixels in the corner of the
    rook's slot and stretched its box to the whole square. A rook then scored
    0.28 registered against its own template where a knight scored 0.88, and
    of the five rooks on the two reference boards three read as knights and the
    other two came back unclear. Cells of the coarse
    grid that are barely covered are dropped first and the box is measured at
    native resolution inside what survives, so the box is coverage-decided and
    still pixel-exact.
    """
    keep = _cut(coarse, _BOX_TABLE)
    if keep.getbbox() is None:
        return plain
    solid = ImageChops.multiply(ImageChops.lighter(bright, dark),
                                keep.resize(bright.size, Image.NEAREST))
    return solid.getbbox() or plain


def _features(square_img, levels=DEFAULT_LEVELS):
    """Reduce one square image to a _Square, or None when it holds no piece."""
    bright_table, dark_table = _tables(levels)
    grey = square_img.convert("L")
    raw = grey.tobytes()
    bright = Image.frombytes("L", grey.size, raw.translate(bright_table))
    dark = Image.frombytes("L", grey.size, raw.translate(dark_table))
    # An empty square costs two thresholds and two bounding boxes and stops
    # here, which is half the board in a starting position.
    plain = _union(bright.getbbox(), dark.getbbox())
    if plain is None:
        return None
    flat = (_shrink(bright), _shrink(dark))
    box = _box(bright, dark, ImageChops.lighter(*flat), plain)
    width = max(1, box[2] - box[0])
    height = max(1, box[3] - box[1])
    grids = flat + (_shrink(bright.crop(box)), _shrink(dark.crop(box)))
    return _Square(grids, height / max(1, grey.size[1]),
                   width / max(1, grey.size[0]))


def _overlap(a, b, offset=0):
    """Intersection over union of a pair of layers against a pair of layers.

    The two layers share one union rather than being averaged into each other.
    Averaging two IoUs flatters any comparison where a layer is empty on both
    sides, because an empty layer scores a perfect 1.0 against another empty
    one; pooling the counts lets an empty layer contribute nothing instead.
    Bright is never counted against dark either way, which is the point.

    bit_count() is a C popcount and wants Python 3.10 or newer. Worth the
    floor: bin(mask).count("1") builds a 1600 character string every time, and
    this runs some hundreds of times a board.
    """
    inter = ((a.masks[offset] & b.masks[offset]).bit_count() +
             (a.masks[offset + 1] & b.masks[offset + 1]).bit_count())
    union = ((a.masks[offset] | b.masks[offset]).bit_count() +
             (a.masks[offset + 1] | b.masks[offset + 1]).bit_count())
    return inter / union if union else 0.0


def _contains(feat, template):
    """How much of a template's ink the square actually holds.

    _overlap divides by the union of the two, which the overlay moves along
    with the intersection, so all twelve scores sink together and the winner
    stops beating the runner up. That is the "?" this exists to answer. Here
    the denominator is the piece's own size and nothing on the square can move
    it, so the candidates stay comparable to each other however much ink is on
    top of them.

    It is not immune to the overlay and must not be read as if it were. The
    mask comes from thresholding each pixel, so ink drawn over a piece replaces
    the piece's own rather than burying it, and this falls when that happens.
    Measured over 225 squares of the reference boards it costs the true piece a
    median 0.016 under a cursor, 0.169 under a bigger one, and 0.614 and 0.630
    under a panel and a covered square. What the fixed denominator buys is the
    direction of that error: a square whose evidence has been taken away scores
    low and is refused, and a square where enough of the piece is still drawn
    can still clear the field, which against a moving union it cannot.

    The layers where they sit in the square, never the registered pair. The
    registration is taken from the piece's own bounding box, and an overlay
    moves that box, so the registered layers of an obscured square describe
    where the overlay reaches as much as where the piece does.
    """
    a, b = feat.masks, template.masks
    whole = b[0].bit_count() + b[1].bit_count()
    if not whole:
        return 0.0
    return ((a[0] & b[0]).bit_count() + (a[1] & b[1]).bit_count()) / whole


def _descriptors(a, b):
    """How alike two squares are on the measurements a retexture leaves alone.

    None of these is a shape match. They are the things about a piece that
    mostly hold when someone redraws it: how tall it is against how wide, how
    much of the square it spans, how much of its own bounding box it fills,
    where its weight sits, and the outline of its top edge. The hole count is
    the sixth and by some way the weakest of them, see _euler.
    """
    top = sum(abs(x - y) for x, y in zip(a.top, b.top)) / TOP_BANDS
    parts = (1.0 - min(1.0, top / TOP_TOL),
             1.0 - min(1.0, abs(a.aspect - b.aspect) / ASPECT_TOL),
             1.0 - min(1.0, abs(a.fill - b.fill) / FILL_TOL),
             1.0 - min(1.0, abs(a.cy - b.cy) / CY_TOL),
             1.0 - min(1.0, abs(a.tall - b.tall) / HEIGHT_TOL),
             1.0 - min(1.0, abs(a.euler - b.euler) / EULER_TOL))
    return sum(parts) / len(parts)


def _score(a, b):
    """One number for how well a square matches a template.

    The descriptors scale the overlap rather than being averaged into it.
    Averaged, they add a term that is near 1 for every candidate and so
    compress the gaps the margin is measured on, which is the opposite of what
    they are for; multiplied, a candidate the descriptors dislike loses ground
    against one they do not and the score stays on the overlap's own scale,
    which is what MIN_OVERLAP is set against.
    """
    shape = ((1.0 - REG_WEIGHT) * _overlap(a, b) +
             REG_WEIGHT * _overlap(a, b, 2))
    return shape * (1.0 - DESC_WEIGHT * (1.0 - _descriptors(a, b)))


def squares(board_img, size=None):
    """Yield (row, col, square image), row 0 being the top of the screen."""
    size = size or board_img.size[0]
    step = size / 8.0
    for r in range(8):
        for c in range(8):
            yield r, c, board_img.crop((int(c * step), int(r * step),
                                        int((c + 1) * step), int((r + 1) * step)))


def ranking(feat, templates):
    """Every piece type scored against this square, best first.

    One entry per piece type, not per template. A piece learned on a light
    square and the same piece learned on a dark one are two templates of one
    type and their scores are maxed, never compared: the square colour a piece
    happens to be standing on is not a thing to be uncertain between.
    """
    return sorted(((max(_score(feat, t.feat) for t in variants), symbol)
                   for symbol, variants in templates.items()), reverse=True)


def _judge(feat, templates, floor=MIN_OVERLAP):
    """The piece on one already reduced square. Returns (symbol, score), where
    symbol is a piece letter, "." for an empty square, or None when the pixels
    do not settle it.

    Every template is scored, both colours. This used to score only the six of
    whichever colour a bright-versus-dark pixel count voted for, which made a
    wrong colour reading impossible to recover from: the right answer was never
    compared against. Colour now only has to agree with the shape, and the two
    disagreeing is a reason to say nothing rather than to overrule the shape.
    """
    if feat is None or feat.coverage < MIN_COVERAGE:
        return ".", 1.0
    ranked = ranking(feat, templates)
    score, best = ranked[0]
    # The runner up is the best score belonging to a different piece type. A
    # bishop beating a knight is the reader being unsure what it is looking at;
    # a white knight beating a black one is not, it is one shape scored twice,
    # and letting that thin the margin threw away most of a foreign piece set.
    runner_up = next((s for s, symbol in ranked[1:]
                      if symbol.lower() != best.lower()), 0.0)
    if score < floor or score - runner_up < MIN_MARGIN:
        return None, score
    if best.isupper() != (feat.bright > feat.dark):
        return None, score
    return best, score


def _confirms(feat, templates, symbol):
    """Could this square still be holding exactly this piece, with something
    drawn on top of it? Yes or no, and never a guess at what else it is.

    A narrower question than _judge's, and answerable where that one is not.
    _contains measures the named piece against its own size rather than against
    a union the overlay has grown, so a piece a cursor only partly hides can
    still clear the field where the twelve scores no longer can.

    The trap is that a blob contains every shape smaller than itself, and a
    pawn is smaller than everything. Hence the second half: the named piece has
    to be the only one the square holds and not merely one of them. That is
    what refuses a square something has covered outright, where a whole layer
    is filled, every piece of that colour is contained to the last cell and the
    square has stopped being evidence.

    The split mask is what lets it see colour at all. A white piece's ink is
    nearly all in the bright layer and a black piece's in the dark one, and
    bright is never counted against dark, so the two do not contain each other:
    on a clean board 28 of the 32 pieces confirm themselves and none confirms
    as the same piece in the other colour. That is what stops a pawn taken by
    the other side's pawn being confirmed as still standing.

    It may still only ever confirm a piece already believed to be there, never
    name one. Colour it can see; which of two pieces of one colour last stood
    on a square it cannot, and the belief is where that comes from.
    """
    if feat is None or symbol == "." or symbol not in templates:
        return False
    held = {sym: max(_contains(feat, t.feat) for t in variants)
            for sym, variants in templates.items()}
    got = held[symbol]
    # Over piece types rather than symbols, for _judge's reason: one shape
    # scored twice is not two things to be uncertain between.
    rival = max((v for sym, v in held.items() if sym.lower() != symbol.lower()),
                default=0.0)
    return got >= CONFIRM_CONTAIN and got - rival >= CONFIRM_MARGIN


def _decide(square_img, templates, levels=DEFAULT_LEVELS, floor=MIN_OVERLAP):
    """_judge on a square that has not been reduced yet."""
    return _judge(_features(square_img, levels), templates, floor)


class _Template:
    """A piece as everything the reader has been shown of it, averaged.

    One snapshot is one rendering: one antialiasing phase, one moment of the
    board's animation, one crop. Averaging the coverage grids over several
    confident frames keeps the parts of a piece that were there every time and
    fades the parts that were not. The average is kept as images and folded in
    C, because a running mean over 6400 cells of Python per square would cost
    more than the whole read.
    """

    __slots__ = ("feat", "light", "n", "tall", "wide")

    def __init__(self, feat, light=None):
        self.feat = feat
        self.light = light
        self.n = 1
        self.tall = feat.tall
        self.wide = feat.wide

    def fold(self, feat):
        self.n += 1
        weight = 1.0 / self.n
        grids = tuple(Image.blend(old, new, weight)
                      for old, new in zip(self.feat.grids, feat.grids))
        self.tall += (feat.tall - self.tall) * weight
        self.wide += (feat.wide - self.wide) * weight
        self.feat = _Square(grids, self.tall, self.wide)


def _board_features(board_img, levels):
    """Every square of a board reduced at once, empties included as None."""
    return [_features(sq, levels) for _, _, sq in squares(board_img)]


def _signature(feats, levels, square_px):
    """How the pieces on this board are drawn, and how well it was captured.

    How much of a piece is outline rather than fill, averaged over the light
    pieces and again over the dark ones. That is a property of the set and not
    of the position, which is what both the cache key and the trust test need:
    chess.com's own reads 0.12 and 0.00 on the opening of 1.png, on the mating
    position of 5.png and on a rendered endgame with three pieces left, while
    the flat set on 6.png reads 0.36 and 0.15 whatever is standing on it.

    Then how far the ink reaches either side of the board colour, as a
    fraction of the gap between the two square colours. Both of those are
    unchanged by a brightness or a contrast knob, which scales every level
    together, and both collapse under blur, which does not: 1.png reaches 0.97
    below its dark squares sharp and 0.63 blurred by two and a half pixels.
    A foreign set and a smeared capture both mean the templates describe
    something this board is not, and both want the same answer.
    That is the difference between a board drawn with thin outlines and one
    photographed badly, and the reader has to treat them the same way, because
    in both cases the templates in hand describe something the board is not.

    The scale comes along because the measurement needs it. A capture whose
    squares are much smaller has resampled the outline away, and then reads as
    a heavier or lighter set than it is: 1.png downsampled to 200px comes out
    at 0.00 and 0.18, which is further from its own full size capture than the
    flat set is.
    """
    light, dark = [], []
    for feat in feats:
        if feat is None or feat.coverage < MIN_COVERAGE:
            continue
        total = feat.bright + feat.dark
        if not total:
            continue
        if feat.bright > feat.dark:
            light.append(feat.dark / total)
        else:
            dark.append(feat.bright / total)
    lo, hi, floor, ceil = levels
    span = max(1, hi - lo)
    return (sum(light) / len(light) if light else None,
            sum(dark) / len(dark) if dark else None,
            (lo - floor) / span, (ceil - hi) / span, square_px)


def _trusted(templates_sig, board_sig):
    """Whether these templates were drawn from the set now on the board.

    Measured over 149 template-and-board pairs, every capture piecetest builds
    of both fixtures, split by what was done to the capture:

        geometric, crops and resizes    same <= 0.083   foreign >= 0.320
        brightness and contrast         same <= 0.186   foreign >= 0.212
        blur                            same <= 0.431   foreign >= 0.526

    Each band separates. Across bands they do not, and it is blur that spoils
    it: a board blurred by 2.5 pixels sits 0.510 from its own templates, which
    is further than a foreign set ever sits under contrast. That is not a false
    alarm though. A capture whose outlines have been smeared away is one the
    templates in hand do not describe either, and the higher floor is the right
    answer for it: it is part of why the distorted set costs 55 wrong pieces
    here against 91 before any of this.

    Before the cutoffs were anchored on the board's ink the middle band was the
    broken one, at 0.171 for the same set against 0.149 for a foreign one, and
    a screen turned up read as somebody else's pieces.

    It rests on two piece sets, which is not many.
    """
    if templates_sig is None or board_sig is None:
        return True
    a, b = templates_sig[4], board_sig[4]
    if a and b and max(a, b) > TRUST_SCALE * min(a, b):
        return True
    apart = [abs(x - y) for x, y in zip(templates_sig[:4], board_sig[:4])
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
        the square colour. A rook cut from a light square is compared against a
        dark square rook on parity as much as on shape, which is how a taught
        starting position read the h8 rook as a pawn: p scored 0.518 against a
        pawn cut from a dark square and r scored 0.413 against a rook cut from
        a light one.
        """
        img = Image.open(path).convert("RGB")
        # crop() pads out of bounds with black, and black counts as a piece
        # pixel, so a truncated sheet would load as solid masks that match
        # everything rather than fail. Check the width instead.
        #
        # A paired sheet cut short falls back to its first twelve slots, which
        # are a whole plain sheet. Cut to exactly twelve it is refused instead,
        # and correctly: those twelve are all light squares, so _levels finds
        # one board colour rather than two and every slot reduces to nothing.
        # Either way loading is all or nothing and nothing loads as rubble.
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
        out = {}
        feats = []
        for slot in range(slots):
            symbol = ORDER[slot % PLAIN_SLOTS]
            feat = _features(img.crop((slot * TEMPLATE_PX, 0,
                                       (slot + 1) * TEMPLATE_PX, TEMPLATE_PX)),
                             levels)
            if feat is None:
                raise ValueError("template sheet holds a blank slot")
            # A plain sheet does not record which colour anything stood on, and
            # None is the honest answer there: observe() then leaves the sheet
            # template alone and starts its own average per colour instead of
            # folding real squares into one that may be the wrong colour.
            light = slot < PLAIN_SLOTS if slots == PAIRED_SLOTS else None
            out.setdefault(symbol, []).append(_Template(feat, light))
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
        on the sheet. This used to want all twelve or nothing, which meant a
        game joined part way through spent its whole life on the bundled sheet
        even when eleven of the twelve were sitting there to be read. Returning
        True still means all twelve, because the callers that ask for a refit
        mid game have a full set in hand already and want to know.

        Both square colours are learned separately. First seen won before, so
        every template came off whichever colour that piece happened to start
        on, and a black knight learned on a light square read poorly on a dark
        one: relearning from 6.png and reading 6.png straight back still left
        14 of its 32 pieces unread.
        """
        grid = grid_of(board, flipped)
        levels = _levels(board_img)
        feats = _board_features(board_img, levels)
        found = {}
        for (r, c, _), feat in zip(squares(board_img), feats):
            symbol = grid[r][c]
            if symbol == "." or feat is None:
                continue
            # a8 and h1 are both light, so screen parity says which colour a
            # square is whichever way round the board is being viewed.
            light = (r + c) % 2 == 0
            variants = found.setdefault(symbol, {})
            if light not in variants:
                variants[light] = _Template(feat, light)
        found = {s: list(v.values()) for s, v in found.items() if v}
        if not found:
            return False
        self.templates = dict(self.templates)
        self.templates.update(found)
        self.learned_size = board_img.size[0]
        self.signature = _signature(feats, levels, board_img.size[0] / 8.0)
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

        A square the reader already reads as some *other* piece is refused.
        That is the one signal available here that the picture and the label
        have come apart, which is exactly the failure a frame the tracker has
        fallen a ply behind on produces, and folding one of those in teaches
        the wrong piece rather than nothing.
        """
        if not self.ready:
            return 0
        grid = grid_of(board, flipped)
        levels = _levels(board_img)
        feats = _board_features(board_img, levels)
        folded = 0
        for (r, c, _), feat in zip(squares(board_img), feats):
            symbol = grid[r][c]
            if symbol == "." or symbol not in self.templates:
                continue
            if feat is None or feat.coverage < MIN_COVERAGE:
                continue
            best = ranking(feat, self.templates)[0][1]
            if best != symbol:
                continue
            light = (r + c) % 2 == 0
            variants = self.templates[symbol]
            match = next((t for t in variants if t.light == light), None)
            if match is None:
                variants.append(_Template(feat, light))
            else:
                match.fold(feat)
            folded += 1
        if folded:
            self.learned_size = board_img.size[0]
            self.signature = _signature(feats, levels, board_img.size[0] / 8.0)
            if self.source == "bundled":
                self.source = "learned in part from your screen"
            self._folds += folded
            # The average moves less the longer it runs, so the cache is
            # rewritten on a doubling rather than on a frame. Left per frame it
            # is a 20 KB write inside the capture loop for a set that has
            # stopped changing.
            if self._folds >= 2 * self._saved:
                self._saved = self._folds
                self._cache_write(levels)
        return folded

    def relearn(self, board_img, board, flipped=False):
        """Learn again part way through a game, after the window changed size.

        All or nothing, unlike learn(). A resize arrives on whatever position
        happens to be up, so refitting from the four piece types an endgame has
        left would trade twelve fitted templates for four, and the caller has
        no way to tell that happened. Failing costs nothing unless what we are
        already holding is worse than the sheet, so the caller can just ask.
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

        Not "the window changed size". Measured over 152 learn-size and
        read-size pairs, a set learned on a board of at least MIN_LEARN_PX read
        every board from 200px to 1600px with no loss against the sheet, in
        either direction, so a size change on its own is never a reason to
        throw one away. What does cost is learning below that floor and then
        growing: at 240px it gives up 3.0 points to the sheet, at 200px 14.4,
        and it is the only case in the whole study that ever named a WRONG
        piece rather than "?".

        The floor is NORM rather than a tuned number. Under it a square held
        fewer screen pixels than the grid its mask is compared on, so the
        template was an upsample the moment it was learned and stays coarse.
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
        same window and the same set starts fitted instead of on the sheet,
        joined part way through or not.

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
            body = {"norm": NORM, "fill": FILL,
                    "signature": self.signature, "pieces": [
                {"symbol": symbol, "light": t.light, "n": t.n,
                 "tall": t.tall, "wide": t.wide,
                 "grids": [base64.b64encode(zlib.compress(g.tobytes())).decode()
                           for g in t.feat.grids]}
                for symbol, variants in self.templates.items() for t in variants]}
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
        here = _signature(_board_features(board_img, levels), levels,
                          board_img.size[0] / 8.0)
        try:
            with open(self._cache_path(board_img.size[0], levels, here)) as fh:
                body = json.load(fh)
            if body["norm"] != NORM or body["fill"] != FILL:
                return False
            loaded = {}
            for item in body["pieces"]:
                grids = tuple(
                    Image.frombytes("L", (NORM, NORM),
                                    zlib.decompress(base64.b64decode(blob)))
                    for blob in item["grids"])
                t = _Template(_Square(grids, item["tall"], item["wide"]),
                              item["light"])
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
        """MIN_OVERLAP, or the mistrusted floor when these templates describe
        something this board is not."""
        here = _signature(feats, levels, square_px)
        return MIN_OVERLAP if _trusted(self.signature, here) else MISTRUST_OVERLAP

    def classify(self, board_img, believed=None):
        """Read the whole board. Returns 8 rows of piece letters and dots, plus
        the weakest match score, which says how much to trust it.

        Every square is reduced first and scored after, rather than one at a
        time, because the trust test needs to have seen the whole board before
        any square is named and reducing is the expensive half.

        `believed` is the grid the caller already thinks is on screen, in the
        same screen order, and is the whole of the second question this asks. A
        square the scores refuse is put back as "is this one named piece still
        here", which _confirms can answer through a pointer or a popup where
        choosing between twelve cannot. It only ever confirms, so a square the
        caller has no belief about, or the wrong belief about, stays "?".

        In this pass rather than a second one because the reducing and the
        trust floor are both already in hand here, and doing it outside would
        buy one narrow answer for the price of reading the whole board twice.
        """
        rows = [["."] * 8 for _ in range(8)]
        weakest = 1.0
        if not self.ready:
            return rows, 0.0

        levels = _levels(board_img)
        feats = _board_features(board_img, levels)
        floor = self._floor(feats, levels, board_img.size[0] / 8.0)
        for (r, c, _), feat in zip(squares(board_img), feats):
            symbol, score = _judge(feat, self.templates, floor)
            if symbol == ".":
                continue
            if symbol is None:
                held = believed[r][c] if believed else "."
                rows[r][c] = (held if _confirms(feat, self.templates, held)
                              else "?")
                # Zero either way. A confirmed square was refused by the
                # scores, so a caller reading the weakest score to decide how
                # far to trust the board must not be told this one scored well.
                weakest = 0.0
            else:
                rows[r][c] = symbol
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
        feats = _board_features(board_img, levels)
        return _judge(feats[row * 8 + col], self.templates,
                      self._floor(feats, levels, board_img.size[0] / 8.0))[0]
