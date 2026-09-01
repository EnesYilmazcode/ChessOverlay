"""Headless checks for the piece reader specifically.

selftest.py checks that the two reference screenshots read correctly. This
checks the harder things: that they keep reading correctly when the capture is
imperfect, that a piece set the templates were not drawn from is read rather
than refused wholesale, and that the reader says "?" instead of naming a piece
when it is not sure. Every floor below is a number that was actually measured,
not a target, so a drop here is a real regression and not a moved goalpost. The
one deliberate slack is the speed ceiling at the end: 30 ms against readings of
16 to 17 ms, so that a slower machine does not read as a slowdown in this code.

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
    # 487, against the 495 the solid mask named. Counted per size, the 45
    # pieces on the two boards come back named 45, 44, 43, 44, 44 and 30 at
    # 824, 664, 560, 400, 280 and 200px, and every refusal is MIN_OVERLAP
    # rather than the margin or the colour veto. Only 200px really gives
    # anything up, and there a square holds 25 screen pixels against the 40
    # cell grid its mask is compared on, so the outline is upsampled guesswork
    # and the match genuinely scores below 0.30.
    r.append(check("  and 487 of 512 squares still named", got[0] >= 487, True))

    got = tally(reader, offset(boards))
    print("      misaligned crops: %d correct, %d wrong, %d unclear" % got)
    r.append(check("a misaligned crop yields nothing wrong at all", got[1], 0))
    r.append(check("  and still names 898 of 1024 squares", got[0] >= 898, True))

    got = tally(reader, distorted(boards))
    print("      wrong brightness or blur: %d correct, %d wrong, %d unclear" % got)
    # 55, against 91 before any of this and 75 before the cutoffs were anchored
    # on the board's own ink. This row is where anchoring pays: brightness and
    # contrast are affine on pixel values, so measuring both ends off the board
    # makes the mask come out the same whatever the knobs are set to.
    r.append(check("distortion costs at most 55 wrong pieces of 1280",
                   got[1] <= 55, True))
    r.append(check("  and still names 1117 of 1280 squares", got[0] >= 1117, True))

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
    r.append(check("  and then reads 27 of its own 32 pieces, none wrong",
                   (got[0] >= 27, got[1]), (True, 0)))

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

    cache.cleanup()
    print("\n%d/%d passed" % (sum(bool(x) for x in r), len(r)))
    return 0 if all(r) else 1


if __name__ == "__main__":
    sys.exit(main())
