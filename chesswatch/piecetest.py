"""Headless checks for the piece reader specifically.

selftest.py checks that the two reference screenshots read correctly. This
checks the harder thing: that they keep reading correctly when the capture is
imperfect, and that the reader says "?" instead of naming a piece when it is
not sure. Every floor below is a number that was actually measured, not a
target, so a drop here is a real regression and not a moved goalpost. The one
deliberate slack is the speed ceiling at the end: 30 ms against readings of 7
to 15 ms, so that a slower machine does not read as a slowdown in this code.

Run:  python piecetest.py
"""

import os
import sys
import tempfile
import time

import chess
from PIL import Image, ImageEnhance, ImageFilter

import pieces as P
import watcher as W
from shots import shot

# 1.png is the position after 1.e4 c5 2.d4 e6 on a maximised window. 5.png is a
# small window over a wallpaper, white to mate, with the last-move highlight on
# h7 and the check marker on h4, both of which sit inside the piece mask cutoffs
# and so must not disturb anything.
TRUTH = {
    "1": ["rnbqkbnr", "pp.p.ppp", "....p...", "..p.....",
          "...PP...", "........", "PPP..PPP", "RNBQKBNR"],
    "5": ["R.....Q.", ".......R", ".p......", ".N.B....",
          "...P...k", "........", ".PPK.P.P", "........"],
}


def check(name, got, want):
    ok = got == want
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        print("        got  ", got)
        print("        want ", want)
    return ok


def board_of(name):
    img = Image.open(shot(name)).convert("RGB")
    box = W.find_board(img)
    if not box:
        return None
    x, y, size = box
    return img.crop((x, y, x + size, y + size))


def tally(reader, cases):
    """Correct, wrong and unknown squares over a list of (name, board image).

    Unknown is counted apart from wrong on purpose. A "?" costs a check pass,
    which is retried a second later; a wrong piece goes into the game record.
    """
    correct = wrong = unknown = 0
    for name, board_img in cases:
        rows, _ = reader.classify(board_img)
        for r in range(8):
            for c in range(8):
                got, want = rows[r][c], TRUTH[name][r][c]
                if got == "?":
                    unknown += 1
                elif got == want:
                    correct += 1
                else:
                    wrong += 1
    return correct, wrong, unknown


def shrunk(boards):
    """The same boards in a smaller browser window. Templates are stored at
    96px, so this is the resampling the reader has to survive."""
    out = []
    for name, b in boards:
        for size in (560, 400, 280, 200):
            out.append((name, b.resize((size, size), Image.LANCZOS)))
    return out


def offset(boards):
    """The same boards cropped a few pixels out, which is what find_board gets
    wrong when the window edge antialiases. Every square is then part of its
    neighbour, so this is where the reader used to invent pieces."""
    out = []
    for name, b in boards:
        size = b.size[0]
        for dx, dy, ds in [(2, 0, 0), (0, 2, 0), (3, 3, 0), (0, 0, 4),
                           (0, 0, -4), (0, 0, 8), (0, 0, -8), (5, 5, -10)]:
            out.append((name, b.crop((dx, dy, dx + size + ds, dy + size + ds))))
    return out


def distorted(boards):
    """A screen that is not at the reference brightness, and the blur display
    scaling adds. This one is genuinely hard: the bright and dark cutoffs the
    mask is built from are absolute, so a brightness shift moves what counts as
    a piece pixel at all."""
    out = []
    for name, b in boards:
        for f in (0.85, 0.92, 1.08, 1.15):
            out.append((name, ImageEnhance.Brightness(b).enhance(f)))
        for f in (0.80, 0.90, 1.25):
            out.append((name, ImageEnhance.Contrast(b).enhance(f)))
        for radius in (1.0, 1.5, 2.5):
            out.append((name, b.filter(ImageFilter.GaussianBlur(radius))))
    return out


def main():
    r = []
    reader = P.PieceReader()
    r.append(check("templates load from the bundled sheet",
                   (reader.ready, reader.source), (True, "bundled")))

    boards = []
    for name in ("1", "5"):
        b = board_of(name)
        if b is None:
            print("FAIL  cannot find the board in %s.png" % name)
            return 1
        boards.append((name, b))

    # -- accuracy floors ---------------------------------------------------
    r.append(check("both reference boards read perfectly",
                   tally(reader, boards), (128, 0, 0)))

    got = tally(reader, shrunk(boards))
    r.append(check("nothing wrong down to a 200px window", got[1], 0))
    r.append(check("  and 495 of 512 squares still named", got[0] >= 495, True))

    got = tally(reader, offset(boards))
    print("      misaligned crops: %d correct, %d wrong, %d unclear" % got)
    r.append(check("a misaligned crop yields at most one wrong piece",
                   got[1] <= 1, True))
    r.append(check("  and still names 802 of 1024 squares", got[0] >= 802, True))

    got = tally(reader, distorted(boards))
    print("      wrong brightness or blur: %d correct, %d wrong, %d unclear" % got)
    r.append(check("distortion costs at most 91 wrong pieces of 1280",
                   got[1] <= 91, True))
    r.append(check("  and still names 924 of 1280 squares", got[0] >= 924, True))

    # -- the margin is what does that -------------------------------------
    # On a clean board the closest correct call is a rook beating a bishop by
    # 0.130, so the margin has to stay well under that or good boards start
    # coming back unclear.
    tight = 1.0
    for name, b in boards:
        for row, col, sq in P.squares(b):
            if TRUTH[name][row][col] == ".":
                continue
            ranked = sorted(((P._overlap(P._mask(sq), t), s)
                             for s, t in reader.templates.items()), reverse=True)
            tight = min(tight, ranked[0][0] - ranked[1][0])
    print("      closest correct call on a clean board: %.3f" % tight)
    r.append(check("the margin leaves clean boards room to spare",
                   P.MIN_MARGIN < tight / 2, True))

    # -- the colour veto catches what the margin does not -----------------
    # Turn the contrast on 1.png up to 1.25 and the black back rank comes out
    # light enough that the shape match lands on the white template of the
    # right piece. b8 scores the white knight 0.644 against 0.588 for its
    # runner up, so the margin is satisfied and nothing but the bright-versus-
    # dark pixel count keeps a white knight off a black square. Nine squares
    # over the whole distorted set land this way.
    harsh = ImageEnhance.Contrast(boards[0][1]).enhance(1.25)
    b8 = next(sq for row, col, sq in P.squares(harsh) if (row, col) == (0, 1))
    ranked = sorted(((P._overlap(P._mask(b8), t), s)
                     for s, t in reader.templates.items()), reverse=True)
    r.append(check("b8 at 1.25 contrast scores the white knight clear of the field",
                   (ranked[0][1], ranked[0][0] - ranked[1][0] > P.MIN_MARGIN,
                    TRUTH["1"][0][1]), ("N", True, "n")))
    r.append(check("  so only the colour veto stops _decide naming it",
                   P._decide(b8, reader.templates)[0], None))

    # -- unclear rather than wrong ----------------------------------------
    # Two pixels of crop error is the case the margin is for. Scoring six
    # templates of one colour picks a black bishop for both black knights and
    # is confident about it; scoring all twelve puts a bishop and a knight
    # within the margin of each other, so the two squares come back unclear.
    # A badly sliced board is not this case: it falls below MIN_OVERLAP and
    # came back all "?" before this branch as well.
    name, b = boards[0]
    nudged = b.crop((2, 0, 2 + b.size[0], b.size[0]))
    rows, weakest = reader.classify(nudged)
    r.append(check("two pixels of crop error make both knights unclear, not bishops",
                   (rows[0][1], rows[0][6], weakest), ("?", "?", 0.0)))
    r.append(check("  and nothing it does name on that board is wrong",
                   [rows[row][col] for row in range(8) for col in range(8)
                    if rows[row][col] not in ("?", TRUTH[name][row][col])], []))

    # The "?" is the only thing that refuses it. A wrong letter is stopped
    # nowhere downstream: the same board with both knights read as bishops is
    # a position the rules allow and would be recorded as one.
    guessed = [list(row) for row in TRUTH[name]]
    guessed[0][1] = guessed[0][6] = "b"
    r.append(check("  and a \"?\" is refused where the wrong bishop would not be",
                   (W.board_from_grid(rows), W.board_from_grid(guessed) is not None),
                   (None, True)))

    # -- relearning and falling back --------------------------------------
    from fakeboard import Renderer
    render = Renderer(shot("1"), W.find_board(Image.open(shot("1")).convert("RGB")))
    start = chess.Board()
    r.append(check("learns from a starting position",
                   (reader.learn(render.render(start), start), reader.source),
                   (True, "learned from your screen")))
    r.append(check("  and remembers the size it learned at",
                   reader.learned_size, render.size))
    r.append(check("  templates learned at one size still read another",
                   tally(reader, boards), (128, 0, 0)))

    # Staleness is not "the window changed size". A set learned at or above
    # MIN_LEARN_PX read every board from 200px to 1600px with no loss against
    # the sheet, in either direction, so a resize alone is no reason to drop it.
    r.append(check("a set learned at a usable size is never stale",
                   [reader.stale(s) for s in (200, render.size // 2,
                                              render.size, render.size * 2)],
                   [False, False, False, False]))

    # Under the floor the square held fewer screen pixels than the 40x40 grid
    # its mask is compared on, so the template was an upsample at birth and
    # stays coarse. That is the one set worth throwing away, and only once the
    # board outgrows it: shrinking never made a wrong piece in the study.
    small = P.MIN_LEARN_PX - 8
    tiny = P.PieceReader()
    tiny.learn(render.render(start).resize((small, small), Image.LANCZOS), start)
    r.append(check("  but one learned under the floor is, once the board grows",
                   (tiny.stale(small * 2), tiny.stale(small), tiny.stale(small // 2)),
                   (True, False, False)))

    # Learning needs all twelve piece types, so an endgame cannot teach them.
    endgame = chess.Board("8/8/4k3/8/8/4K3/4P3/8 w - - 0 1")
    r.append(check("a relearn that cannot see twelve pieces keeps a good set",
                   (reader.relearn(render.render(endgame), endgame),
                    reader.source, reader.learned_size),
                   (False, "learned from your screen", render.size)))
    r.append(check("  and still reads both boards",
                   tally(reader, boards), (128, 0, 0)))
    r.append(check("  but an undersized set is dropped for the sheet",
                   (tiny.relearn(render.render(endgame), endgame),
                    tiny.source, tiny.learned_size),
                   (False, "bundled", None)))
    # Nothing was learned from a window, so no window size can make it stale.
    # Without this the caller relearns on every frame after a fallback.
    r.append(check("  and a reader back on the sheet is never stale at any size",
                   [tiny.stale(s) for s in (200, render.size, render.size * 3)],
                   [False, False, False]))

    # A sheet with slots missing has to fail rather than load. crop() pads out
    # of bounds with black, and black counts as a piece pixel, so the seven
    # missing slots used to load as solid masks with a coverage of 1.0, which
    # matched anything: a five piece sheet read every white pawn on 1.png as a
    # rook without complaining once.
    with tempfile.TemporaryDirectory() as tmp:
        narrow = os.path.join(tmp, "short.png")
        Image.open(P.TEMPLATE_SHEET).crop(
            (0, 0, 5 * P.TEMPLATE_PX, P.TEMPLATE_PX)).save(narrow)
        short = P.PieceReader(narrow)
        r.append(check("a sheet with pieces missing is refused, not padded",
                       (short.ready, short.source), (False, "none")))

    # -- speed -------------------------------------------------------------
    # The best of twenty warmed passes, not the mean of five cold ones. A mean
    # over a handful of runs tracks what else the machine is doing more than it
    # tracks this code: with six other processes competing it came out 46%
    # slower, which is enough to hide a real speedup or invent a regression.
    # The best case moves only when the work per board does.
    reader.classify(boards[0][1])
    runs = []
    for _ in range(20):
        t0 = time.perf_counter()
        reader.classify(boards[0][1])
        runs.append(time.perf_counter() - t0)
    per = min(runs) * 1000
    print("\n      classify: %.0f ms per 824px board, best of 20" % per)
    r.append(check("  the fastest pass over an 824px board stays under 30 ms",
                   per < 30, True))

    print("\n%d/%d passed" % (sum(bool(x) for x in r), len(r)))
    return 0 if all(r) else 1


if __name__ == "__main__":
    sys.exit(main())
