"""Identify which piece is on each square, from pixels alone.

The occupancy reader in watcher.py only tells white from black, which is enough
to follow a game move by move but not enough to read a position cold. This adds
the missing half: shape matching against piece templates.

A square is reduced to a binary mask of its very bright and very dark pixels.
Board colours, the last-move highlight, the check marker and the coordinate
labels all fall between those two cutoffs, so the mask is the piece and nothing
else, whatever the square underneath looks like. Masks are then compared by
overlap. Colour is decided first, so each match is a 1-of-6 choice.

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


# Same cutoffs the occupancy reader uses, so the two always agree on what counts
# as a piece pixel.
from watcher import BRIGHT, DARK, MIN_COVERAGE, grid_of


def _mask(square_img):
    grey = square_img.convert("L").resize((NORM, NORM), Image.NEAREST)
    return bytes(1 if (v > BRIGHT or v < DARK) else 0 for v in grey.getdata())


def _coverage(mask):
    return sum(mask) / len(mask)


def _is_white(square_img):
    grey = square_img.convert("L").resize((NORM, NORM), Image.NEAREST)
    px = list(grey.getdata())
    return sum(1 for v in px if v > BRIGHT) > sum(1 for v in px if v < DARK)


def _overlap(a, b):
    """Intersection over union of two binary masks."""
    inter = union = 0
    for x, y in zip(a, b):
        if x or y:
            union += 1
            if x and y:
                inter += 1
    return inter / union if union else 0.0


def squares(board_img, size=None):
    """Yield (row, col, square image), row 0 being the top of the screen."""
    size = size or board_img.size[0]
    step = size / 8.0
    for r in range(8):
        for c in range(8):
            yield r, c, board_img.crop((int(c * step), int(r * step),
                                        int((c + 1) * step), int((r + 1) * step)))


class PieceReader:
    def __init__(self, sheet=TEMPLATE_SHEET):
        self.templates = {}
        self.source = "none"
        if os.path.exists(sheet):
            try:
                self._load_sheet(sheet)
            except Exception:
                pass

    @property
    def ready(self):
        return len(self.templates) == 12

    def _load_sheet(self, path):
        img = Image.open(path).convert("RGB")
        for slot, symbol in enumerate(ORDER):
            crop = img.crop((slot * TEMPLATE_PX, 0,
                             (slot + 1) * TEMPLATE_PX, TEMPLATE_PX))
            self.templates[symbol] = _mask(crop)
        self.source = "bundled"

    def learn(self, board_img, board, flipped=False):
        """Relearn the templates from a position known to be on screen. Used at
        the start of every game, so the templates match your own rendering."""
        grid = grid_of(board, flipped)
        found = {}
        for r, c, sq in squares(board_img):
            symbol = grid[r][c]
            if symbol != "." and symbol not in found:
                found[symbol] = _mask(sq)
        if len(found) == 12:
            self.templates = found
            self.source = "learned from your screen"
            return True
        return False

    def classify(self, board_img):
        """Read the whole board. Returns 8 rows of piece letters and dots, plus
        the weakest match score, which says how much to trust it."""
        rows = []
        weakest = 1.0
        for r in range(8):
            rows.append(["."] * 8)
        if not self.ready:
            return rows, 0.0

        for r, c, sq in squares(board_img):
            mask = _mask(sq)
            if _coverage(mask) < MIN_COVERAGE:
                continue
            white = _is_white(sq)
            best, score = None, 0.0
            for symbol, template in self.templates.items():
                if symbol.isupper() != white:
                    continue
                value = _overlap(mask, template)
                if value > score:
                    best, score = symbol, value
            if best and score >= MIN_OVERLAP:
                rows[r][c] = best
                weakest = min(weakest, score)
            else:
                rows[r][c] = "?"
                weakest = 0.0
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
        mask = _mask(sq)
        if _coverage(mask) < MIN_COVERAGE:
            return "."
        white = _is_white(sq)
        best, score = None, 0.0
        for symbol, template in self.templates.items():
            if symbol.isupper() != white:
                continue
            value = _overlap(mask, template)
            if value > score:
                best, score = symbol, value
        return best if score >= MIN_OVERLAP else None
