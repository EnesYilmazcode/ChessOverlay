"""Headless checks for the piece set bank.

The question this answers is the one Tier 5 of issue #20 exists for: given a
board drawn in some piece set, does the bank pick that set. Everything else
here is in service of that number.

The corpus is generated rather than committed. Two piece sets are the only ones
this project has legitimate pixels for, both already in testdata/, and every
board below is rendered out of them by fakeboard.py at test time. That is a few
hundred boards without a single PNG in the repository, and it means the sizes
and positions can be changed by editing a tuple.

What the corpus is not is 252 independent trials, and the counts below are
split so that is visible rather than averaged away.

- The chess.com sheet was cut from 1.png and the flat sheet from 6.png, so 192
  of the 252 are a sheet being read back to itself.
- The other 60 come from 5.png, a different screenshot of the chess.com set at
  a different window size over a wallpaper. Independent of the sheet, but they
  are 5 positions by 2 orientations by 6 sizes off 8 sprites in one screenshot,
  and a horizontal flip and a LANCZOS downscale are deterministic transforms,
  not new evidence.
- Every one of the 60 asks only whether chesscom beats flat. There is nowhere
  in this corpus that the flat sheet has to win on pixels it did not come from,
  because there is no second screenshot of that set to render from.

Two sets is what the project has its own pixels for and no piece art is
bundled from anywhere else, so this is the ceiling on how honest the number can
be until someone enrolls a set of their own.

Run:  python banktest.py    (about 45 seconds, most of it the 2520 board sweep
                             MIN_CONFIDENCE is measured from)
"""

import os
import shutil
import sys
import tempfile
import time

import chess
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

import piecebank as B
import pieces as P
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

# chess.com's last-move highlight, off a real screenshot. Only used to paint one.
HIGHLIGHT = (247, 247, 105)


def check(name, got, want):
    ok = got == want
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        print("        got  ", got)
        print("        want ", want)
    return ok


def rect_of(name):
    return W.find_board(Image.open(shot(name)).convert("RGB"))


def board_colours(name):
    """The light and dark square colours of a reference screenshot, counted off
    the board itself rather than asked of the renderer. Half the squares are one
    colour and half the other, and no piece covers a whole square, so the two
    run away with the count whatever position is on the board."""
    name = name[:-4] if name.endswith(".png") else name
    x0, y0, size = rect_of(name)
    board = Image.open(shot(name)).convert("RGB").crop((x0, y0, x0 + size, y0 + size))
    counts = sorted(board.getcolors(size * size), reverse=True)
    first, second = counts[0][1], counts[1][1]
    # Brighter one first, to match light then dark.
    return (first, second) if sum(first) > sum(second) else (second, first)


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


def rescaled(rend, board, scale, size=None):
    """The same piece art drawn smaller inside the square, which is a set the
    bank does not hold and cannot have been enrolled from. Not new artwork, a
    transform of art already here, which is the only third set this repository
    can honestly produce."""
    out = Image.new("RGB", (rend.size, rend.size))
    step = rend.step
    small = max(8, int(step * scale))
    off = (step - small) // 2
    for row, rank in enumerate(range(7, -1, -1)):
        for col, file in enumerate(range(8)):
            pos = (col * step, row * step)
            out.paste(rend.sprites["light" if (row + col) % 2 == 0 else "dark"], pos)
            piece = board.piece_at(chess.square(file, rank))
            if piece:
                key = piece.symbol()
                out.paste(rend.sprites[key].resize((small, small), Image.LANCZOS),
                          (pos[0] + off, pos[1] + off),
                          rend.masks[key].resize((small, small), Image.LANCZOS))
    return out.resize((size, size), Image.LANCZOS) if size else out


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
    # 6.png is the whole second piece set, so say so rather than dying in
    # find_board on a folder that does not have it. CHESSWATCH_TESTDATA points
    # at your own board theme and predates that file.
    for name in ("1", "5", "6"):
        if not os.path.exists(shot(name)):
            print("FAIL  %s.png is missing from %s" % (name, os.path.dirname(shot(name))))
            return 1

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

    # -- the renderer draws board colours, not whatever was in the square ---
    # An empty square is drawn up to 64 times a board, so anything cut along
    # with it is repeated 64 times. The sprites used to come from a5 and a6,
    # which on a board with in-square coordinates are the two squares carrying
    # "5" and "6", and every rendered board came out stamped with them. 1.png
    # has no in-square labels and hid it for as long as it was the only
    # reference; 6.png has them.
    #
    # Checked against the two colours the reference screenshot is actually made
    # of, counted over the whole board and not through the renderer. An earlier
    # version of this compared the rendered square against rend.light, which is
    # the value render() painted it with, so it held whatever the renderer had
    # measured, magenta included.
    stamped = []
    for name, src, _, rend in rends:
        want = board_colours(src)
        if (rend.light, rend.dark) != want:
            stamped.append((src, "measured", (rend.light, rend.dark), want))
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
                if len(colours) != 1 or colours[0][1] not in want:
                    stamped.append((src, row, col, len(colours)))
    r.append(check("a rendered empty square is the reference's own square colour",
                   stamped, []))

    # The case the interior-squares rule is not enough for on its own. A
    # coordinate label is a minority of one square and loses to the mode; a
    # last-move highlight is the whole square and wins it. Painted onto b5 and
    # b6, which is where scanning for the first empty interior square lands on
    # an opening position, it used to become the light and dark colour of every
    # rendered board.
    with tempfile.TemporaryDirectory() as tmp:
        marked = Image.open(shot("1")).convert("RGB")
        x0, y0, size = rect_of("1")
        step = size / 8.0
        draw = ImageDraw.Draw(marked)
        for row, col in ((3, 1), (2, 1)):
            draw.rectangle([x0 + col * step, y0 + row * step,
                            x0 + (col + 1) * step - 1, y0 + (row + 1) * step - 1],
                           fill=HIGHLIGHT)
        path = os.path.join(tmp, "highlighted.png")
        marked.save(path)
        highlighted = Renderer(path, (x0, y0, size), chess.Board(FEN1))
        r.append(check("  and a last-move highlight on b5 and b6 does not become it",
                       (highlighted.light, highlighted.dark), board_colours("1")))

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

    # -- a screen at the wrong brightness, and what MIN_CONFIDENCE is for ---
    # Where this gives out, and it gives out the same way pieces.py does. The
    # cutoffs are absolute, so turning the screen down takes the bright layer
    # away, and on an endgame there was little else to go on.
    #
    # This is the measurement MIN_CONFIDENCE comes from, so it is the whole
    # corpus at every size and both orientations rather than a slice of it.
    # Scored before the gate, because the question is what the gate has to
    # catch. It is most of this file's running time and it is worth it: the
    # alternative is a threshold nobody measured.
    ungated = wrong = refused_early = 0
    wrong_margins = []
    for name, src, _, label, sparse, img in corpus(rends):
        for variant in distortions(img):
            ranked = B.rank(variant, bank)
            if not ranked:
                refused_early += 1
                continue
            margin = ranked[0][1] - ranked[1][1]
            if ranked[0][0] == name:
                ungated += 1
            else:
                wrong += 1
                wrong_margins.append((margin, label))
    trials = ungated + wrong + refused_early
    print("\n      wrong brightness or blur, before the gate: %d right, %d wrong,"
          " %d with too few pieces, of %d" % (ungated, wrong, refused_early, trials))
    r.append(check("distortion costs 154 wrong picks of 2520",
                   (ungated, wrong, refused_early), (2028, 154, 338)))

    wrong_margins.sort(reverse=True)
    print("      the most confident wrong pick anywhere: %.4f (%s)"
          % (wrong_margins[0][0], wrong_margins[0][1]))
    print("      the least confident right pick on a clean board: %.4f"
          % confidence[0][0])
    r.append(check("  no wrong pick in 2772 boards reaches MIN_CONFIDENCE",
                   (round(wrong_margins[0][0], 4),
                    sum(1 for m, _ in wrong_margins if m >= B.MIN_CONFIDENCE)),
                   (0.0444, 0)))
    r.append(check("  and no right pick on a clean board falls below it",
                   sum(1 for m in confidence if m[0] < B.MIN_CONFIDENCE), 0))

    # The band is real but it is narrow, 0.0444 to 0.1212, so the gate is set
    # once from a measurement rather than nudged.
    r.append(check("  which leaves a band of 2.7, not an order of magnitude",
                   round(confidence[0][0] / wrong_margins[0][0], 1), 2.7))

    # And the gate is what select() actually does, not advice in a docstring.
    # A wrong sheet is not a "?" downstream, it is confidently wrong pieces, so
    # the refusal has to happen where the answer is handed out.
    duped = next(v for v in distortions(rends[2][3].render(POSITIONS[4][1], size=200))
                 if B.rank(v, bank)[0][0] != "flat")
    r.append(check("  select refuses a board that would have picked the wrong set",
                   (B.rank(duped, bank)[0][0], B.select(duped, bank)[0],
                    B.sheet_for(B.select(duped, bank)[0], bank)),
                   ("chesscom", None, None)))
    r.append(check("  and a bank of one set is a refusal, not a free win",
                   B.select(rends[0][3].render(chess.Board()), bank[:1]), (None, 0.0)))

    # -- what the gate does not do -----------------------------------------
    # It is a margin between two sets in the bank. It says nothing about
    # whether the set on screen is one of them, and the two claims are not the
    # same claim. Drawn at 0.80 scale the flat set is a set nobody enrolled,
    # and the bank names flat for all 48 boards with a mean margin of 0.163,
    # clear of both MIN_CONFIDENCE and the 0.121 worst case on a real board.
    # Pinned here so nobody reads the gate as proof of membership. The same
    # transform on chesscom clears the gate zero times, so how far an absent
    # set gets depends entirely on which bank entry it happens to resemble.
    print()
    absent = {}
    for _, src, _, rend in (rends[2], rends[0]):
        picks = {}
        margins = []
        for label, position in POSITIONS:
            if not rend.can_render(position):
                continue
            for size in SIZES:
                ranked = B.rank(rescaled(rend, position, 0.80, size), bank)
                picks[ranked[0][0]] = picks.get(ranked[0][0], 0) + 1
                margins.append(ranked[0][1] - ranked[1][1])
        over = sum(1 for m in margins if m >= B.MIN_CONFIDENCE)
        absent[src] = (picks, over, len(margins))
        print("      %s redrawn at 0.80 scale, a set nobody enrolled: %s,"
              " mean margin %.3f, %d of %d clear the gate"
              % (src, picks, sum(margins) / len(margins), over, len(margins)))
    r.append(check("an absent set that resembles a bank entry clears the gate every time",
                   absent["6.png"], ({"flat": 48}, 48, 48)))
    r.append(check("  and one that resembles neither clears it none of 48",
                   absent["1.png"][1:], (0, 48)))

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
            scored[(src, pieces.name)] = (ok, n)
            print("        %-7s board, %-9s templates: %d of %d" %
                  (src, pieces.name, ok, n))
    r.append(check("  the set the bank picks reads every piece on its own board",
                   [scored[k] for k in (("1.png", "chesscom"), ("5.png", "chesscom"),
                                        ("6.png", "flat"))],
                   [(32, 32), (9, 9), (32, 32)]))
    r.append(check("  and the other set misreads 6 of 32, 3 of 9 and 10 of 32",
                   [scored[k] for k in (("6.png", "chesscom"), ("5.png", "flat"),
                                        ("1.png", "flat"))],
                   [(26, 32), (6, 9), (22, 32)]))

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

        # And a sheet at some other slot size is refused too. It would load
        # here, since this used to take the slot size off the sheet's height,
        # and pieces.py would then read it off the wrong pixels without a word,
        # because pieces.py reads a fixed 96. Both take the size from one
        # constant now, and a sheet that disagrees stops here.
        tall = os.path.join(tmp, "tall.png")
        Image.open(mine).resize((B.SLOT_PX * 12 * 2, B.SLOT_PX * 2)).save(tall)

        refused = []
        for path in (short, tall):
            try:
                B.read_sheet(path)
            except ValueError:
                refused.append(os.path.basename(path))
        r.append(check("  a sheet that is short or the wrong slot size will not load",
                       refused, ["short.png", "tall.png"]))
        r.append(check("  and both readers take the slot size from one constant",
                       (B.SLOT_PX, B.ORDER), (P.TEMPLATE_PX, P.ORDER)))

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
