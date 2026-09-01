"""Identify which piece is on each square, from pixels alone.

The occupancy reader in watcher.py only tells white from black, which is enough
to follow a game move by move but not enough to read a position cold. This adds
the missing half: shape matching against piece templates.

A square is reduced to a binary mask of its very bright and very dark pixels.
Board colours, the last-move highlight, the check marker and the coordinate
labels all fall between those two cutoffs, so the mask is the piece and nothing
else, whatever the square underneath looks like. Masks are then compared by
overlap. All twelve templates are scored and the winner has to beat the runner
up by a margin, so a square the pixels do not settle comes back as "?" instead
of as a confident wrong piece.

Templates come from `pieces.png` to start with, and are relearned from your own
screen the moment a starting position appears, which makes them exact for your
window size.
"""

import os

import chess
from PIL import Image

APP_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_SHEET = os.path.join(APP_DIR, "pieces.png")

ORDER = "KQRBNPkqrbnp"
TEMPLATE_PX = 96          # size each template is stored at
NORM = 40                 # size every mask is compared at
MIN_OVERLAP = 0.30        # below this, call it unrecognised rather than guess

# And the winner has to be this far clear of the runner up. Measured over the
# two reference screenshots put through the twenty-two variations piecetest.py
# builds: on the boards as captured the closest correct call is a rook beating
# a bishop by 0.130, so 0.05 costs nothing there, and it costs nothing either
# down to a 200px window. What it buys is on boards the reader gets a bad crop
# of, where wrong answers drop from 15 in 1024 squares to 1, and on boards
# captured at the wrong brightness, where they drop from 221 in 1280 to 91.
# Anything past 0.13 starts eating correct answers on a clean board, so the
# usable band is narrow and 0.05 sits well inside it.
MIN_MARGIN = 0.05

# Templates learned on a board smaller than this were upsampled into the NORM
# grid at birth, and stay coarse however big the board later gets. See stale().
MIN_LEARN_PX = NORM * 8

# Confirming one named piece rather than choosing among twelve, which is a
# question that survives something being drawn over the square. See _confirms.
# Measured over 10078 squares that classify gave up on, taken from four games
# under a mouse pointer, a two, three and four square hover tooltip, a two
# pixel crop error and a board shrunk to 280px: at these numbers 3441 of those
# squares were filled back in and 6 of the 3441 wrongly, every one of the 6 a
# capture hidden under a tooltip wide enough to cover the whole square.
# CONFIRM_EMPTY is the tight one. At 0.08 a real piece starts passing as an
# empty square and the 6 becomes 33; at 0.02 it costs 2214 correct fills and
# saves none. CONFIRM_CONTAIN is flat anywhere from 0.70 to 0.80 and only
# starts costing fills past 0.85.
CONFIRM_CONTAIN = 0.80
CONFIRM_MARGIN = 0.05
CONFIRM_EMPTY = 0.05


# Same cutoffs the occupancy reader uses, so the two always agree on what counts
# as a piece pixel.
from watcher import BRIGHT, DARK, MIN_COVERAGE, grid_of


_PIECE_LUT = [255 if (v > BRIGHT or v < DARK) else 0 for v in range(256)]


def _mask(square_img):
    """A square as one integer, one bit per pixel of the 40x40 grid.

    A bitset rather than a list of bytes so that & and | intersect all 1600
    pixels at once. That is where the speed is, and all of it lands in
    _overlap: scoring the 824px reference board against twelve templates went
    from 50 ms to 0.2 ms. Building the mask is no part of that win. Spelled as
    a string of ones and zeros it was slower than the byte list it replaced,
    and point() only gets that back by thresholding in C, 7.9 ms a board down
    to 2.4 ms.

    Rows of a mode "1" image pad up to a whole byte, and Pillow zeroes the
    padding, so a NORM that is not a multiple of 8 changes what the integer is
    without changing any score: zero bits add nothing to an and, an or or a
    popcount.
    """
    grey = square_img.convert("L").resize((NORM, NORM), Image.NEAREST)
    return int.from_bytes(grey.point(_PIECE_LUT, "1").tobytes(), "big")


def _coverage(mask):
    return mask.bit_count() / (NORM * NORM)


def _is_white(square_img):
    grey = square_img.convert("L").resize((NORM, NORM), Image.NEAREST)
    px = list(grey.getdata())
    return sum(1 for v in px if v > BRIGHT) > sum(1 for v in px if v < DARK)


def _overlap(a, b):
    """Intersection over union of two binary masks.

    bit_count() is a C popcount and wants Python 3.10 or newer. Worth the
    floor: bin(mask).count("1") builds a 1600 character string every time, and
    this runs 768 times a board.
    """
    union = (a | b).bit_count()
    return (a & b).bit_count() / union if union else 0.0


def _one_square(board_img, row, col):
    """One square cropped out of a whole board, by screen position."""
    step = board_img.size[0] / 8.0
    return board_img.crop((int(col * step), int(row * step),
                           int((col + 1) * step), int((row + 1) * step)))


def squares(board_img, size=None):
    """Yield (row, col, square image), row 0 being the top of the screen."""
    size = size or board_img.size[0]
    step = size / 8.0
    for r in range(8):
        for c in range(8):
            yield r, c, board_img.crop((int(c * step), int(r * step),
                                        int((c + 1) * step), int((r + 1) * step)))


def _confirms(square_img, templates, symbol):
    """Could this square be holding exactly this piece, with something drawn on
    top of it? Returns True or False, and never a guess at what else it is.

    A narrower question than _decide's, and answerable when that one is not. A
    mouse pointer, a hover tooltip or a neighbour's half dragged piece only ADD
    pixels to a square: the mask counts very dark pixels as well as very bright
    ones, so whatever is drawn over the piece joins the mask rather than
    erasing the piece from it. Overlap divides by the union, so those extra
    pixels sink all twelve scores together and the winner stops beating the
    runner up, which is exactly the "?" this exists to answer. Asking how much
    of ONE template is present divides by that template instead, and nothing
    drawn on top can make that smaller.

    The trap is that a large piece's outline swallows a small one's, so a queen
    standing where a pawn used to would confirm the pawn. Hence the second
    half: the named piece has to be the best fit of the twelve and not merely a
    present one. What survives that is colour, which these masks cannot see at
    all, a black pawn and a white one being the same silhouette. So a pawn
    captured by the other side's pawn is confirmed as still standing. That
    costs a refusal rather than a wrong move, because the capture is then left
    with nowhere to land, but it is why this may only ever confirm a piece
    already believed to be there and may never be used to name one.
    """
    mask = _mask(square_img)
    if symbol == ".":
        return _coverage(mask) < CONFIRM_EMPTY
    if _coverage(mask) < MIN_COVERAGE:
        return False
    present = {}
    for sym, template in templates.items():
        whole = template.bit_count()
        present[sym] = (mask & template).bit_count() / whole if whole else 0.0
    return (present.get(symbol, 0.0) >= CONFIRM_CONTAIN
            and max(present.values()) - present[symbol] < CONFIRM_MARGIN)


def _decide(square_img, templates):
    """The piece on one square. Returns (symbol, score), where symbol is a
    piece letter, "." for an empty square, or None when the pixels do not
    settle it.

    Every template is scored, both colours. This used to score only the six of
    whichever colour a bright-versus-dark pixel count voted for, which made a
    wrong colour reading impossible to recover from: the right answer was never
    compared against. Colour now only has to agree with the shape, and the two
    disagreeing is a reason to say nothing rather than to overrule the shape.
    """
    mask = _mask(square_img)
    if _coverage(mask) < MIN_COVERAGE:
        return ".", 1.0
    ranked = sorted(((_overlap(mask, t), s) for s, t in templates.items()),
                    reverse=True)
    score, best = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    if score < MIN_OVERLAP or score - runner_up < MIN_MARGIN:
        return None, score
    if best.isupper() != _is_white(square_img):
        return None, score
    return best, score


class PieceReader:
    def __init__(self, sheet=TEMPLATE_SHEET):
        self.sheet = sheet
        self.templates = {}
        self.source = "none"
        self.learned_size = None
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
        return {symbol: _mask(img.crop((slot * TEMPLATE_PX, 0,
                                        (slot + 1) * TEMPLATE_PX, TEMPLATE_PX)))
                for slot, symbol in enumerate(ORDER)}

    def use_bundled(self):
        """Go back to the templates that ship with the program. Loading is all
        or nothing, so a sheet that will not read leaves whatever is already in
        use alone instead of half replacing it."""
        try:
            loaded = self._read_sheet(self.sheet)
        except Exception:
            return False
        self.templates = loaded
        self.source = "bundled"
        self.learned_size = None
        return True

    def learn(self, board_img, board, flipped=False):
        """Relearn the templates from a position known to be on screen. Used at
        the start of every game, so the templates match your own rendering.

        All twelve piece types have to be on the board, which in practice means
        the starting position. A game joined part way through never gets past
        that and spends its whole life on the bundled sheet.
        """
        grid = grid_of(board, flipped)
        found = {}
        for r, c, sq in squares(board_img):
            symbol = grid[r][c]
            if symbol != "." and symbol not in found:
                found[symbol] = _mask(sq)
        if len(found) == 12:
            self.templates = found
            self.source = "learned from your screen"
            self.learned_size = board_img.size[0]
            return True
        return False

    def relearn(self, board_img, board, flipped=False):
        """Learn again part way through a game, after the window changed size.

        Same rules as learn(). Failing costs nothing unless what we are already
        holding is worse than the sheet, so the caller can just ask.
        """
        if self.learn(board_img, board, flipped):
            return True
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

    def classify(self, board_img):
        """Read the whole board. Returns 8 rows of piece letters and dots, plus
        the weakest match score, which says how much to trust it."""
        rows = [["."] * 8 for _ in range(8)]
        weakest = 1.0
        if not self.ready:
            return rows, 0.0

        for r, c, sq in squares(board_img):
            symbol, score = _decide(sq, self.templates)
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
        return _decide(_one_square(board_img, row, col), self.templates)[0]

    def confirm(self, board_img, row, col, symbol):
        """Is one square consistent with holding exactly this piece?

        The second half of a two pass read: classify names what it can, and
        whatever it refused is put back to this as a yes or no question about
        the piece already believed to be standing there. Only ever confirms;
        see _confirms for what it cannot see.
        """
        if not self.ready:
            return False
        return _confirms(_one_square(board_img, row, col), self.templates,
                         symbol)
