"""Carry templates for several piece sets and pick the right one off the board.

pieces.py relearns its templates from your own screen, which is exact, but it
needs all twelve piece types visible to do it. A game joined part way through
never qualifies and spends its whole life on the one bundled sheet, cut from
chess.com's own set. On any other set that sheet reads almost nothing.

This is the other half. The bank holds a sheet per piece set. Given a board it
scores every set and returns the one that fits best, which needs no particular
position on the board and so works on the game you just walked in on. Selection
is a much easier question than identification: it does not matter which piece a
square holds, only that some template in the set covers its pixels well, so a
king and a rook on an otherwise empty board are enough to tell one set from
another.

A square is reduced to two binary masks, its very bright pixels and its very
dark pixels, kept apart rather than merged. Merging them fills a white piece's
outline in with its own fill and leaves every piece the same blob, which is
issue #20: the sets separate by two hundredths of an IoU and a retexture eats
that. Kept apart the outline pattern survives, and the sets separate by an
order of magnitude more.

The bank ships two sets because two sets is what the project legitimately has
pixels for. Add your own with:

    python piecebank.py <screenshot.png> <name> [FEN]

Run with no arguments to list what is in the bank.
"""

import os
import sys

import chess
from PIL import Image

APP_DIR = os.path.dirname(os.path.abspath(__file__))
BANK_DIR = os.path.join(APP_DIR, "piecesets")

ORDER = "KQRBNPkqrbnp"
SLOT_PX = 96              # size each template is stored at
NORM = 40                 # size every mask is compared at

# Fewer pieces than this on the board and the answer is refused. Not an
# accuracy floor: measured over the corpus banktest.py builds, a board holding
# one piece still picks the right set every time and by a margin of 0.110,
# which is no worse than a full one. It is the number of pieces a chess
# position has to have. Two kings is the minimum the rules allow, so a
# rectangle with less than that on it is not a board, and a pick made from one
# stray bright artefact is worth refusing whatever it scores.
MIN_PIECES = 2

# Same cutoffs the occupancy reader uses, so every part of the program agrees on
# what counts as a piece pixel.
from watcher import BRIGHT, DARK, MIN_COVERAGE, find_board

# The masking below is deliberately duplicated from pieces.py rather than
# imported. The two want to merge once pieces.py scores split layers as well,
# and that is a single _mask to share; keeping a copy here in the meantime is
# what lets the bank be worked on without waiting on that file.
_BRIGHT_LUT = [255 if v > BRIGHT else 0 for v in range(256)]
_DARK_LUT = [255 if v < DARK else 0 for v in range(256)]
_HALF_LUT = [255 if v >= 128 else 0 for v in range(256)]


def _layers(square_img):
    """A square as two bitsets, its bright pixels and its dark pixels.

    Thresholded at the resolution the square was captured at and only then
    downsampled, which is the other half of issue #20. Resizing first with
    NEAREST makes a two pixel outline survive or vanish on subpixel phase, and
    a piece whose outline came out thin scores against every template alike.
    Area-averaging the binary layer instead keeps a thin outline as a grey band
    that thresholds back to a line.
    """
    grey = square_img.convert("L")
    out = []
    for lut in (_BRIGHT_LUT, _DARK_LUT):
        binary = grey.point(lut).resize((NORM, NORM), Image.BOX)
        out.append(int.from_bytes(binary.point(_HALF_LUT, "1").tobytes(), "big"))
    return tuple(out)


def _coverage(layers):
    return (layers[0] | layers[1]).bit_count() / (NORM * NORM)


def _similarity(a, b):
    """Intersection over union of two squares, both layers pooled.

    Pooled rather than one IoU per layer averaged. Averaged, a layer that is
    empty on both sides has no union and has to fall back to something, and
    scoring that as a perfect match flatters exactly the comparisons that
    carried no evidence. Summing the counts first gives the empty layer no
    weight at all, which is what it earned.
    """
    inter = (a[0] & b[0]).bit_count() + (a[1] & b[1]).bit_count()
    union = (a[0] | b[0]).bit_count() + (a[1] | b[1]).bit_count()
    return inter / union if union else 0.0


def squares(board_img):
    """Yield (row, col, square image), row 0 being the top of the screen."""
    size = board_img.size[0]
    step = size / 8.0
    for r in range(8):
        for c in range(8):
            yield r, c, board_img.crop((int(c * step), int(r * step),
                                        int((c + 1) * step), int((r + 1) * step)))


def occupied(board_img):
    """The masks of every square that holds something. Computed once and scored
    against every set, since the masks do not depend on which set is asked."""
    seen = []
    for _, _, sq in squares(board_img):
        layers = _layers(sq)
        if _coverage(layers) >= MIN_COVERAGE:
            seen.append(layers)
    return seen


class PieceSet:
    """Twelve templates and the sheet they came from.

    sheet is the path a caller can hand to pieces.py once the bank has picked,
    so the reader loads the winning set with its own masking rather than this
    module's.
    """

    def __init__(self, name, templates, sheet=None):
        self.name = name
        self.templates = templates
        self.sheet = sheet

    def __repr__(self):
        return "PieceSet(%r, %d templates)" % (self.name, len(self.templates))

    def fit(self, seen):
        """How well this set explains a board, as the mean over occupied
        squares of the best template's overlap.

        The best template, not the right one. A set is being judged on whether
        it has a shape for what is on the board, and asking which shape would
        be asking the question the bank exists to make answerable.
        """
        if not seen:
            return 0.0
        return sum(max(_similarity(m, t) for t in self.templates.values())
                   for m in seen) / len(seen)


def read_sheet(path):
    """Load one set from a twelve slot sheet. Raises if the sheet is short.

    crop() pads out of bounds with black and black counts as a piece pixel, so
    a truncated sheet would load as solid masks that match everything rather
    than fail.
    """
    img = Image.open(path).convert("RGB")
    slot = img.size[1]
    if img.size[0] < len(ORDER) * slot:
        raise ValueError("%s holds fewer than twelve pieces" % path)
    return {symbol: _layers(img.crop((i * slot, 0, (i + 1) * slot, slot)))
            for i, symbol in enumerate(ORDER)}


def load_bank(directory=BANK_DIR):
    """Every set in the bank, named after its file. A sheet that will not read
    is skipped rather than fatal: one bad file a user dropped in should not
    take the working sets down with it."""
    sets = []
    for filename in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
        if not filename.endswith(".png"):
            continue
        path = os.path.join(directory, filename)
        try:
            sets.append(PieceSet(filename[:-4], read_sheet(path), path))
        except Exception:
            continue
    return sets


def rank(board_img, sets=None):
    """Every set scored on this board, best first, as (name, score) pairs.

    Empty when the board holds too few pieces to tell the sets apart.
    """
    sets = load_bank() if sets is None else sets
    seen = occupied(board_img)
    if len(seen) < MIN_PIECES or not sets:
        return []
    return sorted(((s.name, s.fit(seen)) for s in sets),
                  key=lambda pair: -pair[1])


def select(board_img, sets=None):
    """The piece set this board is drawn in.

    Returns (name, confidence), or (None, 0.0) when the board holds too few
    pieces to say. Confidence is how far clear of the runner up the winner
    came, on the same 0 to 1 scale the scores are on, so a bank of one set
    always reports 0.0: nothing was ruled out, because there was nothing to
    rule out.
    """
    ranked = rank(board_img, sets)
    if not ranked:
        return None, 0.0
    runner_up = ranked[1][1] if len(ranked) > 1 else ranked[0][1]
    return ranked[0][0], ranked[0][1] - runner_up


def sheet_for(name, sets=None):
    """Where the sheet for a named set lives, so a reader can load it."""
    sets = load_bank() if sets is None else sets
    for s in sets:
        if s.name == name:
            return s.sheet
    return None


# ------------------------------------------------------- enrolling a new set

def cut_sheet(img, rect, board, flipped=False):
    """Build a twelve slot sheet from a screenshot of a known position.

    Greyscale, because the masks only ever look at brightness, and a greyscale
    sheet is a third of the bytes of the colour one. Returns (sheet, missing),
    and a sheet with anything missing is not usable.
    """
    x0, y0, size = rect
    step = size / 8.0
    grey = img.convert("L")
    grid = [[(board.piece_at(chess.square(f, r)).symbol()
              if board.piece_at(chess.square(f, r)) else ".")
             for f in (range(7, -1, -1) if flipped else range(8))]
            for r in (range(8) if flipped else range(7, -1, -1))]

    sheet = Image.new("L", (SLOT_PX * len(ORDER), SLOT_PX))
    missing = []
    for slot, symbol in enumerate(ORDER):
        where = next(((r, c) for r in range(8) for c in range(8)
                      if grid[r][c] == symbol), None)
        if where is None:
            missing.append(symbol)
            continue
        row, col = where
        crop = grey.crop((int(x0 + col * step), int(y0 + row * step),
                          int(x0 + (col + 1) * step), int(y0 + (row + 1) * step)))
        sheet.paste(crop.resize((SLOT_PX, SLOT_PX), Image.LANCZOS),
                    (slot * SLOT_PX, 0))
    return sheet, missing


def main(argv):
    if not argv:
        bank = load_bank()
        print("bank at", BANK_DIR)
        for s in bank:
            print("  %-12s %d templates" % (s.name, len(s.templates)))
        if not bank:
            print("  empty")
        print("\nadd one:  python piecebank.py <screenshot.png> <name> [FEN]")
        return 0
    if len(argv) < 2:
        print(__doc__)
        return 2

    screenshot, name = argv[0], argv[1]
    board = chess.Board(argv[2]) if len(argv) > 2 else chess.Board()
    img = Image.open(screenshot).convert("RGB")
    rect = find_board(img)
    if not rect:
        print("No board found in that screenshot.")
        return 1
    print("board at (%d,%d) %dpx, squares %.1fpx" % (rect[0], rect[1], rect[2],
                                                     rect[2] / 8.0))

    sheet, missing = cut_sheet(img, rect, board)
    if missing:
        print("That position is missing:", " ".join(missing))
        return 1

    os.makedirs(BANK_DIR, exist_ok=True)
    path = os.path.join(BANK_DIR, "%s.png" % name)
    sheet.save(path)
    print("wrote", path, sheet.size)

    # Worth saying out loud, because a set enrolled from a screenshot the bank
    # cannot tell apart from one it already holds buys nothing.
    x0, y0, size = rect
    ranked = rank(img.crop((x0, y0, x0 + size, y0 + size)))
    if len(ranked) > 1:
        print("scores on that screenshot: " +
              ", ".join("%s %.3f" % pair for pair in ranked))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
