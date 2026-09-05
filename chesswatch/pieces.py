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
else. The cutoffs are measured off the board rather than fixed, so a board theme
that is not chess.com's green moves them instead of being cut into.

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

# What counts as an empty square, and where each piece is, both shared with the
# occupancy reader. Its BRIGHT and DARK are not imported any more, because what
# counts as a piece pixel is measured off the board here rather than fixed.
from watcher import MIN_COVERAGE, grid_of

# chess.com's green board converted to grey, and what _levels falls back to.
# Only a bootstrap: every read measures the board in front of it.
DEFAULT_LEVELS = (131, 233)

# A piece pixel is more than half way from the board colour to black or to
# white. Half is the midpoint and not a tuned number. On chess.com's board it
# puts the cutoffs at 65 and 244, within a few units of the fixed 70 and 244
# the occupancy reader uses, so this is the same rule with the board measured
# instead of assumed.
PIECE_F = 0.5

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
    """Bright and dark threshold tables for a board drawn in these two greys."""
    got = _TABLE_CACHE.get(levels)
    if got is None:
        lo, hi = levels
        dark = lo - lo * PIECE_F
        bright = hi + (255 - hi) * PIECE_F
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


def _levels(board_img, floor=0.04, apart=24):
    """The two greys a board's squares are drawn in, darker one first.

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
    """
    hist = board_img.resize((128, 128), Image.NEAREST).convert("L").histogram()
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
        return found[0], found[0]
    return min(found), max(found)


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


def _signature(feats, square_px):
    """How the pieces on this board are drawn, as two numbers and a scale.

    How much of a piece is outline rather than fill, averaged over the light
    pieces and again over the dark ones. That is a property of the set and not
    of the position, which is what both the cache key and the trust test need:
    chess.com's own reads 0.12 and 0.00 on the opening of 1.png, on the mating
    position of 5.png and on a rendered endgame with three pieces left, while
    the flat set on 6.png reads 0.36 and 0.15 whatever is standing on it.

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
    return (sum(light) / len(light) if light else None,
            sum(dark) / len(dark) if dark else None,
            square_px)


def _trusted(templates_sig, board_sig):
    """Whether these templates were drawn from the set now on the board.

    Measured over 149 template-and-board pairs, every capture piecetest builds
    of both fixtures, and it separates cleanly only while the capture is at the
    reference brightness. There, over 69 pairs, the same set never sits further
    than 0.078 and a foreign set never closer than 0.166.

    Add brightness, contrast or blur and the two distributions overlap: the
    same set reaches 0.171 at 1.25 contrast, a foreign one falls to 0.149 at
    0.85 brightness, and no threshold separates them at all. Distortion moves
    what counts as a piece pixel, which moves the outline share, which is the
    whole measurement. So this catches a foreign set on a clean capture and
    misses one on a badly distorted capture, and there is no tuning that fixes
    the second case.

    It rests on two piece sets, which is not many.
    """
    if templates_sig is None or board_sig is None:
        return True
    a, b = templates_sig[2], board_sig[2]
    if a and b and max(a, b) > TRUST_SCALE * min(a, b):
        return True
    apart = [abs(x - y) for x, y in zip(templates_sig[:2], board_sig[:2])
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
        img = Image.open(path).convert("RGB")
        # crop() pads out of bounds with black, and black counts as a piece
        # pixel, so a truncated sheet would load as solid masks that match
        # everything rather than fail. Check the width instead.
        if img.size[0] < len(ORDER) * TEMPLATE_PX:
            raise ValueError("template sheet holds fewer than twelve pieces")
        # The sheet is twelve real squares side by side, board colours and all,
        # so its own two greys measure the same way a board's do.
        levels = _levels(img)
        out = {}
        feats = []
        for slot, symbol in enumerate(ORDER):
            feat = _features(img.crop((slot * TEMPLATE_PX, 0,
                                       (slot + 1) * TEMPLATE_PX, TEMPLATE_PX)),
                             levels)
            if feat is None:
                raise ValueError("template sheet holds a blank slot")
            out[symbol] = [_Template(feat)]
            feats.append(feat)
        return out, _signature(feats, TEMPLATE_PX)

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
        self.signature = _signature(feats, board_img.size[0] / 8.0)
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
            self.signature = _signature(feats, board_img.size[0] / 8.0)
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
        here = _signature(_board_features(board_img, levels),
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

    def _floor(self, feats, board_img):
        """MIN_OVERLAP, or the mistrusted floor when these templates were drawn
        from a different piece set than the one on the board."""
        here = _signature(feats, board_img.size[0] / 8.0)
        return MIN_OVERLAP if _trusted(self.signature, here) else MISTRUST_OVERLAP

    def classify(self, board_img):
        """Read the whole board. Returns 8 rows of piece letters and dots, plus
        the weakest match score, which says how much to trust it.

        Every square is reduced first and scored after, rather than one at a
        time, because the trust test needs to have seen the whole board before
        any square is named and reducing is the expensive half.
        """
        rows = [["."] * 8 for _ in range(8)]
        weakest = 1.0
        if not self.ready:
            return rows, 0.0

        levels = _levels(board_img)
        feats = _board_features(board_img, levels)
        floor = self._floor(feats, board_img)
        for (r, c, _), feat in zip(squares(board_img), feats):
            symbol, score = _judge(feat, self.templates, floor)
            if symbol == ".":
                continue
            if symbol is None:
                rows[r][c] = "?"
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
                      self._floor(feats, board_img))[0]
