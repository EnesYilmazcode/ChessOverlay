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

It says nothing rather than something doubtful. A square the reader cannot
settle comes back as "?" and is retried a second later, but a wrongly chosen
set is doubted by nothing downstream: every square is then read against foreign
templates and the reader's own gate cannot tell that from a hard board. So the
winner has to beat the runner up by MIN_CONFIDENCE or select() names no set at
all.

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

NORM = 40                 # size every mask is compared at

# The sheet layout is shared with pieces.py rather than restated, because a
# bank sheet has to be loadable by both. Restating it let the two disagree:
# this module used to take the slot size from the sheet's own height while
# pieces.py reads a fixed 96, so a sheet cut at any other size loaded here and
# read off the wrong pixels there, silently and only in one of them.
from pieces import ORDER, TEMPLATE_PX as SLOT_PX

# Fewer pieces than this on the board and the answer is refused. Not an
# accuracy floor: measured over the corpus banktest.py builds, a board holding
# one piece still picks the right set every time and by a margin of 0.110,
# which is no worse than a full one. It is the number of pieces a chess
# position has to have. Two kings is the minimum the rules allow, so a
# rectangle with less than that on it is not a board, and a pick made from one
# stray bright artefact is worth refusing whatever it scores.
MIN_PIECES = 2

# And the winner has to be this far clear of the runner up, or no set is
# named. Measured over 2772 boards, the whole corpus banktest.py builds and
# then the same corpus at ten wrong brightness, contrast and blur settings:
# 154 boards picked the wrong set and the most confident of them managed
# 0.0444, while the worst margin on any of the 252 undistorted boards was
# 0.1212. So 0.05 refuses every wrong pick that was ever made and costs nothing
# on a board captured as it looks.
#
# The band is a factor of 2.7 and not more, and it is a floor on the margin
# between two sets, not on how well either fits. A set that is not in the bank
# at all can still clear it. See select().
MIN_CONFIDENCE = 0.05

# The cutoffs the occupancy reader uses today, not a shared definition of a
# piece pixel. pieces.py is moving to levels measured off the board in front of
# it so that a board theme stops mattering, and after that this module is the
# one still thresholding at a fixed 244 and 70.
#
# That is a real cost and it is measured, not waved away: the 154 wrong picks
# banktest.py finds are all boards captured at a brightness these cutoffs were
# not set for. MIN_CONFIDENCE refuses every one of them, so the failure is a
# refusal rather than a wrong sheet, which is why this is worth shipping ahead
# of the fix. Taking the levels from the board is the fix, and it belongs here
# too once pieces.py has it to share.
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
    """Load one set from a twelve slot sheet. Raises if the sheet is not one.

    The slot size is SLOT_PX and is not taken from the sheet, so a sheet of
    some other size is refused here rather than being read correctly here and
    off the wrong pixels by pieces.py, which reads a fixed 96.

    Both checks matter because crop() pads out of bounds with black and black
    counts as a piece pixel, so a sheet that is short or the wrong height would
    load as solid masks that match everything rather than fail.
    """
    img = Image.open(path).convert("RGB")
    if img.size[1] != SLOT_PX or img.size[0] < len(ORDER) * SLOT_PX:
        raise ValueError("%s is not a %d by %d sheet of twelve pieces"
                         % (path, len(ORDER) * SLOT_PX, SLOT_PX))
    return {symbol: _layers(img.crop((i * SLOT_PX, 0,
                                      (i + 1) * SLOT_PX, SLOT_PX)))
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

    Returns (name, confidence). name is None when the answer is refused, and
    the confidence is returned either way so a caller can see how close it
    came. Refusing rather than guessing is the same rule pieces.py reads a
    square by, and for a stronger reason: a square the pixels do not settle
    comes back as "?" and is retried a second later, but a wrongly chosen set
    is not doubted by anything downstream. Every square then gets read against
    foreign templates, and pieces.py's own gate cannot tell that from a hard
    board, because the shapes it is comparing are the only shapes it has.

    Two ways to be refused. Too few pieces to be a chess position at all, and
    the two best sets too close together to separate. Confidence is how far
    clear of the runner up the winner came, so a bank holding one set always
    reports 0.0 and is always refused: one set was never a choice, and a caller
    that wants it anyway should ask the bank for it by name.
    """
    ranked = rank(board_img, sets)
    if not ranked:
        return None, 0.0
    runner_up = ranked[1][1] if len(ranked) > 1 else ranked[0][1]
    confidence = ranked[0][1] - runner_up
    if confidence < MIN_CONFIDENCE:
        return None, confidence
    return ranked[0][0], confidence


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
