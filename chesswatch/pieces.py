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


# Same cutoffs the occupancy reader uses, so the two always agree on what counts
# as a piece pixel.
from watcher import BRIGHT, DARK, MIN_COVERAGE, grid_of


def _bits(mask):
    return bin(mask).count("1")


def _mask(square_img):
    """A square as one integer, one bit per pixel of the 40x40 grid.

    A bitset rather than a list of bytes because a board is now scored against
    all twelve templates instead of six, and & and | do a whole 1600 pixel
    intersection in one operation. On the 824px reference board that is 14 ms
    per board against the 26 ms the old pairwise loop took over half as many
    templates.
    """
    grey = square_img.convert("L").resize((NORM, NORM), Image.NEAREST)
    return int("".join("1" if (v > BRIGHT or v < DARK) else "0"
                       for v in grey.getdata()), 2)


def _coverage(mask):
    return _bits(mask) / (NORM * NORM)


def _is_white(square_img):
    grey = square_img.convert("L").resize((NORM, NORM), Image.NEAREST)
    px = list(grey.getdata())
    return sum(1 for v in px if v > BRIGHT) > sum(1 for v in px if v < DARK)


def _overlap(a, b):
    """Intersection over union of two binary masks."""
    union = _bits(a | b)
    return _bits(a & b) / union if union else 0.0


def squares(board_img, size=None):
    """Yield (row, col, square image), row 0 being the top of the screen."""
    size = size or board_img.size[0]
    step = size / 8.0
    for r in range(8):
        for c in range(8):
            yield r, c, board_img.crop((int(c * step), int(r * step),
                                        int((c + 1) * step), int((r + 1) * step)))


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

        Same rules as learn(), but the failure is not silent. Templates learned
        at a size that is no longer on screen are worse than the bundled sheet,
        because every mask is normalised by resampling and the scores drop, so
        a relearn that cannot see all twelve pieces falls back to the sheet.
        """
        if self.learn(board_img, board, flipped):
            return True
        self.use_bundled()
        return False

    def stale(self, board_size, tolerance=0.03):
        """True when the board on screen is no longer the size the templates
        were learned at, which is the cue to relearn. The bundled sheet is never
        stale, it was not learned from any particular window."""
        if self.learned_size is None:
            return False
        return abs(board_size - self.learned_size) > self.learned_size * tolerance

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
        size = board_img.size[0]
        step = size / 8.0
        sq = board_img.crop((int(col * step), int(row * step),
                             int((col + 1) * step), int((row + 1) * step)))
        return _decide(sq, self.templates)[0]
