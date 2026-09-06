"""Headless checks for the piece reader specifically.

selftest.py checks that the two reference screenshots read correctly. This
checks the harder things: that they keep reading correctly when the capture is
imperfect, that a piece set the templates were not drawn from is read rather
than refused wholesale, and that the reader says "?" instead of naming a piece
when it is not sure. Every floor below is a number that was actually measured,
not a target, so a drop here is a real regression and not a moved goalpost. The
one deliberate slack is the two speed ceilings at the end, 30 and 60 ms against
readings of 20 and 38, so that a slower machine does not read as a slowdown in
this code.

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
# and so must not disturb anything. 6.png is the starting position on the same
# chess.com board colours drawn with a different, flat piece set: the case the
# reader used to fail outright.
TRUTH = {
    "1": ["rnbqkbnr", "pp.p.ppp", "....p...", "..p.....",
          "...PP...", "........", "PPP..PPP", "RNBQKBNR"],
    "5": ["R.....Q.", ".......R", ".p......", ".N.B....",
          "...P...k", "........", ".PPK.P.P", "........"],
    "6": ["rnbqkbnr", "pppppppp", "........", "........",
          "........", "........", "PPPPPPPP", "RNBQKBNR"],
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


def named(reader, cases):
    """The same tally counting only the 32 squares that hold a piece."""
    correct = wrong = unknown = 0
    for name, board_img in cases:
        rows, _ = reader.classify(board_img)
        for r in range(8):
            for c in range(8):
                want = TRUTH[name][r][c]
                if want == ".":
                    continue
                if rows[r][c] == "?":
                    unknown += 1
                elif rows[r][c] == want:
                    correct += 1
                else:
                    wrong += 1
    return correct, wrong, unknown


def top_pick(reader, cases):
    """How often the best scoring piece type is the right one, before the
    confidence gate refuses anything. This is the honest measure of whether the
    reader can tell the pieces apart at all; what it names is that filtered."""
    hit = total = 0
    for name, board_img in cases:
        levels = P._levels(board_img)
        for row, col, sq in P.squares(board_img):
            want = TRUTH[name][row][col]
            if want == ".":
                continue
            total += 1
            feat = P._features(sq, levels)
            if feat and P.ranking(feat, reader.templates)[0][1] == want:
                hit += 1
    return hit, total


def refused_by_colour(reader, cases):
    """(true piece, piece the shape picked) for every square whose shape match
    cleared the floor and the margin and was then refused on colour.

    The gate is spelled out here rather than borrowed from _judge, because a
    row that asked _judge about both halves at once could not tell which half
    answered.
    """
    out = []
    for name, img in cases:
        levels = P._levels(img)
        feats = P._board_features(img, levels)
        floor = reader._floor(feats, levels, img.size[0] / 8.0)
        for (row, col, _), feat in zip(P.squares(img), feats):
            if feat is None or feat.coverage < P.MIN_COVERAGE:
                continue
            ranked = P.ranking(feat, reader.templates)
            score, best = ranked[0]
            runner_up = next((s for s, sym in ranked[1:]
                              if sym.lower() != best.lower()), 0.0)
            if score < floor or score - runner_up < P.MIN_MARGIN:
                continue
            if P._judge(feat, reader.templates, floor)[0] is None:
                out.append((TRUTH[name][row][col], best))
    return out


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


def smeared(boards):
    """The same boards captured badly enough that a white piece stops reaching
    either cutoff: a small window and display scaling together.

    This is the case pieces.py's damage handling exists for. Without it the
    reader answers "." at a score of 1.0 on 60 of the 154 pieces these six
    captures hold, before a single template is scored, and every one of them is
    white.
    """
    out = []
    for name, b in boards:
        for size, radius in ((280, 1.5), (400, 2.0)):
            out.append((name, b.resize((size, size), Image.LANCZOS)
                        .filter(ImageFilter.GaussianBlur(radius))))
    return out


def vanished(reader, cases):
    """How many squares that hold a piece the reader calls empty.

    Counted apart from `wrong` because it is a different failure with a
    different cause: not the shape matcher choosing badly but the two ink
    layers going quiet and _judge answering before it ever runs.
    """
    out = 0
    for name, board_img in cases:
        rows, _ = reader.classify(board_img)
        for r in range(8):
            for c in range(8):
                if TRUTH[name][r][c] != "." and rows[r][c] == ".":
                    out += 1
    return out


def distorted(boards):
    """A screen that is not at the reference brightness, and the blur display
    scaling adds. This used to be the genuinely hard one, because the bright and
    dark cutoffs were absolute and a brightness shift moved what counted as a
    piece pixel at all. The cutoffs are measured off the board now, so most of
    what this does is move them with it."""
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
    cache = tempfile.TemporaryDirectory()
    reader = P.PieceReader(cache_dir=cache.name)
    r.append(check("templates load from the bundled sheet",
                   (reader.ready, reader.source), (True, "bundled")))

    boards = []
    for name in ("1", "5", "6"):
        b = board_of(name)
        if b is None:
            print("FAIL  cannot find the board in %s.png" % name)
            return 1
        boards.append((name, b))
    six = [boards.pop()]

    # -- accuracy floors ---------------------------------------------------
    r.append(check("both reference boards read perfectly",
                   tally(reader, boards), (128, 0, 0)))

    got = tally(reader, shrunk(boards))
    r.append(check("nothing wrong down to a 200px window", got[1], 0))
    # 485, against the 487 before the reader started doubting a soft capture
    # and the 495 the solid mask named. Both of the two are at 400px, which is
    # the one size in this list that a 703px screenshot lands on measurably
    # soft, 0.665 against 0.42 at full size, so the margin a call has to clear
    # rises from 0.05 to 0.12 and two calls no longer clear it. That is the
    # priced half of pieces.py's CAREFUL_AT and it is what buys the smeared
    # rows further down.
    r.append(check("  and 485 of 512 squares still named", got[0] >= 485, True))

    got = tally(reader, offset(boards))
    print("      misaligned crops: %d correct, %d wrong, %d unclear" % got)
    r.append(check("a misaligned crop yields nothing wrong at all", got[1], 0))
    # 897, one fewer than before the same margin, and on the same crop of the
    # same board: a crop pulled two pixels out is very slightly soft as well.
    r.append(check("  and still names 897 of 1024 squares", got[0] >= 897, True))

    got = tally(reader, distorted(boards))
    print("      wrong brightness or blur: %d correct, %d wrong, %d unclear" % got)
    # 55, against 91 before any of this and 75 before the cutoffs were anchored
    # on the board's own ink. This row is where anchoring pays: brightness and
    # contrast are affine on pixel values, so measuring both ends off the board
    # makes the mask come out the same whatever the knobs are set to.
    #
    # All 55 are on the brightness and contrast rows, 28 and 27, and both are
    # unchanged by the damage handling: those are white pieces whose fill has
    # been clipped into the square colour, so the evidence for white is gone
    # rather than smeared, and a wider margin cannot put it back. The blur rows
    # are 0 wrong and were 0 before, on this piece set.
    r.append(check("distortion costs at most 55 wrong pieces of 1280",
                   got[1] <= 55, True))
    # 1133, against the 1121 the reader named before it sharpened anything.
    # The whole of the gain is on the blur rows, 320 against 308; the
    # brightness and contrast rows come back square for square the same.
    r.append(check("  and still names 1133 of 1280 squares", got[0] >= 1133, True))

    # -- the margin is what does that -------------------------------------
    # On a clean board the closest correct call clears the runner up by 0.192,
    # so the margin has to stay well under that or good boards start coming
    # back unclear. It was 0.130 before the mask was split, which is the point:
    # the split bought cross-set headroom without spending same-set headroom.
    tight = 1.0
    for name, b in boards:
        levels = P._levels(b)
        for row, col, sq in P.squares(b):
            if TRUTH[name][row][col] == ".":
                continue
            ranked = P.ranking(P._features(sq, levels), reader.templates)
            best = ranked[0][1]
            runner_up = next((s for s, sym in ranked[1:]
                              if sym.lower() != best.lower()), 0.0)
            tight = min(tight, ranked[0][0] - runner_up)
    print("      closest correct call on a clean board: %.3f" % tight)
    r.append(check("the margin leaves clean boards room to spare",
                   P.MIN_MARGIN < tight / 2, True))

    # -- a piece set the templates were never drawn from ------------------
    # This is what the split mask is for. Scored as one merged silhouette every
    # piece is the same blob and the right answer is top of the ranking 5 times
    # in 32; scored as a bright layer and a dark layer it is top 23 times. The
    # confidence gate then keeps 8 of those 23, because a foreign set scores
    # low enough that MIN_OVERLAP and the margin throw most of it away, and
    # that is the correct trade: not one of the 32 comes back wrong.
    got = top_pick(reader, six)
    print("      bundled templates on 6.png: right piece top of the ranking "
          "%d times in %d" % got)
    r.append(check("the bundled set picks the right piece on 6.png 25 times in 32",
                   got[0] >= 25, True))
    got = named(reader, six)
    print("      and names %d, refuses %d, gets %d wrong" % (got[0], got[2], got[1]))
    r.append(check("  names 8 of them and none of them wrongly",
                   (got[0] >= 8, got[1]), (True, 0)))

    # -- learning the set in front of it ----------------------------------
    # The real route for a foreign set is to learn it, and this is the case the
    # per-square-colour templates fixed: learning from 6.png and reading 6.png
    # straight back used to leave 18 of its 32 pieces unread, because every
    # template came off whichever square colour that piece started on.
    start = chess.Board()
    own = P.PieceReader(cache_dir=cache.name)
    r.append(check("a foreign set is learnable in one frame",
                   own.learn(six[0][1], start), True))
    got = named(own, six)
    print("      learned from 6.png, reading 6.png: %d named, %d refused, "
          "%d wrong" % (got[0], got[2], got[1]))
    r.append(check("  and then reads all 32 of its own pieces, none wrong",
                   (got[0], got[1]), (32, 0)))

    # -- colour, decided against the templates and not against itself -----
    # The last five of those 32. Colour used to be a bright-versus-dark pixel
    # count on the square alone, which reads the piece set rather than the
    # piece: 6.png draws a white rook as a white fill inside a thick black
    # outline, so five of its eight white back-rank pieces hold more dark
    # pixels than bright and were refused against their own correct template,
    # every one of them an outright match clear of the next piece type by more
    # than 0.66. check() wants all 64 squares, so a board taught by hand stayed
    # "board unclear" on the very capture it was taught from, forever.
    levels = P._levels(six[0][1])
    outlined = []
    for row, col, sq in P.squares(six[0][1]):
        want = TRUTH["6"][row][col]
        if not want.isupper():
            continue
        feat = P._features(sq, levels)
        if feat.bright <= feat.dark:
            outlined.append((want, P._judge(feat, own.templates)[0]))
    r.append(check("a white piece drawn mostly in outline still reads white",
                   outlined, [("R", "R"), ("B", "B"), ("Q", "Q"),
                              ("B", "B"), ("R", "R")]))

    # It is still a test, and this is what it catches. Two or three pixels of
    # crop error on 6.png pulls a neighbouring square's ink in, and three of
    # its white queens then match the BLACK queen template top of the ranking
    # and clear of every other piece type. The shape gate has nothing to say
    # about those; the ink share does, because the square holds 0.37 of its ink
    # bright where the black queen template holds 0.16 and the white one 0.37.
    r.append(check("a white queen matched to the black queen is refused",
                   sorted(refused_by_colour(own, offset(six))),
                   [("Q", "q")] * 3))
    got = named(own, offset(six))
    print("      6.png templates on misaligned crops of 6.png: %d named, "
          "%d refused, %d wrong" % (got[0], got[2], got[1]))
    r.append(check("  so a misaligned crop of 6.png names 211, none wrong",
                   (got[0], got[1]), (211, 0)))

    # And this is what it costs. Blur and contrast are the cases that erase a
    # white piece's fill outright: at 2.5px the bright layer of 6.png's back
    # rank empties completely, the square's ink then genuinely reads black, and
    # 19 correct calls become "?" rather than staying named. That is the trade
    # this file always makes, a refusal against a wrong piece, and it is priced
    # here so it cannot grow quietly.
    priced = refused_by_colour(own, distorted(six))
    print("      and refuses %d correct calls on blurred or shifted 6.png"
          % len(priced))
    r.append(check("  costing 19 correct calls where the ink is smeared away",
                   (len(priced), [x for x in priced if x[0] != x[1]]),
                   (19, [])))

    # None of it touches the set that never had the problem. chess.com draws a
    # white piece as a pale fill with a hairline edge, so the old count and the
    # new comparison agree on all 45 pieces of both reference boards however
    # they are shrunk, misaligned or distorted, which is why this survived.
    r.append(check("  and fires on no square of either chess.com fixture",
                   refused_by_colour(reader, boards + shrunk(boards)
                                     + offset(boards) + distorted(boards)), []))

    from fakeboard import Renderer
    render = Renderer(shot("1"), W.find_board(Image.open(shot("1")).convert("RGB")))
    other = P.PieceReader(cache_dir=cache.name)
    other.learn(render.render(start), start)

    # Templates from one set turned on the other, both ways round. Neither is a
    # thing the app does on purpose, since learn() runs on whatever is actually
    # on screen, but it is the honest measure of how far a learned set travels.
    print("      chess.com templates on 6.png:  %d named, %d refused, %d wrong"
          % tuple(named(other, six)[i] for i in (0, 2, 1)))
    print("      6.png templates on 1 and 5:    %d named, %d refused, %d wrong"
          % tuple(named(own, boards)[i] for i in (0, 2, 1)))
    r.append(check("chess.com templates refuse 6.png rather than guess it",
                   named(other, six)[1], 0))
    # The direction that needed the trust gate. 6.png draws its pawn at the box
    # size chess.com draws a rook at, so once registration has normalised the
    # box away its pawn template outscores its own rook template on a chess.com
    # rook, and named four rooks and three knights as pawns by up to 0.17 of
    # margin. Charging for the stretch cannot fix it: those wrong calls fit the
    # box to within 0.81 to 0.99 while the true pieces fit to 0.48 to 0.55, so
    # the penalty pushes the wrong way. What does fix it is noticing that the
    # templates are not this set's at all.
    r.append(check("  and 6.png templates name 13 of them and none wrongly",
                   named(own, boards)[:2], (13, 0)))

    # -- the trust gate ----------------------------------------------------
    # A set carries the signature of the board it was learned from: how much of
    # a piece is outline rather than fill, once for the light pieces and once
    # for the dark. When the board being read disagrees, the floor a call has
    # to clear rises from MIN_OVERLAP to MISTRUST_OVERLAP, which is the whole
    # of what removed those seven.
    def sig_of(board_img):
        levels = P._levels(board_img)
        return P._signature(P._board_features(board_img, levels), levels,
                            board_img.size[0] / 8.0)

    # Outline share for the light pieces and for the dark ones, then how far
    # the ink reaches either side of the board colour. The flat set is drawn
    # with half again the outline on its light pieces and puts a bright rim on
    # its dark ones where chess.com puts none at all.
    r.append(check("a learned set knows which board it came from",
                   (round(own.signature[0], 2), round(own.signature[1], 2),
                    round(other.signature[0], 2), round(other.signature[1], 2)),
                   (0.35, 0.15, 0.23, 0.0)))
    r.append(check("  and trusts its own board and its own set's other capture",
                   [P._trusted(other.signature, sig_of(b)) for _, b in boards]
                   + [P._trusted(own.signature, sig_of(six[0][1]))],
                   [True, True, True]))
    r.append(check("  and distrusts the other set both ways round",
                   [P._trusted(own.signature, sig_of(b)) for _, b in boards]
                   + [P._trusted(other.signature, sig_of(six[0][1]))],
                   [False, False, False]))
    # The signature has to survive the screen being turned up and down, or the
    # gate is separating captures rather than sets. Before the cutoffs were
    # anchored on the board's own ink it did not: 5.png at 1.25 contrast sat
    # 0.171 from its OWN set's templates while 1.png at the same contrast sat
    # 0.149 from the FOREIGN set's, so the two crossed over and no threshold
    # could tell them apart. Anchored, the same two pairs read 0.127 and 0.378,
    # which is the whole point of that change and the thing to keep.
    def apart(a, b):
        parts = [abs(x - y) for x, y in zip(a[:4], b[:4])
                 if x is not None and y is not None]
        return round(max(parts), 3) if parts else None

    harsh1 = ImageEnhance.Contrast(boards[0][1]).enhance(1.25)
    harsh5 = ImageEnhance.Contrast(boards[1][1]).enhance(1.25)
    print("      at 1.25 contrast: own set %.3f away, foreign set %.3f away"
          % (apart(other.signature, sig_of(harsh5)),
             apart(own.signature, sig_of(harsh1))))
    r.append(check("  and a set stays nearer its own contrast-shifted capture",
                   (apart(other.signature, sig_of(harsh5)) <= 0.127,
                    apart(own.signature, sig_of(harsh1)) >= 0.378), (True, True)))

    # It must not fire on the same set merely captured badly. It does fire on
    # 11 of these 44, all of them blurred or shrunk far enough that the ink no
    # longer reaches, and that is the right answer rather than a false alarm: a
    # capture that has lost its outlines is one the templates do not describe
    # either. Nothing above lost a correct answer to it, which is the test that
    # matters and is the four accuracy rows at the top of this file.
    bad = shrunk(boards) + offset(boards) + distorted(boards)
    held = sum(1 for _, b in bad if P._trusted(reader.signature, sig_of(b)))
    print("      trust gate holds on %d of %d same-set captures" % (held, len(bad)))
    r.append(check("  and holds on 33 of the 44 bad captures of the right set",
                   held >= 33, True))

    # -- measuring the board instead of assuming it -----------------------
    # Turn the contrast on 1.png up to 1.25 and the board's two greys move from
    # 131 and 233 to 120 and 246, and its ink from 32 and 254 to 0 and 255. The
    # cutoffs used to be fixed at 70 and 244, so the black back rank came out
    # light enough that b8 matched the WHITE knight and only a bright-versus-
    # dark pixel count stopped it being named one. Measured off the board at
    # both ends, all four numbers move together and the mask comes out the
    # same: b8 is a black knight by 0.50 and the colour veto has nothing left
    # to catch, firing on zero squares of the distorted set against nine before.
    harsh = ImageEnhance.Contrast(boards[0][1]).enhance(1.25)
    levels = P._levels(harsh)
    b8 = next(sq for row, col, sq in P.squares(harsh) if (row, col) == (0, 1))
    ranked = P.ranking(P._features(b8, levels), reader.templates)
    r.append(check("contrast moves the measured board colours, not the pieces",
                   (levels, ranked[0][1]), ((120, 246, 0, 255), "n")))
    r.append(check("  so b8 at 1.25 contrast is simply read, not vetoed",
                   P._decide(b8, reader.templates, levels)[0], "n"))

    # The mask itself is what has to be invariant, not just the ranking. Both
    # ends of the threshold are measured off the board, and brightness and
    # contrast are affine on pixel values, so every one of those measurements
    # moves by the same factor and the thresholded mask lands on the same
    # cells. Anything left over is clipping at 0 and 255 and rounding.
    plain = next(sq for row, col, sq in P.squares(boards[0][1])
                 if (row, col) == (0, 1))
    flat = P._features(plain, P._levels(boards[0][1]))
    lifted = ImageEnhance.Brightness(boards[0][1]).enhance(1.08)
    b8up = next(sq for row, col, sq in P.squares(lifted) if (row, col) == (0, 1))
    same = P._features(b8up, P._levels(lifted))
    agree = ((flat.masks[0] & same.masks[0]).bit_count()
             + (flat.masks[1] & same.masks[1]).bit_count())
    union = ((flat.masks[0] | same.masks[0]).bit_count()
             + (flat.masks[1] | same.masks[1]).bit_count())
    print("      the same square at 1.00 and 1.08 brightness: masks agree %.3f"
          % (agree / union))
    r.append(check("  and the mask of a square barely moves when the screen does",
                   agree / union >= 0.97, True))

    # -- measuring the capture as well as the board ------------------------
    # A board carries its own ruler. Every square boundary is a step between
    # the two square colours, both already measured, and a Gaussian blurred
    # step of height h peaks at h / (sigma * root 2pi), so reading the seven
    # internal boundaries says what the capture did to the picture. It measures
    # the board and not the pieces, which is why it comes out the same on every
    # set, size, orientation and position.
    sharp_sigma, soft_sigma = [], []
    for _, b in boards:
        for size in (824, 560, 400, 280):
            small = b.resize((size, size), Image.LANCZOS)
            # -1.0 for an unmeasurable board, so that a broken estimator reads
            # as a failed row rather than as a stack trace.
            sharp_sigma.append(P.sigma_of(small) or -1.0)
            soft_sigma.append(
                P.sigma_of(small.filter(ImageFilter.GaussianBlur(1.1))) or -1.0)
    print("      measured blur: %.3f to %.3f sharp, %.3f to %.3f at 1.1px"
          % (min(sharp_sigma), max(sharp_sigma),
             min(soft_sigma), max(soft_sigma)))
    # 0.420 to 0.665 sharp against 1.031 to 1.197 blurred, over both boards at
    # four sizes each. The gate has to sit in that gap and not inside either
    # distribution, because sharpening a capture that is merely small pushes it
    # away from the templates. See pieces.SHARPEN_AT.
    r.append(check("a board measures the blur on its own capture",
                   (max(sharp_sigma) < P.SHARPEN_AT < min(soft_sigma),
                    round(min(soft_sigma), 1)), (True, 1.0)))
    # And a sharp capture is left completely alone: no convolution, no wider
    # margin, no colour refusal. Every number this adds is inert until the
    # picture is measurably soft.
    r.append(check("  and a sharp capture is not touched at all",
                   [(P._unblur(b, P.sigma_of(b)) is b,
                     P._soft_margin(P.sigma_of(b)) == P.MIN_MARGIN,
                     P._colour_blind(P._levels(b)))
                    for _, b in boards + six],
                   [(True, True, False)] * 3))

    # Past about 1.9 pixels the step stops fitting inside the narrow profile,
    # every row fails the height check and the narrow window has no answer at
    # all. Without the wide one behind it the reader would call those captures
    # sharp, leave them alone and go straight back to the bug below.
    hard = boards[0][1].filter(ImageFilter.GaussianBlur(2.5))
    grey = hard.convert("L")
    lo, hi = P._levels(hard)[:2]
    narrow = P._sigma_at(grey, grey.size[0] / 8.0, hi - lo, P.SIGMA_HALVES[0])
    broad = P.sigma_of(hard)
    r.append(check("past 1.9px only the wide profile has an answer",
                   (narrow, broad is not None and 2.6 < broad < 2.8),
                   (None, True)))

    # -- a white piece read as an empty square -----------------------------
    # The bug this whole section is for. A white piece is a light body inside a
    # hairline dark edge; blur puts the body under the bright cutoff and smears
    # the edge away, and _judge then answers "." at a score of 1.0 without
    # scoring a single template. It is the loudest possible failure, because
    # "." is not a refusal: check() believes it and records the position.
    bad = smeared(boards + six)
    got = tally(reader, bad)
    gone = vanished(reader, bad)
    print("      badly captured: %d correct, %d wrong, %d unclear, %d pieces "
          "read as empty" % (got + (gone,)))
    # 228 correct and none wrong, against 228 correct and 60 wrong before. The
    # 60 are every white piece on all six captures and no black one.
    r.append(check("a smeared capture reads no piece as an empty square",
                   (gone, got[1]), (0, 0)))
    r.append(check("  and gives up nothing to do it", got[0] >= 228, True))

    # The safety net under all of it, and the reason it holds whatever the
    # sharpening did: how far apart the brightest and darkest pixel in the
    # middle of a square are. Pinned as a gap rather than as a threshold, since
    # a threshold inside a distribution is not a test.
    empty_top, held_low = 0.0, 1.0
    for name, img in bad:
        fixed = P._unblur(img, P.sigma_of(img))
        levels = P._levels(fixed)
        span = max(1, levels[1] - levels[0])
        for row, col, sq in P.squares(fixed):
            wide, high = sq.size
            dx = int(wide * (1 - P.OCCUPIED_INSIDE) / 2)
            dy = int(high * (1 - P.OCCUPIED_INSIDE) / 2)
            low, top = sq.crop((dx, dy, wide - dx,
                                high - dy)).convert("L").getextrema()
            reach = (top - low) / span
            if TRUTH[name][row][col] == ".":
                empty_top = max(empty_top, reach)
            else:
                held_low = min(held_low, reach)
    print("      an empty square reaches %.4f, an occupied one %.4f"
          % (empty_top, held_low))
    r.append(check("  and the emptiness test sits in a gap, not a distribution",
                   (empty_top < P.OCCUPIED_F < held_low,
                    held_low > 10 * empty_top), (True, True)))

    # What the wide profile buys, priced. Three of these six captures measure
    # nothing at all through the narrow window alone, and the reader then reads
    # them as sharp and gets 27 pieces back as empty squares.
    halves = P.SIGMA_HALVES
    P.SIGMA_HALVES = halves[:1]
    try:
        narrow_only = vanished(P.PieceReader(cache_dir=cache.name), bad)
    finally:
        P.SIGMA_HALVES = halves
    r.append(check("  which the narrow profile alone cannot do", narrow_only, 27))

    # And the same rule reaches the one square piece_at is asked about, which
    # is the promoted pawn. Two white bishops on the 280px 6.png capture are
    # what the sharpening alone does not recover, and without the net there
    # piece_at answers "." on both of them, which _confirm_promotion reads as
    # "nothing appeared" and records a queen.
    r.append(check("  and piece_at will not call an occupied square empty",
                   [reader.piece_at(bad[4][1], 7, col) for col in (2, 5)],
                   [None, None]))

    # -- a capture that has lost a whole ink layer -------------------------
    # 1.25 contrast clips the light square to 246 and the white fill to 255, so
    # _tables sees no headroom for light ink and switches the bright layer off
    # for the whole board. Refusing colour there is right; refusing it on a
    # board that simply has no white pieces left is not, and the two look
    # identical to the layer test alone. The second half is that the ink is
    # pressed against the end of the range, which a clipped capture does and an
    # absent colour does not.
    black_only = render.render(chess.Board("rnbqkbnr/pppppppp/8/8/8/8/8/8 w - - 0 1"))
    r.append(check("a clipped capture is colour blind and a one-colour board is not",
                   [P._colour_blind(P._levels(img))
                    for img in (harsh, black_only, boards[0][1])],
                   [True, False, False]))

    # And what being colour blind then changes: the runner up is taken over all
    # twelve symbols rather than over piece types, because the thing that
    # normally decides colour is the layer that has gone. So a piece that does
    # not clearly beat its own opposite comes back "?" rather than coming back
    # the wrong colour.
    #
    # The templates are doctored to make that checkable. None of the three
    # screenshots here ever reaches the state on any capture, blurred, shrunk,
    # clipped or brightened, so the square that needs it does not exist in this
    # file; bench.py's `contrast` variant is what measures the rule end to end,
    # where it removes 157 wrong answers and costs no right ones. Giving "r"
    # the white rook's own template makes the twin score exactly what the
    # winner scores, which is the state the rule is about.
    twinned = dict(own.templates)
    twinned["r"] = own.templates["R"]
    rook = next(sq for row, col, sq in P.squares(six[0][1]) if (row, col) == (7, 0))
    feat = P._features(rook, P._levels(six[0][1]))
    # Compared without case, because the two now score identically and which
    # of them comes top is the sort order rather than the reader.
    r.append(check("  a piece that cannot beat its own opposite colour is refused",
                   (P._judge(feat, twinned, colour_blind=False)[0].lower(),
                    P._judge(feat, twinned, colour_blind=True)[0]), ("r", None)))

    # -- the arrow the program draws on the board it is reading ------------
    # overlay.py picks a colour that greys into the band read_occupancy
    # ignores, and that is the whole reason the recorder cannot corrupt a game
    # with its own coach arrow. The emptiness test above asks about grey levels
    # rather than about the two ink bands, so it is the one part of this reader
    # that can see the arrow. What has to hold is the direction of that: the
    # arrow may cost the reader an answer and may never change one.
    import bench as _bench
    withdrawn = added = altered = 0
    for name, img in bad:
        plain_rows, _ = reader.classify(img)
        for uci in ("e2e4", "g1f3"):
            drawn, _ = reader.classify(_bench._draw_arrow(img, uci))
            for row in range(8):
                for col in range(8):
                    was, now = plain_rows[row][col], drawn[row][col]
                    if was == now:
                        continue
                    if now == "?":
                        withdrawn += 1
                    elif was == "?":
                        added += 1
                    else:
                        altered += 1
    print("      with the coach arrow drawn: %d answers withdrawn, %d added, "
          "%d altered" % (withdrawn, added, altered))
    r.append(check("the coach arrow can cost an answer and cannot change one",
                   (added, altered), (0, 0)))

    # -- unclear rather than wrong ----------------------------------------
    # Two pixels of crop error pulls a black column in off the edge of the
    # picture, and black is a piece pixel. The whole h file goes unclear and
    # nothing else moves. What matters is the second line: no square anywhere
    # on that board is named as the wrong piece.
    name, b = boards[0]
    nudged = b.crop((2, 0, 2 + b.size[0], b.size[0]))
    rows, weakest = reader.classify(nudged)
    r.append(check("two pixels of crop error cost the h file and nothing else",
                   ([rows[row][7] for row in range(8)].count("?"), weakest),
                   (8, 0.0)))
    r.append(check("  and nothing it does name on that board is wrong",
                   [rows[row][col] for row in range(8) for col in range(8)
                    if rows[row][col] not in ("?", TRUTH[name][row][col])], []))

    # The "?" is the only thing that refuses it. A wrong letter is stopped
    # nowhere downstream: the same board with the h file guessed at is a
    # position the rules allow and would be recorded as one.
    guessed = [list(row) for row in TRUTH[name]]
    r.append(check("  and a \"?\" is refused where a guessed h file would not be",
                   (W.board_from_grid(rows), W.board_from_grid(guessed) is not None),
                   (None, True)))

    # -- what the reader refuses, a belief can sometimes put back ----------
    # Choosing between twelve pieces is a question a pointer or a hover popup
    # spoils: the overlap divides by a union the overlay moves, so all twelve
    # scores sink together and the winner stops beating the runner up. Asking
    # whether ONE named piece is present divides by that template instead,
    # which nothing on the square can move, so the candidates stay comparable
    # to each other however much is drawn on top. See _contains.
    import fakeboard as _F

    covered = []
    for name, b in boards:
        for row in range(0, 8, 2):
            for col in range(0, 8, 2):
                covered.append((name, _F.with_pointer(b, row, col)))
                covered.append((name, _F.with_pointer(b, row, col, size=1.8)))
        for row in range(8):
            for col in (1, 3, 5):
                for wide in (1, 2):
                    covered.append((name, _F.with_panel(b, row, col, wide)))
                    covered.append((name, _F.with_panel(b, row, col, wide,
                                                        dark=False)))
        # And whole squares painted over edge to edge, which is the case
        # nothing can see through and nothing may claim to.
        for row, col in ((0, 4), (3, 2), (6, 1), (4, 4)):
            covered.append((name, _F.cover(b, row, col)))

    def field(feat):
        return {sym: max(P._contains(feat, t.feat) for t in variants)
                for sym, variants in reader.templates.items()}

    refused = put_back = worst = offered = swallowed = 0
    nearest_lie, lowest_yes = 0.0, 1.0
    for name, img in covered:
        want = TRUTH[name]
        blind, _ = reader.classify(img)
        levels = P._levels(img)
        left = 0
        for row, col, sq in P.squares(img):
            if blind[row][col] != "?":
                continue
            refused += 1
            feat = P._features(sq, levels)
            if P._confirms(feat, reader.templates, want[row][col]):
                put_back += 1
            else:
                left += 1
            # A refusal costs a check pass, which runs again a second later; a
            # wrong letter goes into the game record and stays there. So every
            # square the reader gave up on is also offered every belief except
            # the true one, and none of those may come back confirmed.
            for symbol in P.ORDER + ".":
                if symbol == want[row][col]:
                    continue
                offered += 1
                if P._confirms(feat, reader.templates, symbol):
                    swallowed += 1
            # How close the nearest of them came, and how little containment
            # the margin was ever satisfied with, so both numbers below are
            # measured here rather than asserted from elsewhere.
            held = field(feat) if feat is not None else {}
            for symbol, got in held.items():
                rival = max(v for sym, v in held.items()
                            if sym.lower() != symbol.lower())
                if symbol != want[row][col]:
                    nearest_lie = max(nearest_lie, got - rival)
                elif got - rival >= P.CONFIRM_MARGIN:
                    lowest_yes = min(lowest_yes, got)
        worst = max(worst, left)
    print("      obstructed: %d captures, %d squares refused, %d put back, "
          "worst %d left" % (len(covered), refused, put_back, worst))
    r.append(check("a belief puts back 23 of the 349 squares an obstruction "
                   "costs", (refused, put_back), (349, 23)))
    r.append(check("  and no capture is left with more than 3 unreadable",
                   worst, 3))
    print("      %d wrong beliefs offered, the nearest short by %.4f"
          % (offered, P.CONFIRM_MARGIN - nearest_lie))
    r.append(check("a piece the square is not holding is never confirmed",
                   (offered, swallowed), (4188, 0)))
    # And where the margin's number comes from. The nearest miss is a black
    # bishop believed where a white one stands under a dark panel, so 0.15
    # clears the worst of them by 0.0195 and 0.12 does not.
    r.append(check("  the nearest of them reaches 0.1235 of the 0.15",
                   round(nearest_lie, 4), 0.1235))

    # The containment floor refused nothing on any of these: the lowest
    # containment the margin was satisfied with is 0.5354. Pinning that gap is
    # what stops the floor being raised into the answers, or dropped as
    # decorative, since on the margin alone a square holding almost nothing
    # confirms as long as nothing else is there either.
    r.append(check("the containment floor sits below every confirm it allows",
                   (round(lowest_yes, 4), P.CONFIRM_CONTAIN < lowest_yes),
                   (0.5354, True)))

    # Splitting the mask is what lets any of this see colour at all. A white
    # piece's ink is nearly all in the bright layer and a black piece's in the
    # dark one, and bright is never counted against dark, so the two do not
    # contain each other. Without that a pawn taken by the other side's pawn
    # would confirm as still standing, since the two are one silhouette.
    start_img = render.render(start)
    levels = P._levels(start_img)
    home = W.grid_of(start, False)
    itself = swapped = 0
    for row, col, sq in P.squares(start_img):
        if home[row][col] == ".":
            continue
        feat = P._features(sq, levels)
        itself += P._confirms(feat, reader.templates, home[row][col])
        swapped += P._confirms(feat, reader.templates,
                               home[row][col].swapcase())
    r.append(check("on a clean board 28 of the 32 pieces confirm themselves",
                   (itself, swapped), (28, 0)))

    # And that the reader is wired to ask at all. Three things at once: it does
    # put something back, what it puts back is the belief and nowhere the
    # scores had already answered, and a belief it cannot confirm leaves the
    # board exactly as it read it.
    name, b = boards[0]
    shown = _F.with_pointer(b, 0, 0, size=1.8)
    want = [list(row) for row in TRUTH[name]]
    blind, _ = reader.classify(shown)
    told, _ = reader.classify(shown, want)
    moved = [(row, col) for row in range(8) for col in range(8)
             if told[row][col] != blind[row][col]]
    r.append(check("classify puts the belief back, and only where it refused",
                   (len(moved), all(blind[row][col] == "?"
                                    and told[row][col] == want[row][col]
                                    for row, col in moved)),
                   (2, True)))
    r.append(check("  and a belief it cannot confirm changes nothing",
                   reader.classify(shown, [["k"] * 8 for _ in range(8)])[0],
                   blind))

    # -- relearning and falling back --------------------------------------
    r.append(check("learns from a starting position",
                   (reader.learn(render.render(start), start), reader.source),
                   (True, "learned from your screen")))
    r.append(check("  and remembers the size it learned at",
                   reader.learned_size, render.size))
    r.append(check("  templates learned at one size still read another",
                   tally(reader, boards), (128, 0, 0)))
    # One template per piece per square colour, which for a starting position
    # means two of everything except the kings and queens: those stand on one
    # colour each and there is nothing to learn about the other until they move.
    r.append(check("  and learned both square colours of every piece it could",
                   "".join(str(len(reader.templates[s])) for s in P.ORDER),
                   "112222112222"))

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
    tiny = P.PieceReader(cache_dir=cache.name)
    tiny.learn(render.render(start).resize((small, small), Image.LANCZOS), start)
    r.append(check("  but one learned under the floor is, once the board grows",
                   (tiny.stale(small * 2), tiny.stale(small), tiny.stale(small // 2)),
                   (True, False, False)))

    # -- learning part of a position --------------------------------------
    # learn() used to want all twelve piece types or it kept none of them, so a
    # game joined part way through never left the bundled sheet. It keeps what
    # it saw now. Returning False still means "not all twelve", because that is
    # what relearn() and its callers ask about.
    rooks = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    part = P.PieceReader(cache_dir=cache.name)
    sheet_kept = dict(part.templates)
    r.append(check("a position with four piece types learns those four",
                   (part.learn(render.render(rooks), rooks), part.source),
                   (False, "learned in part from your screen")))
    r.append(check("  replacing exactly K R k r and leaving the sheet alone",
                   sorted(s for s in P.ORDER
                          if part.templates[s] is not sheet_kept[s]),
                   ["K", "R", "k", "r"]))

    # relearn() is the strict one, and has to be: it fires on every resize, on
    # whatever position happens to be up, and a caller that traded twelve
    # fitted templates for four would never know.
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

    # -- folding in more frames -------------------------------------------
    # The tracker knows the position on every frame it is following, so every
    # one of those frames is labelled data going spare. observe() averages them
    # into the templates instead of leaving the one snapshot learn() took.
    folding = P.PieceReader(cache_dir=cache.name)
    folding.learn(six[0][1], start)
    blurred = six[0][1].filter(ImageFilter.GaussianBlur(1.0))
    r.append(check("a second sighting folds every piece in",
                   folding.observe(six[0][1], start), 32))
    r.append(check("  averaging rather than replacing, so counts climb",
                   min(t.n for v in folding.templates.values() for t in v) > 1,
                   True))
    r.append(check("  and a blurred frame on top costs it nothing",
                   (folding.observe(blurred, start), named(folding, six)[1]),
                   (32, 0)))
    # A frame the tracker has fallen a ply behind on carries a picture of one
    # position and a label for another. observe() cannot see the lag, but it
    # can see that the square it is being told about does not read as what it
    # is being told, and it refuses those rather than learning them.
    moved = chess.Board()
    moved.push_san("e4")
    r.append(check("  and a square whose label has moved on is refused",
                   folding.observe(six[0][1], moved) < 32, True))

    # -- the cache ---------------------------------------------------------
    # Learning needs a position we are certain about, which means catching a
    # game from move one. Cached, the second game on the same window and the
    # same piece set starts fitted whether it was caught from move one or not.
    only6 = tempfile.TemporaryDirectory()
    P.PieceReader(cache_dir=only6.name).learn(six[0][1], start)
    fresh = P.PieceReader(cache_dir=only6.name)
    r.append(check("the set learned from 6.png comes back from disk",
                   (fresh.restore(six[0][1]), fresh.source, fresh.learned_size),
                   (True, "learned in an earlier game", six[0][1].size[0])))
    r.append(check("  and reads exactly what the learned set read",
                   named(fresh, six), named(own, six)))
    # Keyed on the board width and a fingerprint of how the set is drawn, so a
    # set that was never learned here misses rather than loading templates that
    # would name the wrong pieces confidently. Both boards below are 824px and
    # 703px of the same chess.com green, so it is the piece set and the window
    # width doing the work and not the theme.
    r.append(check("  a piece set never learned here misses the cache",
                   P.PieceReader(cache_dir=only6.name).restore(boards[0][1]),
                   False))
    r.append(check("  as does the same set in a different sized window",
                   P.PieceReader(cache_dir=only6.name).restore(
                       six[0][1].resize((560, 560), Image.LANCZOS)), False))
    # The cache directory ignores itself, so a clone never shows it as
    # something to commit and .gitignore never has to know it exists.
    r.append(check("  and the cache directory ignores itself",
                   open(os.path.join(only6.name, ".gitignore")).read().strip(),
                   "*"))
    only6.cleanup()

    # A sheet with slots missing has to fail rather than load. crop() pads out
    # of bounds with black, and black counts as a piece pixel, so the seven
    # missing slots used to load as solid masks with a coverage of 1.0, which
    # matched anything: a five piece sheet read every white pawn on 1.png as a
    # rook without complaining once.
    with tempfile.TemporaryDirectory() as tmp:
        narrow = os.path.join(tmp, "short.png")
        Image.open(P.TEMPLATE_SHEET).crop(
            (0, 0, 5 * P.TEMPLATE_PX, P.TEMPLATE_PX)).save(narrow)
        short = P.PieceReader(narrow, cache_dir=cache.name)
        r.append(check("a sheet with pieces missing is refused, not padded",
                       (short.ready, short.source), (False, "none")))

    # -- speed -------------------------------------------------------------
    # The best of twenty warmed passes, not the mean of five cold ones. A mean
    # over a handful of runs tracks what else the machine is doing more than it
    # tracks this code: with six other processes competing it came out 46%
    # slower, which is enough to hide a real speedup or invent a regression.
    # The best case moves only when the work per board does.
    #
    # 17 ms against the 6 the solid mask took, and it is nearly all in reducing
    # the squares: measured separately, 13.8 ms goes on turning 64 squares into
    # features, 1.4 ms on scoring them and 0.5 ms on measuring the board's own
    # greys and ink. A square went from one threshold, one subsample and one
    # packed mask to two thresholds, four area-downsamples, a coverage-decided
    # bounding box and five packed masks. Doubling the templates, which is what
    # learning both square colours does, costs 0.7 ms of the 17.
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

    # A soft capture pays for the unsharp mask on top: 21 ms of it, in colour,
    # measured at 38 ms against the 21 here. The ceiling is separate rather
    # than raised, so that the cost of sharpening cannot quietly become the
    # cost of reading, and it is affordable for the same reason the 30 above
    # is: chesswatch runs classify once every CHECK_EVERY frames, so this lands
    # inside one 120 ms tick every few seconds rather than on every frame.
    blurry = boards[0][1].filter(ImageFilter.GaussianBlur(1.1))
    reader.classify(blurry)
    runs = []
    for _ in range(20):
        t0 = time.perf_counter()
        reader.classify(blurry)
        runs.append(time.perf_counter() - t0)
    soft_per = min(runs) * 1000
    print("      classify: %.0f ms when the capture needs sharpening" % soft_per)
    r.append(check("  and under 60 ms when it has to sharpen first",
                   soft_per < 60, True))

    cache.cleanup()
    print("\n%d/%d passed" % (sum(bool(x) for x in r), len(r)))
    return 0 if all(r) else 1


if __name__ == "__main__":
    sys.exit(main())
