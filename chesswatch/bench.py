"""One measuring stick for any way of telling chess pieces apart.

Everything the reader has ever been measured on is two piece sets, both cut
from screenshots taken from white's side. That is too narrow to tell a reader
that works from one that has memorised two sets, and it is why a set the owner
actually plays on read five squares as unreadable while every test passed.

So: four genuinely different designs, both orientations, eight ways a set or a
capture can differ, several sizes, and positions from the opening to a bare
king. Ground truth is free because the position is chosen before it is drawn.

An entrant is any object with

    learn(board_img, board, flipped) -> bool
    classify(board_img)             -> 8 rows of piece letters, "." or "?"

which is the shape pieces.PieceReader already has, so the reader in the tree is
always the baseline to beat.

Scored on three numbers, and they are not interchangeable. A wrong piece goes
into a permanent game record; an unreadable square is retried a second later.
An entrant that reads more squares by guessing has not won anything.

Run:  python bench.py [name-of-entrant]
"""

import os
import sys
import time

import chess
from PIL import Image

import setgen
import watcher as W

SIZES = (824, 560, 400, 280)

# The program draws its own arrow on the board it is reading. overlay.py picks a
# colour that greys into the band read_occupancy ignores, so the shipped reader
# cannot see it, and the docstring there states that as the safety argument. A
# matcher that measures edges rather than levels is not protected by it at all,
# so a bench without the arrow flatters exactly the designs that would break.
ARROWS = (None, "e2e4", "g1f3")

POSITIONS = {
    "opening": [],
    "developed": ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6", "O-O",
                  "Be7", "Re1", "b5", "Bb3", "O-O", "c3", "d6"],
    "middlegame": ["d4", "Nf6", "c4", "e6", "Nc3", "Bb4", "e3", "O-O", "Bd3",
                   "d5", "Nf3", "c5", "O-O", "Nc6", "a3", "Bxc3", "bxc3",
                   "dxc4", "Bxc4", "Qc7"],
    "endgame": ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Bxc6", "dxc6", "Nxe5",
                "Qd4", "Nf3", "Qxe4+", "Qe2", "Qxe2+", "Kxe2", "Bg4", "d3",
                "O-O-O", "Be3", "Bxf3+", "Kxf3", "Nf6"],
}


def board_of(name):
    b = chess.Board()
    for san in POSITIONS[name]:
        b.push_san(san)
    return b


def _real_sets():
    """The two sets cut from real screenshots, as renderers."""
    from fakeboard import Renderer
    out = {}
    ref = chess.Board()
    for san in ("e4", "c5", "d4", "e6"):
        ref.push_san(san)
    for name, shot, board in (("chesscom", "testdata/1.png", ref),
                              ("flat", "testdata/6.png", chess.Board())):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), shot)
        if not os.path.exists(path):
            continue
        rect = W.find_board(Image.open(path).convert("RGB"))
        out[name] = Renderer(path, rect, board=board)
    return out


def _draw_arrow(img, uci, bad=False):
    """A coach arrow, as the overlay paints it on the live board. bad picks the
    move to avoid rather than the move to play, which is the other colour."""
    import chess as _c
    import overlay as OV
    from PIL import ImageDraw
    size = img.size[0]
    region = (0, 0, size, size)
    move = _c.Move.from_uci(uci)
    pts = OV.path_points(region, move, False)
    out = img.copy()
    pen = ImageDraw.Draw(out)
    # bad picks the move-to-avoid arrow, which is the other colour and thinner.
    colour = OV.colour_for(bad)
    width = max(2, int(round(size / 8.0 * (OV.BAD_WEIGHT if bad
                                           else OV.WEIGHT))))
    pen.line([c for p in pts for c in p], fill=colour, width=width,
             joint="curve")
    radius = width / 2.0
    for px, py in pts:
        pen.ellipse([px - radius, py - radius, px + radius, py + radius],
                    fill=colour)
    return out


def corpus(sizes=SIZES, variants=setgen.VARIANTS, quick=False, arrows=ARROWS):
    """Every (set name, orientation, position, variant, size) board to score,
    with the position that drew it.

    Yielded rather than built, because the whole sweep is thousands of pictures
    and holding them costs more than making them twice.
    """
    fonts = [f for f in setgen.fonts() if setgen.readable(f)]
    real = _real_sets()
    names = sorted(real) + [os.path.basename(f).split(".")[0] for f in fonts]
    if quick:
        sizes, variants = (824, 400), ("plain", "small", "blur")
    for flipped in (False, True):
        for pos in POSITIONS:
            board = board_of(pos)
            for size in sizes:
                for kind in variants:
                    for name in names:
                      for arrow in arrows:
                        if name in real:
                            img = real[name].render(board, flipped)
                            if img.size[0] != size:
                                img = img.resize((size, size), Image.LANCZOS)
                        else:
                            font = next(f for f in fonts
                                        if os.path.basename(f).startswith(name))
                            img = setgen.render(board, font, size, flipped)
                        img = setgen.variants(img, kind)
                        if arrow:
                            img = _draw_arrow(img, arrow)
                        yield name, flipped, pos, kind, size, img, board, arrow


def teach(entrant, name, flipped, sizes=SIZES):
    """Show the entrant a starting position in this set, the way the program
    does when a game begins. Taught at the largest size only, then asked about
    every size, because that is what happens when a window is resized."""
    fonts = [f for f in setgen.fonts() if setgen.readable(f)]
    real = _real_sets()
    start = chess.Board()
    if name in real:
        img = real[name].render(start, flipped)
    else:
        font = next(f for f in fonts if os.path.basename(f).startswith(name))
        img = setgen.render(start, font, sizes[0], flipped)
    return entrant.learn(img, start, flipped)


def score(entrant_factory, quick=False, verbose=True, colours=False):
    """Run one entrant over the whole corpus. Returns the totals and a
    breakdown, and never lets a crash on one board pass as a good result.

    colours scores W/B/. against the position instead of piece letters, which is
    what the occupancy reader answers.
    """
    totals = {"right": 0, "wrong": 0, "unknown": 0, "crashed": 0}
    by = {}
    taught = {}
    started = time.time()
    frames = {"clean": 0, "refused": 0, "wrong": 0}
    for name, flipped, pos, kind, size, img, board, arrow in corpus(quick=quick):
        key = (name, flipped)
        if key not in taught:
            e = entrant_factory()
            taught[key] = (e, teach(e, name, flipped))
        entrant, ok = taught[key]
        want = (W.occupancy_of(board, flipped) if colours
                else W.grid_of(board, flipped))
        try:
            rows = entrant.classify(img)
            if isinstance(rows, tuple):
                rows = rows[0]
        except Exception:
            totals["crashed"] += 1
            continue
        cell = by.setdefault((name, flipped, kind), [0, 0, 0])
        blank = bad = 0
        for r in range(8):
            for c in range(8):
                got = rows[r][c]
                if got == "?":
                    totals["unknown"] += 1; cell[2] += 1; blank += 1
                elif got == want[r][c]:
                    totals["right"] += 1; cell[0] += 1
                else:
                    totals["wrong"] += 1; cell[1] += 1; bad += 1
        # watcher.check() takes a frame all or nothing: any "?" and the whole
        # board is thrown away, otherwise every square is believed. So a wrong
        # square only reaches a game record on a board with no blanks, and per
        # square figures hide how often that happens.
        if blank:
            frames["refused"] += 1
        elif bad:
            frames["wrong"] += 1
        else:
            frames["clean"] += 1
    totals["frames"] = frames
    totals["seconds"] = round(time.time() - started, 1)
    if verbose:
        report(totals, by)
    return totals, by


def report(totals, by):
    n = totals["right"] + totals["wrong"] + totals["unknown"]
    print("\n%d squares in %.0fs   right %.2f%%   WRONG %.2f%%   unknown %.2f%%"
          % (n, totals["seconds"], 100.0 * totals["right"] / max(n, 1),
             100.0 * totals["wrong"] / max(n, 1),
             100.0 * totals["unknown"] / max(n, 1)))
    f = totals.get("frames")
    if f:
        n = f["clean"] + f["refused"] + f["wrong"]
        print("boards: %.1f%% read whole, %.1f%% refused, %.2f%% ACCEPTED WRONG"
              % (100.0 * f["clean"] / max(n, 1), 100.0 * f["refused"] / max(n, 1),
                 100.0 * f["wrong"] / max(n, 1)))
    if totals["crashed"]:
        print("   %d boards crashed the entrant" % totals["crashed"])
    print("\n  set          side   variant      right  wrong  unknown")
    for (name, flipped, kind), (r, w, u) in sorted(by.items()):
        print("  %-12s %-6s %-12s %5d  %5d  %7d"
              % (name, "black" if flipped else "white", kind, r, w, u))


def baseline():
    import pieces as P
    return P.PieceReader()


class Occupancy:
    """watcher.read_occupancy as an entrant, so the fast reader can be measured
    on the same boards as the slow one. There is nothing for it to learn, and it
    answers colours, so it is scored with colours=True."""

    def learn(self, board_img, board, flipped):
        return True

    def classify(self, board_img):
        return W.read_occupancy(board_img)


if __name__ == "__main__":
    quick = "--quick" in sys.argv
    if "--occupancy" in sys.argv:
        print("scoring the occupancy reader in the tree")
        score(Occupancy, quick=quick, colours=True)
    else:
        print("scoring the piece reader in the tree")
        score(baseline, quick=quick)
