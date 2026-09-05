"""Headless checks for the piece set bank.

The question this answers is the one Tier 5 of issue #20 exists for: given a
board drawn in some piece set, does the bank pick that set. Everything else
here is in service of that number.

The corpus is generated rather than committed. Two piece sets are the only ones
this project has legitimate pixels for, both already in testdata/, and every
board below is rendered out of them by fakeboard.py at test time. That is a few
hundred boards without a single PNG in the repository, and it means the sizes
and positions can be changed by editing a tuple.

One thing the corpus is honest about. The chess.com sheet in the bank was cut
from 1.png, so boards rendered out of 1.png are the sheet being read back to
itself. 5.png is a different screenshot of the same set, at a different window
size, over a wallpaper, and it holds eight of the twelve piece types, so the
boards rendered out of it are the only genuinely independent trials in here and
are counted separately. The flat set has no second screenshot at all, so it has
no independent trials. Said plainly rather than averaged away.

Run:  python banktest.py
"""

import os
import shutil
import sys
import tempfile
import time

import chess
from PIL import Image, ImageEnhance, ImageFilter

import piecebank as B
import watcher as W
from fakeboard import Renderer
from shots import shot

# What each reference screenshot is a picture of. 1.png is after 1.e4 c5 2.d4
# e6 and holds all twelve piece types; 5.png is a mate position and holds
# eight, so it can draw an endgame but not an opening.
FEN1 = "rnbqkbnr/pp1p1ppp/4p3/2p5/3PP3/8/PPP2PPP/RNBQKBNR w KQkq - 0 3"
FEN5 = "R5Q1/7R/1p6/1N1B4/3P3k/8/1PPK1P1P/8 w - - 0 1"

POSITIONS = [
    ("start", chess.Board()),
    ("italian",
     chess.Board("r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 0 1")),
    ("rook ending", chess.Board("8/5ppp/4k3/8/8/4K3/5PPP/R7 w - - 0 1")),
    ("K+R v k", chess.Board("8/8/4k3/8/8/4K3/8/R7 w - - 0 1")),
    ("K+P v k", chess.Board("8/8/4k3/8/8/4K3/4P3/8 w - - 0 1")),
    ("K+B+N v k", chess.Board("8/8/4k3/8/2B5/4KN2/8/8 w - - 0 1")),
    ("K+Q v k+p", chess.Board("8/8/4k3/8/8/2Q1K3/5p2/8 w - - 0 1")),
    ("queen v queen", chess.Board("8/5q2/4k3/8/8/4K3/2Q5/8 w - - 0 1")),
]

# A maximised window down to a browser squeezed into a quarter of a laptop.
SIZES = (824, 664, 560, 400, 280, 200)

SPARSE = 6                 # pieces at or under this and the board is an endgame


def check(name, got, want):
    ok = got == want
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        print("        got  ", got)
        print("        want ", want)
    return ok


def rect_of(name):
    return W.find_board(Image.open(shot(name)).convert("RGB"))


def renderers():
    """(set name, source, independent, renderer) for every fixture that can
    draw. independent says whether the bank's sheet for that set came from
    somewhere else."""
    return [
        ("chesscom", "1.png", False,
         Renderer(shot("1"), rect_of("1"), chess.Board(FEN1))),
        ("chesscom", "5.png", True,
         Renderer(shot("5"), rect_of("5"), chess.Board(FEN5))),
        ("flat", "6.png", False, Renderer(shot("6"), rect_of("6"))),
    ]


def corpus(rends):
    """Every board the fixtures can draw, as (set, source, independent, label,
    sparse, image). Rendered one at a time so the whole corpus is never in
    memory at once."""
    for name, src, independent, rend in rends:
        for label, position in POSITIONS:
            if not rend.can_render(position):
                continue
            sparse = len(position.piece_map()) <= SPARSE
            for flipped in (False, True):
                base = rend.render(position, flipped)
                for size in SIZES:
                    yield (name, src, independent,
                           "%s %s %d%s" % (src, label, size,
                                           " flipped" if flipped else ""),
                           sparse, base.resize((size, size), Image.LANCZOS))


def distortions(img):
    """A screen that is not at the reference brightness, and the blur display
    scaling adds. Hard for this module for the same reason it is hard for
    pieces.py: the bright and dark cutoffs are absolute, so a brightness shift
    moves what counts as a piece pixel at all."""
    for f in (0.85, 0.92, 1.08, 1.15):
        yield ImageEnhance.Brightness(img).enhance(f)
    for f in (0.80, 0.90, 1.25):
        yield ImageEnhance.Contrast(img).enhance(f)
    for radius in (1.0, 1.5, 2.5):
        yield img.filter(ImageFilter.GaussianBlur(radius))


def main():
    r = []
    bank = B.load_bank()
    r.append(check("the bank loads two sets of twelve templates",
                   sorted((s.name, len(s.templates)) for s in bank),
                   [("chesscom", 12), ("flat", 12)]))
    r.append(check("  and each one names the sheet it came from",
                   [os.path.basename(s.sheet) for s in sorted(bank, key=lambda s: s.name)],
                   ["chesscom.png", "flat.png"]))
    if not bank:
        return 1

    rends = renderers()

    # -- the renderer draws boards, not coordinate labels -------------------
    # An empty square is drawn up to 64 times a board, so anything cut along
    # with it is repeated 64 times. The sprites used to come from a5 and a6,
    # which on a board with in-square coordinates are the two squares carrying
    # "5" and "6", and every rendered board came out stamped with them. 1.png
    # has no in-square labels and hid it for as long as it was the only
    # reference; 6.png has them.
    stamped = []
    for name, src, _, rend in rends:
        position = POSITIONS[3][1]                 # K+R v k, 61 empty squares
        img = rend.render(position)
        step = rend.step
        for row in range(8):
            for col in range(8):
                if position.piece_at(chess.square(col, 7 - row)):
                    continue
                square = img.crop((col * step, row * step,
                                   (col + 1) * step, (row + 1) * step))
                colours = square.getcolors(step * step)
                if len(colours) != 1 or colours[0][1] not in (rend.light, rend.dark):
                    stamped.append((src, row, col, len(colours)))
    r.append(check("a rendered empty square is the square colour and nothing else",
                   stamped, []))

    # -- the signature livetest.py and selftest.py pass ---------------------
    plain = Renderer(shot("1"), rect_of("1"))
    r.append(check("the old two argument call still cuts all twelve pieces",
                   (sorted(plain.pieces), plain.size), (sorted("KQRBNPkqrbnp"), 824)))
    r.append(check("  and a reference that is not an opening cuts what it has",
                   sorted(rends[1][3].pieces), sorted("BKNPQRkp")))
    r.append(check("  and says so before being asked to draw the rest",
                   (rends[1][3].can_render(POSITIONS[3][1]),
                    rends[1][3].can_render(chess.Board())), (True, False)))

    # -- the number that matters -------------------------------------------
    right = trials = 0
    sparse_right = sparse_trials = 0
    indep_right = indep_trials = 0
    confidence = []
    misses = []
    for want, src, independent, label, sparse, img in corpus(rends):
        got, conf = B.select(img, bank)
        trials += 1
        sparse_trials += sparse
        indep_trials += independent
        if got == want:
            right += 1
            sparse_right += sparse
            indep_right += independent
            confidence.append((conf, label))
        else:
            misses.append((label, got))
    print("\n      set selection over %d rendered boards: %d right, %d wrong"
          % (trials, right, trials - right))
    print("        endgames of %d pieces or fewer: %d of %d"
          % (SPARSE, sparse_right, sparse_trials))
    print("        boards from a screenshot the sheet did not come from: %d of %d"
          % (indep_right, indep_trials))
    r.append(check("every rendered board picks the set it was drawn in",
                   (right, trials), (252, 252)))
    r.append(check("  including the sparse endgames, which is the joined late case",
                   (sparse_right, sparse_trials), (168, 168)))
    r.append(check("  and the 60 drawn from a screenshot the sheet did not come from",
                   (indep_right, indep_trials), (60, 60)))
    if misses:
        print("      missed:", misses[:6])

    confidence.sort()
    print("      confidence, winner over runner up: %.3f at worst (%s), %.3f at best"
          % (confidence[0][0], confidence[0][1], confidence[-1][0]))
    r.append(check("  never on less than a 0.10 margin over the other set",
                   confidence[0][0] > 0.10, True))

    # -- how little of a board is enough -----------------------------------
    # The joined late case at its worst. Selection does not need a position,
    # only pixels it has a shape for, so the floor is far below what a real
    # board ever shows.
    for label, fen, want in (("one piece", "8/8/8/8/8/8/8/R7 w - - 0 1", 0.109),
                             ("two", "8/8/4k3/8/8/4K3/8/8 w - - 0 1", 0.126),
                             ("three", "8/8/4k3/8/8/4K3/8/R7 w - - 0 1", 0.121)):
        position = chess.Board(fen)
        ok = n = 0
        gap = 1.0
        for name, src, _, rend in rends:
            if not rend.can_render(position):
                continue
            for size in SIZES:
                ranked = sorted(((s.name, s.fit(B.occupied(rend.render(position, size=size))))
                                 for s in bank), key=lambda pair: -pair[1])
                n += 1
                ok += ranked[0][0] == name
                gap = min(gap, ranked[0][1] - ranked[1][1])
        print("      margin at worst: %.3f" % gap)
        r.append(check("a board holding %s picks the set it was drawn in" % label,
                       (ok, n, gap >= want), (18, 18, True)))

    # A legal position holds two kings at the least, so anything under that is
    # not a board and the pick would be made from an artefact.
    r.append(check("  but an empty board is refused rather than guessed at",
                   B.select(rends[0][3].render(chess.Board(None)), bank), (None, 0.0)))

    # -- a screen at the wrong brightness ----------------------------------
    # Where this gives out, and it gives out the same way pieces.py does. The
    # cutoffs are absolute, so turning the screen down takes the bright layer
    # away, and on an endgame there was little else to go on. What holds is
    # that the confidence goes with it: no wrong pick anywhere in the corpus
    # came within a quarter of the margin a clean board produces.
    ok = wrong = refused = 0
    worst_wrong = 0.0
    for name, src, _, label, sparse, img in corpus(rends):
        if "560" not in label or "flipped" in label:
            continue
        for variant in distortions(img):
            got, conf = B.select(variant, bank)
            if got is None:
                refused += 1
            elif got == name:
                ok += 1
            else:
                wrong += 1
                worst_wrong = max(worst_wrong, conf)
    print("\n      wrong brightness or blur: %d right, %d wrong, %d refused"
          % (ok, wrong, refused))
    r.append(check("distortion costs at most 12 wrong picks of 210",
                   (ok >= 177, wrong <= 12, ok + wrong + refused), (True, True, 210)))
    print("      the most confident wrong pick in the whole corpus: %.3f" % worst_wrong)
    r.append(check("  and every one of them comes in under a 0.05 margin",
                   worst_wrong < 0.05, True))

    # -- picking right is worth doing --------------------------------------
    # Top-1 before any confidence gate, which is the honest number issue #20
    # settled on. The gated number is worse still: the bundled sheet named 1 of
    # 32 pieces on 6.png.
    print("\n      top-1 piece identification, by which set is used:")
    scored = {}
    for name, src, _, rend in rends:
        position = chess.Board() if rend.can_render(chess.Board()) else POSITIONS[2][1]
        img = rend.render(position)
        for pieces in bank:
            ok = n = 0
            for row, col, square in B.squares(img):
                want = position.piece_at(chess.square(col, 7 - row))
                if want is None:
                    continue
                layers = B._layers(square)
                best = max((B._similarity(layers, t), symbol)
                           for symbol, t in pieces.templates.items())
                n += 1
                ok += best[1] == want.symbol()
            scored[(src, pieces.name)] = ok / n
            print("        %-7s board, %-9s templates: %d of %d" %
                  (src, pieces.name, ok, n))
    r.append(check("  the set the bank picks reads every piece on its own board",
                   [v for k, v in scored.items()
                    if k in (("1.png", "chesscom"), ("5.png", "chesscom"),
                             ("6.png", "flat"))], [1.0, 1.0, 1.0]))
    r.append(check("  and the other set misreads a fifth to a third of them",
                   max(v for k, v in scored.items()
                       if k in (("1.png", "flat"), ("5.png", "flat"),
                                ("6.png", "chesscom"))) < 0.82, True))

    # -- enrolling a set of your own ---------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        img = Image.open(shot("6")).convert("RGB")
        sheet, missing = B.cut_sheet(img, rect_of("6"), chess.Board())
        r.append(check("a screenshot of an opening position enrolls whole",
                       (missing, sheet.size), ([], (B.SLOT_PX * 12, B.SLOT_PX))))
        mine = os.path.join(tmp, "mine.png")
        sheet.save(mine)
        r.append(check("  and a smaller board is enough to enroll from",
                       B.cut_sheet(Image.open(shot("6")).convert("RGB").resize((355, 352)),
                                   (1, 1, 350), chess.Board())[1], []))

        # An endgame cannot enroll a set: the sheet has twelve slots and a
        # position that never showed a black bishop cannot fill one.
        r.append(check("  but a position missing pieces is refused, not padded",
                       sorted(B.cut_sheet(img, rect_of("6"), chess.Board(FEN5))[1]),
                       sorted("nqrb")))

        # crop() pads out of bounds with black and black counts as a piece
        # pixel, so a short sheet would load as solid masks matching anything.
        short = os.path.join(tmp, "short.png")
        Image.open(mine).crop((0, 0, 5 * B.SLOT_PX, B.SLOT_PX)).save(short)
        failed = False
        try:
            B.read_sheet(short)
        except ValueError:
            failed = True
        r.append(check("  a sheet with pieces missing will not load", failed, True))

        # One unreadable file a user dropped in should not take the bank down.
        with open(os.path.join(tmp, "junk.png"), "w") as f:
            f.write("not a png")
        shutil.copy(mine, os.path.join(tmp, "borrowed.png"))
        loaded = B.load_bank(tmp)
        r.append(check("  and a bad file is skipped rather than taking the bank down",
                       sorted(s.name for s in loaded), ["borrowed", "mine"]))

        # The point of enrolling: a set the bank did not have wins on its own
        # boards once it does.
        rend = rends[2][3]
        one = [s for s in loaded if s.name == "mine"]
        r.append(check("  and the set just enrolled fits its own boards best",
                       [B.rank(rend.render(p), one + [bank[0]])[0][0]
                        for _, p in POSITIONS], ["mine"] * len(POSITIONS)))

    # -- speed --------------------------------------------------------------
    # Runs once when a board is first seen, not per frame, so the ceiling is
    # loose on purpose. Best of ten rather than a mean, for the reason
    # piecetest.py gives.
    img = rends[0][3].render(chess.Board())
    B.select(img, bank)
    runs = []
    for _ in range(10):
        t0 = time.perf_counter()
        B.select(img, bank)
        runs.append(time.perf_counter() - t0)
    per = min(runs) * 1000
    print("\n      select: %.0f ms over an 824px board against 2 sets, best of 10" % per)
    r.append(check("  choosing a set stays under 60 ms", per < 60, True))

    print("\n%d/%d passed" % (sum(bool(x) for x in r), len(r)))
    return 0 if all(r) else 1


if __name__ == "__main__":
    sys.exit(main())
