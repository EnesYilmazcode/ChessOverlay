"""Headless checks for the piece reader specifically.

selftest.py checks that the two reference screenshots read correctly. This
checks the harder thing: that they keep reading correctly when the capture is
imperfect, and that the reader says "?" instead of naming a piece when it is
not sure. Every floor below is a number that was actually measured, not a
target, so a drop here is a real regression and not a moved goalpost.

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
    r.append(check("  and still names 780 of 1024 squares", got[0] >= 780, True))

    got = tally(reader, distorted(boards))
    print("      wrong brightness or blur: %d correct, %d wrong, %d unclear" % got)
    r.append(check("distortion costs at most 91 wrong pieces of 1280",
                   got[1] <= 91, True))
    r.append(check("  and still names 920 of 1280 squares", got[0] >= 920, True))

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

    # -- unclear rather than wrong ----------------------------------------
    # Half a square of one piece against half of another is the case that used
    # to produce a confident answer. It has to produce none.
    name, b = boards[0]
    half = b.crop((b.size[0] // 16, 0, b.size[0] + b.size[0] // 16, b.size[0]))
    rows, weakest = reader.classify(half)
    r.append(check("a board sliced down the middle of every square is unclear",
                   ("?" in "".join("".join(row) for row in rows), weakest),
                   (True, 0.0)))
    r.append(check("  so board_from_grid refuses to build a position from it",
                   W.board_from_grid(rows), None))

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

    r.append(check("a resized board is stale", reader.stale(render.size // 2), True))
    r.append(check("  the same size is not", reader.stale(render.size), False))
    r.append(check("  a few pixels of window edge is not",
                   reader.stale(render.size + 4), False))

    # Learning needs all twelve piece types, so a position joined part way
    # through cannot teach them. That used to leave the stale set in place
    # silently; now it goes back to the sheet.
    endgame = chess.Board("8/8/4k3/8/8/4K3/4P3/8 w - - 0 1")
    r.append(check("a relearn that cannot see twelve pieces falls back",
                   (reader.relearn(render.render(endgame), endgame),
                    reader.source, reader.learned_size),
                   (False, "bundled", None)))
    r.append(check("  and the bundled templates still read both boards",
                   tally(reader, boards), (128, 0, 0)))

    # A sheet with slots missing has to fail rather than load. crop() pads out
    # of bounds with black, and black counts as a piece pixel, so the seven
    # missing slots used to load as solid masks with a coverage of 1.0, which
    # matched anything: a five piece sheet read every white pawn on 1.png as a
    # rook without complaining once.
    narrow = os.path.join(tempfile.mkdtemp(), "short.png")
    Image.open(P.TEMPLATE_SHEET).crop(
        (0, 0, 5 * P.TEMPLATE_PX, P.TEMPLATE_PX)).save(narrow)
    r.append(check("a sheet with pieces missing is refused, not padded",
                   (P.PieceReader(narrow).ready, P.PieceReader(narrow).source),
                   (False, "none")))

    # -- speed -------------------------------------------------------------
    t0 = time.time()
    for _ in range(5):
        reader.classify(boards[0][1])
    per = (time.time() - t0) / 5 * 1000
    print("\n      classify: %.0f ms per 824px board" % per)
    r.append(check("  the piece pass stays cheap enough to run on a timer",
                   per < 60, True))

    print("\n%d/%d passed" % (sum(bool(x) for x in r), len(r)))
    return 0 if all(r) else 1


if __name__ == "__main__":
    sys.exit(main())
