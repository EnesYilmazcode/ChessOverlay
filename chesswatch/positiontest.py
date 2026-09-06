"""Headless checks for the whole board solver.

Two halves, and they are asking different questions.

The first has no pixels in it. The solver takes a table of scores and nothing
else, so the tables are built by hand and made as good or as bad as each check
needs, which keeps the two failures apart: piecetest.py says whether the
templates match, this says whether the board is read correctly given whatever
the templates came back with.

The second renders real boards out of the screenshot fixtures, draws a mouse
pointer, a popup or a covered block over them, and puts what pieces.py makes of
those through the solver. The floor is measured there and nowhere else. It used
to be measured on the synthetic noise in the first half and had never seen an
obstructed board, which is what issue 33 is about, and a floor tuned on noise
turns out to answer a different question from the one an obstructed board asks.
That half draws no random numbers at all: the corpus is the fixtures, so it is
the same corpus every run and there is no seed for a result to be true of.

Every number below was measured and then written down, not picked as a target
first. Some are rates rather than zeroes, because zero is not what was
measured. The one deliberate slack is the speed ceiling at the end: 40 ms
against readings of 9 to 11 ms, so that a slower machine does not read as a
slowdown in this code.

Takes about a minute, most of it rendering and reading the boards in the
second half.

Run:  python positiontest.py
"""

import random
import sys
import time

import chess

import bench
import pieces as PC
import position as P
import setgen
import watcher as W
from fakeboard import cover, with_panel, with_pointer

NAMES = P.ORDER + P.EMPTY

# What pieces.py names a square on, which is MARGIN there. The reading it
# produces is the thing solve confirms or refuses, so a check that builds a
# reading by hand has to refuse the same squares pieces.py would.
READER_MARGIN = PC.MARGIN

# The floors the corpus below is swept at. Wide on purpose: the point of the
# sweep is the shape of the curve rather than the one number taken off it, and
# a band that stops just past the number it is defending proves nothing about
# what lies further up.
FLOORS = (0.0, 0.02, 0.05, 0.08, 0.10, P.MIN_PIN, 0.14, 0.16, 0.20, 0.30, 0.50)

# Ten legal positions, eight of them off random games and two built by hand to
# put material where only a promotion can reach. A solver that quietly forbids
# promoted material would pass every other check in this file.
BANK = [
    ["r.b.kbnr", "ppppn.pp", "....pq..", ".P...pN.",
     "P.P..P..", "...P....", "R..KP.PP", ".NBQ.B.R"],
    ["rnbqkbnr", "ppp..pp.", "....p..p", "...p....",
     "..P.PN..", "........", "PP.P.PPP", "RNBQKB.R"],
    ["rnb..br.", ".pk...p.", "..pp....", "p.P.pp..",
     "P....PPp", "..qP.N.n", "...BPQRP", ".R..K..."],
    [".rb..b.r", "p....kp.", ".q.p.ppn", "P.P.P.Q.",
     "...P..PP", "...nP...", ".P..K.R.", "R.B..B.."],
    ["rQ.q.k..", "p.p.....", "...b....", "..Rpp.pP",
     "Pp.P.n..", "BP..P..P", ".....r..", "N...KB.R"],
    [".n.k.br.", "..pq...p", "n.......", "...ppppP",
     ".P.P.Rn.", "r.....BB", "P..K....", "....R..."],
    ["k....b.r", "....B...", ".....Rnp", "........",
     "..P....P", ".....p..", "..R.....", "..NQKB.."],
    ["r.....q.", "......p.", "p..b.N.n", ".......B",
     "...p.k..", ".P......", ".......K", ".b..R..."],
    # Three white queens off six pawns: two of them were promoted, and the two
    # pawns that did it are gone, which is why six and not eight.
    ["Q.Q..k..", "........", "........", "........",
     "........", "........", "PPPPPP..", "...QK..."],
    # Two white bishops both on dark squares, off seven pawns, same reason.
    ["....k...", "........", "........", "........",
     "........", "....B...", "PPPPPPP.", "..B.K..."],
]

# A middlegame with both kings home and eight black pawns, used where a check
# needs a named square rather than one out of the bank.
MID = ["r...k..r", "pp.n.ppp", "..p.pn..", "...p....",
       "...P.B..", "..N.PN..", "PPP..PPP", "R..QKB.R"]

# One white bishop, on f1, which is a light square. Anything that would put a
# second white bishop on a light square needs a promotion, and white's eight
# pawns are all still there to prove none happened. A dark one is free.
ONE_BISHOP = ["....k...", "pppppppp", "........", "........",
              "........", "........", "PPPPPPPP", "....KB.."]

# Four white queens against eight white pawns: eleven units of a budget of
# eight, so three of the queens have nowhere to come from. Legal one square at
# a time, illegal as a board, and self consistent, which is the shape that
# ceilings taken off a previous position cannot catch by themselves.
IMPOSSIBLE = ["Q.Q.Qk..", "........", "........", "........",
              "........", "........", "PPPPPPPP", "...QK..."]


def check(name, got, want):
    ok = got == want
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        print("        got  ", got)
        print("        want ", want)
    return ok


def matrix(rows, best=0.90, rest=0.30, empty=0.95):
    """Scores that say plainly what `rows` says. The true name on a square
    scores `best`, an empty square `empty`, everything else `rest`, so the gap
    between the top two is 0.60 on a piece and 0.65 on a hole."""
    out = {}
    for r in range(8):
        for c in range(8):
            here = {symbol: rest for symbol in NAMES}
            if rows[r][c] == P.EMPTY:
                here[P.EMPTY] = empty
            else:
                here[rows[r][c]] = best
            out[(r, c)] = here
    return out


def smeared(rows, pull, noise, rnd):
    """The same scores after the two things that actually go wrong.

    `pull` drags every score toward 0.5, which is what issue 20 measured: the
    mask fills a piece and its outline into one blob, every template then
    scores about the same, and the whole signal is left in the last
    hundredths. `noise` is per template jitter, which is what a piece set the
    templates were not learned from looks like. Pull keeps the ranking and
    only shrinks the margins, so noise is what makes a top pick wrong.
    """
    out = matrix(rows)
    for square in out:
        for symbol in out[square]:
            value = 0.5 + pull * (out[square][symbol] - 0.5)
            out[square][symbol] = max(0.0, min(1.0, value + rnd.gauss(0, noise)))
    return out


def at(reading, floor):
    """The same reading at a different floor.

    Nothing in the solve depends on the floor except the last step, where a
    square that did not clear it is blanked, so one solve answers for every
    floor. That is what makes a thirty draw sweep across four floors cost
    thirty solves rather than a hundred and twenty. It holds for solve as well
    as for the raw board, because the other half of what solve asks, whether
    the caller read the same name, does not depend on the floor either.
    """
    return [[symbol if score >= floor else P.UNKNOWN
             for symbol, score in zip(row, scores)]
            for row, scores in zip(reading.rows, reading.confidence)]


def alone(scores, floor):
    """The same scores read square by square, which is what pieces.py does."""
    return P._ungoverned(P._candidates(scores), floor, "square by square")


def by_square(scores, margin=READER_MARGIN):
    """The reading a caller would arrive at on its own, which is what solve
    takes as its second argument and is only ever allowed to agree with."""
    return alone(scores, margin).rows


def whole(scores, prior=None, moved=None):
    """The board the flow picked, before any of it is handed back.

    solve only ever confirms a square the caller already read, so the checks
    that are about the counting rules themselves have to look at the
    assignment rather than at the answer. Reaching for a private to get it is
    the point rather than a shortcut: no caller can have this, and the checks
    below are the only thing that is allowed to see it.

    None when the counting rules cannot fill the board at all, which is what
    solve turns into a fallback.
    """
    got = P._assign(P._candidates(scores), P._caps(prior),
                    P._pinned(prior, moved))
    return None if got is None else P.Reading(got[0], got[1], got[2])


def tally(rows, truth):
    """Named and wrong. Counted apart on purpose, the same way piecetest.py
    counts them: an unknown square costs a confirmation pass that runs again a
    second later, a wrong one goes into the game record."""
    named = wrong = 0
    for r in range(8):
        for c in range(8):
            if rows[r][c] != P.UNKNOWN:
                named += 1
                if rows[r][c] != truth[r][c]:
                    wrong += 1
    return named, wrong


def read_both(pull, noise, draws, base=100):
    """Every board in BANK smeared `draws` times over, read square by square
    once and then put through the solver.

    The reading is taken at pieces.py's own margin and stays there, because it
    is not a thing the floor moves: it is what a caller would have arrived at
    on its own, and the floor is about what the solver will confirm out of it.
    Floors are applied to the solver's answer afterwards by at().
    """
    out = []
    for seed in range(draws):
        rnd = random.Random(base + seed)
        for truth in BANK:
            scores = smeared(truth, pull, noise, rnd)
            read = by_square(scores)
            out.append((truth, P.solve(scores, read, floor=0.0), read))
    return out


def counted(cases, floor):
    """(named, wrong) confirmed at this floor, and (named, wrong) as read."""
    solved = flat = (0, 0)
    for truth, got, read in cases:
        a = tally(at(got, floor), truth)
        b = tally(read, truth)
        solved = (solved[0] + a[0], solved[1] + a[1])
        flat = (flat[0] + b[0], flat[1] + b[1])
    return solved, flat


def without(scores, row, col, symbol):
    """The best board that does not put `symbol` on that square.

    Scored hopelessly rather than deleted, because a name left out of the
    table is offered at zero rather than refused, and zero is not out of
    reach. The solver picks the best remaining name itself, so this is one
    solve where forcing each rival in turn would be twelve.
    """
    cut = {square: dict(names) for square, names in scores.items()}
    cut[(row, col)][symbol] = -100.0
    return whole(cut)


def forced(scores, row, col, symbol):
    """The best board that has `symbol` on that square, whatever the scores
    say. Held there through the carry over path, with the rest of the board
    left unknown so that this one square is the only thing fixed."""
    prior = [[P.UNKNOWN] * 8 for _ in range(8)]
    prior[row][col] = symbol
    moved = [(r, c) for r in range(8) for c in range(8) if (r, c) != (row, col)]
    return whole(scores, prior=prior, moved=moved)


def hard_boards():
    """Nine boards where the counting rules actually bind, for checking the
    confidences on. Four are smeared, so the margins are small and every
    capacity is under pressure; the rest are the boards the checks below turn
    on. A clean board is no test of a confidence: nothing binds, every square
    comes back at exactly 0.600, and the local gap between the top two
    templates would give the same answer."""
    rnd = random.Random(7)
    out = [("smeared %d" % i, smeared(truth, 0.10, 0.010, rnd))
           for i, truth in enumerate(BANK[:4])]
    scores = matrix(MID)
    scores[(4, 5)]["Q"] = 0.92
    scores[(4, 5)]["B"] = 0.90
    out.append(("phantom queen", scores))
    scores = matrix(MID)
    scores[(3, 3)]["K"] = 0.97
    out.append(("phantom king", scores))
    scores = matrix(BANK[1])
    scores[(5, 0)]["P"] = 0.97
    out.append(("ninth pawn", scores))
    out.append(("three queens", matrix(BANK[8])))
    out.append(("two dark bishops", matrix(BANK[9])))
    return out


# ------------------------------------------- boards with real pixels on them

# Two sizes rather than four. 824 is a maximised board and 280 is about the
# smallest chess.com will draw one, and the two in between behave like the ends
# rather than like anything of their own.
BOARD_PX = (824, 280)

# What a capture does to a board on the way in, from setgen. "plain" is the
# render itself; the other three are the axes that cost the reader margin, and
# leaving them out leaves a corpus the reader finds easy and the floor cannot
# be measured on.
CAPTURES = ("plain", "small", "blur", "dim")

# What is drawn on top. Five of the six put something over the board, because a
# corpus that is mostly clean boards measures the reader rather than the floor,
# and the whole point of issue 33 is that the floor had only ever seen clean
# ones. cover() is the honest worst case: nothing can see through it and
# nothing should claim to.
HIDES = (
    ("nothing", lambda img: img),
    ("a pointer", lambda img: with_pointer(img, 3, 3)),
    ("a big pointer", lambda img: with_pointer(img, 5, 2, size=1.4)),
    ("one square covered", lambda img: cover(img, 2, 4)),
    ("four covered", lambda img: cover(img, 4, 1, across=2, down=2)),
    ("a popup", lambda img: with_panel(img, 1, 1, across=3, down=1)),
)


def _screen(square, flipped):
    """Where a board square lands on screen, row 0 at the top."""
    rank, file = chess.square_rank(square), chess.square_file(square)
    return (rank, 7 - file) if flipped else (7 - rank, file)


def _touched(board, move):
    """Every square a move rewrites, which is watcher._touched's rule.

    Written out again rather than borrowed, because the one there is a method
    on a tracker with a game in it. Getting it wrong is not a small error: a
    castle rewrites four squares, and a carry over that misses two of them
    carries a rook that has moved.
    """
    out = [move.from_square, move.to_square]
    if board.is_castling(move):
        rank = chess.square_rank(move.from_square)
        if (chess.square_file(move.to_square)
                > chess.square_file(move.from_square)):
            out += [chess.square(7, rank), chess.square(5, rank)]
        else:
            out += [chess.square(0, rank), chess.square(3, rank)]
    elif board.is_en_passant(move):
        out.append(chess.square(chess.square_file(move.to_square),
                                chess.square_rank(move.from_square)))
    return out


def scored(reader, board_img):
    """pieces.py's own scores for every square, as the table solve takes.

    classify() names each square and throws the scores away, so this runs the
    same pipeline over again and keeps them. A square the emptiness test says
    holds nothing is empty at 1 and everything else at 0, which is what _judge
    does with one; everything else is every template's best score for that
    piece type, which is what _judge ranks.
    """
    levels = PC._levels(board_img)
    feats = PC._board_features(board_img, levels)
    out = {}
    for i, feat in enumerate(feats):
        row, col = divmod(i, 8)
        here = {}
        if feat is None or not feat.occupied:
            here[P.EMPTY] = 1.0
        else:
            for score, symbol in PC.ranking(feat, reader.templates):
                here[symbol] = float(score)
        out[(row, col)] = here
    return out


def rendered():
    """Every board the floor is measured on, and everything known about it.

    Yields (what is drawn on it, the truth, what pieces.py read, the scores
    behind that reading, the position a move ago, the squares that move
    touched). Both fixture piece sets, both orientations, two sizes, four
    capture variants and six things drawn on top.

    The reader is taught the opening position in the same set at the same size
    first, the way the program teaches it when a game begins, so what it gets
    wrong here is what it would get wrong live rather than a set it has never
    seen. Yielded rather than built because the pictures cost more to hold than
    to make.
    """
    for _, draw in sorted(bench._real_sets().items()):
        for flipped in (False, True):
            for size in BOARD_PX:
                start = chess.Board()
                reader = PC.PieceReader()
                reader.learn(draw.render(start, flipped, size), start, flipped)
                for opening in bench.POSITIONS:
                    before = bench.board_of(opening)
                    legal = list(before.legal_moves)
                    move = legal[len(legal) // 2]
                    after = before.copy()
                    after.push(move)
                    if not draw.can_render(after):
                        continue
                    moved = [_screen(square, flipped)
                             for square in _touched(before, move)]
                    prior = W.grid_of(before, flipped)
                    truth = W.grid_of(after, flipped)
                    for capture in CAPTURES:
                        drawn = setgen.variants(
                            draw.render(after, flipped, size), capture)
                        for label, hide in HIDES:
                            img = hide(drawn)
                            yield (label, truth, reader.classify(img)[0],
                                   scored(reader, img), prior, moved)


def main():
    r = []

    # -- a clean board comes back untouched --------------------------------
    # Including the two positions with promoted material. The counting rules
    # have to let a third queen and a second dark squared bishop stand, or
    # they would only be trading one class of wrong answer for another.
    read = []
    weakest = 1.0
    for truth in BANK:
        scores = matrix(truth)
        got = P.solve(scores, by_square(scores))
        read.append(["".join(row) for row in got.rows] == truth
                    and got.fallback is None)
        weakest = min(weakest, min(min(row) for row in got.confidence))
    r.append(check("all ten clean boards read back exactly", read, [True] * 10))
    print("      weakest confidence on a clean board: %.3f" % weakest)
    r.append(check("  and the least sure square is still well over the floor",
                   weakest > P.MIN_PIN * 4, True))

    # -- the confidence is the real cost of being wrong --------------------
    # Not an estimate of it, and not the gap between the top two templates
    # either, which is the thing it has to be checked against: on a clean
    # board the two agree everywhere, so a clean board proves nothing here.
    # Every square of the nine boards above is re-solved with the name it was
    # given put out of reach, and how much worse that board is has to be the
    # number the solver reported.
    worst = 0.0
    differs = checked = 0
    for label, scores in hard_boards():
        got = whole(scores)
        for row in range(8):
            for col in range(8):
                ranked = sorted((score for _, score
                                 in P._candidates(scores)[(row, col)]),
                                reverse=True)
                local = min(1.0, max(0.0, ranked[0] - ranked[1]))
                said = got.confidence[row][col]
                differs += abs(local - said) > 1e-6
                checked += 1
                other = without(scores, row, col, got.rows[row][col])
                worst = max(worst, abs(min(1.0, got.total - other.total) - said))
    print("      %d squares re-solved, %d of them where the confidence is not"
          " the local gap" % (checked, differs))
    # Not zero: costs are integers a millionth of a score point apart, so a
    # confidence can be a rounding step away from the board it describes.
    r.append(check("  the confidence is the re-solved cost, to a millionth",
                   worst < 2e-6, True))
    r.append(check("  and it is telling those two apart on a third of them",
                   differs > checked // 3, True))

    # The same claim through a second, slower route: force every rival name on
    # in turn and take the best board that comes back. A rival that could not
    # be placed at all would return a fallback and its total would be the
    # unpinned one, which would agree with anything, so that is checked rather
    # than assumed.
    scores = hard_boards()[4][1]
    got = whole(scores)
    agree = 0.0
    fell_back = 0
    for row, col in ((4, 5), (7, 3), (0, 4), (3, 3)):
        rival = None
        for symbol in NAMES:
            if symbol == got.rows[row][col]:
                continue
            if symbol in "Pp" and row in (0, 7):
                continue
            other = forced(scores, row, col, symbol)
            if other is None:
                fell_back += 1
                continue
            if rival is None or other.total > rival:
                rival = other.total
        agree = max(agree, abs(min(1.0, got.total - rival)
                               - got.confidence[row][col]))
    r.append(check("  and forcing every rival name on instead agrees with it",
                   (agree < 2e-6, fell_back), (True, 0)))

    # -- a wrong top pick that the counting rules put right ----------------
    # f4 holds a white bishop and the templates put a queen a couple of
    # hundredths ahead of it. White already has a queen on d1 and eight pawns,
    # so a second queen would have to have been promoted and there is no pawn
    # left to spend. Square by square this is a queen, or a shrug; as a board
    # it is a bishop, and it is not close.
    scores = matrix(MID)
    scores[(4, 5)]["Q"] = 0.92
    # Half the reader's own margin, taken from it rather than written down, so
    # this stays a gap the reader shrugs at whatever that margin is set to.
    scores[(4, 5)]["B"] = 0.92 - READER_MARGIN / 2
    r.append(check("a second queen nobody can pay for is a bishop to the"
                   " board",
                   (alone(scores, 0.0).rows[4][5], by_square(scores)[4][5],
                    whole(scores).rows[4][5]), ("Q", P.UNKNOWN, "B")))
    print("      f4 as a bishop, by %.3f, against a top pick %.3f the other "
          "way" % (whole(scores).confidence[4][5], READER_MARGIN / 2))

    # And it is not handed over. That gap is under pieces.py's own margin, so
    # the reader shrugged at f4, and a shrug is the one thing this
    # module may not paint over: every guard downstream reads "?" as no
    # evidence. The bishop is what the board thinks, not what it may say.
    got = P.solve(scores, by_square(scores))
    r.append(check("  and it is still not named, because the reader shrugged",
                   (got.rows[4][5], got.rows[7][3]), (P.UNKNOWN, "Q")))

    # The same square with the queen scoring far enough ahead that the reader
    # does name it. Now there is something to disagree with, and disagreement
    # is a refusal rather than a correction: handing the bishop back would be
    # this module naming a square off the counting rules alone, and handing
    # the queen back would be it agreeing with a board it knows is illegal.
    scores[(4, 5)]["Q"] = 0.99
    got = P.solve(scores, by_square(scores))
    r.append(check("  a queen the reader did name is refused, not corrected",
                   (by_square(scores)[4][5], whole(scores).rows[4][5],
                    got.rows[4][5]), ("Q", "B", P.UNKNOWN)))
    r.append(check("  and nothing else on the board moved",
                   ["".join(row) for row in got.rows],
                   MID[:4] + ["...P.?.."] + MID[5:]))

    # A bishop cannot change square colour, so the same score means different
    # things on the two colours. White's only bishop is on f1, a light square:
    # a second light squared bishop needs a promotion white cannot afford, a
    # dark squared one is simply the other bishop.
    both = []
    for square in ((4, 5), (4, 2)):
        scores = matrix(ONE_BISHOP)
        scores[square][P.EMPTY] = 0.80
        scores[square]["B"] = 0.97          # 0.17 clear, either way round
        got = P.solve(scores, by_square(scores))
        both.append((whole(scores).rows[square[0]][square[1]],
                     got.rows[square[0]][square[1]],
                     round(got.confidence[square[0]][square[1]], 3)))
    # The reader says a bishop on both. On the dark square the board agrees and
    # the square is confirmed; on the light one it says the square is empty,
    # and two readings that disagree is a reason to look again rather than a
    # reason to pick one, so what comes back there is "?" and not ".".
    r.append(check("the same bishop score is taken on dark and refused on light",
                   both, [("B", "B", 0.17), (P.EMPTY, P.UNKNOWN, 0.43)]))

    # -- two kings on the board, one of them not real ----------------------
    # The phantom scores higher than the real one, so nothing local saves this.
    # What settles it is that giving d5 the king costs e1 its own best name as
    # well, and the pair is worth more than the point of score the phantom won.
    scores = matrix(MID)
    scores[(3, 3)]["K"] = 0.97
    got = P.solve(scores, by_square(scores))
    r.append(check("the higher scoring of two white kings is not the one kept",
                   (alone(scores, 0.0).rows[3][3], whole(scores).rows[3][3],
                    got.rows[3][3], got.rows[7][4]),
                   ("K", "p", P.UNKNOWN, "K")))
    # A phantom king costs the reader that square and nothing else. It is not
    # replaced with the pawn the board would put there, because the reader
    # never offered a pawn, but the real king on e1 is confirmed and so is
    # every other square, which is what the counting rules were for.
    r.append(check("  one king a side and one white queen survive",
                   [sum(row.count(letter) for row in got.rows)
                    for letter in "KkQ"], [1, 1, 1]))

    # -- a reading that would need a ninth pawn ----------------------------
    scores = matrix(BANK[1])
    scores[(5, 0)]["P"] = 0.97              # a ninth white pawn, over the hole
    board = whole(scores)
    got = P.solve(scores, by_square(scores))
    pawns = sum(row.count("P") for row in board.rows)
    r.append(check("a ninth pawn is refused however well it scores",
                   (board.rows[5][0], pawns, got.rows[5][0]),
                   (P.EMPTY, 8, P.UNKNOWN)))
    print("      the ninth pawn is refused by %.3f" % board.confidence[5][0])

    # -- a board with nothing to go on -------------------------------------
    # Every template ties everywhere. Legal boards fit and the solver picks
    # one, but no square has a reason to prefer what it was given, so none of
    # them is named. Inventing 64 pieces here is the failure this whole module
    # has to avoid, and it is the failure a legality solver invites.
    flat = {(row, col): {symbol: 0.5 for symbol in NAMES}
            for row in range(8) for col in range(8)}
    got = P.solve(flat, by_square(flat))
    r.append(check("a board where every template ties names nothing",
                   (tally(got.rows, BANK[0]),
                    max(max(row) for row in got.confidence)), ((0, 0), 0.0)))

    # Half a board. The row that is readable is read and the rest is not
    # guessed at from the counting rules alone.
    half = {}
    for row in range(8):
        for col in range(8):
            half[(row, col)] = (dict(matrix(MID)[(row, col)]) if row == 7
                                else {symbol: 0.5 for symbol in NAMES})
    got = P.solve(half, by_square(half))
    r.append(check("  and a board readable on one row reads that row only",
                   ("".join(got.rows[7]), got.rows[0].count(P.UNKNOWN)),
                   (MID[7], 8)))

    # -- carrying the last position forward --------------------------------
    # A square the move did not touch, whose pixels came back as mush. Cold
    # there is nothing to say about it. With the previous position it is not
    # read at all, it is carried, which is the one case where a square is
    # certain without any evidence for it on screen.
    scores = matrix(MID)
    scores[(2, 5)] = {symbol: 0.5 for symbol in NAMES}
    read = by_square(scores)
    cold = P.solve(scores, read)
    warm = P.solve(scores, read, prior=MID, moved=[(6, 4), (4, 4)])
    r.append(check("a square the move did not touch is carried over, not read",
                   (cold.rows[2][5], cold.confidence[2][5],
                    warm.rows[2][5], warm.confidence[2][5]),
                   (P.UNKNOWN, 0.0, "n", 1.0)))

    # The square the move did land on is still read from the pixels, and its
    # confidence is the ordinary one, not the carried certainty.
    after = [list(row) for row in BANK[1]]
    scores = matrix(["".join(row) for row in after])
    scores[(4, 4)] = {symbol: 0.20 for symbol in NAMES}
    scores[(4, 4)]["P"] = 0.60
    scores[(4, 4)][P.EMPTY] = 0.45
    warm = P.solve(scores, by_square(scores), prior=BANK[1],
                   moved=[(6, 4), (4, 4)])
    r.append(check("  and the square it did land on is read as usual",
                   (warm.rows[4][4], round(warm.confidence[4][4], 3)),
                   ("P", 0.15)))

    # And it has to clear the floor like anything else. The same square worth
    # a hundredth over the empty square instead of fifteen is under MIN_PIN,
    # and what comes back then is "?" even though the reader named it and the
    # board agreed with the name. The floor applies to a confirmation exactly
    # as it used to apply to a name, which is the whole of what moving it
    # changes.
    scores[(4, 4)]["P"] = 0.46
    # Read at no margin at all, because MIN_PIN now sits below the reader's
    # own: a correlation reader's confidences are smaller than a mask reader's
    # were, the floor moved down with them, and a square the reader is willing
    # to name always clears it. The floor still applies to a confirmation, and
    # this is what asks whether it does.
    read = alone(scores, 0.0).rows
    tight = P.solve(scores, read, prior=BANK[1], moved=[(6, 4), (4, 4)])
    loose = P.solve(scores, read, prior=BANK[1], moved=[(6, 4), (4, 4)],
                    floor=0.005)
    r.append(check("  and a confirmation under the floor is refused as well",
                   (read[4][4], tight.rows[4][4], loose.rows[4][4],
                    round(tight.confidence[4][4], 3)),
                   ("P", P.UNKNOWN, "P", 0.01)))

    # Material never increases. A black bishop where black has none is a legal
    # board on its own, and impossible one move on from a board without one.
    scores = matrix(MID)
    scores[(3, 4)][P.EMPTY] = 0.80
    scores[(3, 4)]["b"] = 0.97
    read = by_square(scores)
    cold = P.solve(scores, read)
    warm = P.solve(scores, read, prior=MID)
    # The board with the previous position in hand says the square is empty,
    # and what the caller gets is "?" rather than that emptiness: the reader
    # said a bishop, so the two disagree, and a disagreement is a square to
    # look at again.
    r.append(check("a man appearing out of nowhere is refused, cold it is not",
                   (cold.rows[3][4], warm.rows[3][4],
                    whole(scores, prior=MID).rows[3][4]),
                   ("b", P.UNKNOWN, P.EMPTY)))

    # -- a previous position that is not a position ------------------------
    # This is the one the module cannot catch on its own once it has adopted
    # the counts, because a previous position never contradicts ceilings taken
    # from itself. IMPOSSIBLE is the shape that matters: every square of it is
    # a legal thing to see, the board as a whole is not, and it agrees with
    # its own counts perfectly. Read cold the four queens are refused; handed
    # in as a prior it has to come out the same way, whatever `moved` says.
    scores = matrix(IMPOSSIBLE)
    cold = P.solve(scores, by_square(scores))
    warm = [P.solve(scores, by_square(scores), prior=IMPOSSIBLE, moved=moved)
            for moved in (None, (), [(0, 0)])]
    r.append(check("a self consistent but illegal prior is refused, not adopted",
                   [(sum(row.count("Q") for row in got.rows), got.fallback)
                    for got in warm],
                   [(0, "the previous position is not a position, read cold")]
                   * 3))
    r.append(check("  and it reads exactly as it does with no prior at all",
                   [got.rows == cold.rows for got in warm], [True] * 3))

    # The two easier shapes, and two that are not grids at all. A prior is
    # dropped whole, its ceilings with its squares, so all four come back as
    # the cold reading rather than as a partly trusted one.
    bad = []
    for square, symbol in (((5, 0), "P"), ((0, 0), "p")):
        broken = [list(row) for row in BANK[1]]
        broken[square[0]][square[1]] = symbol
        bad.append(broken)
    bad.append([["X"] * 8] * 8)              # not a letter this module knows
    bad.append([[None] * 8] * 8)             # not a letter at all
    bad.append([["."] * 8] * 3)              # not eight rows
    bad.append("nonsense")                   # not a grid
    scores = matrix(BANK[1])
    got = [P.solve(scores, by_square(scores), prior=prior, moved=())
           for prior in bad]
    r.append(check("a prior that is not a position at all is dropped whole",
                   [(["".join(row) for row in one.rows] == BANK[1],
                     one.fallback) for one in got],
                   [(True, "the previous position is not a position,"
                           " read cold")] * len(bad)))

    # -- a reading that is not a reading -----------------------------------
    # It arrives from the same reader a previous position does and gets the
    # same treatment: dropped, said out loud, and nothing confirmed. Raising
    # would be worse, because this runs inside a capture loop, and confirming
    # everything would be worse still, since there would be nothing left to
    # have confirmed it against.
    unreadable = ([["X"] * 8] * 8, [[None] * 8] * 8, [["."] * 8] * 3,
                  "nonsense")
    got = [P.solve(scores, read) for read in unreadable]
    r.append(check("a reading that is not a grid confirms nothing at all",
                   [(tally(one.rows, BANK[1]), one.fallback) for one in got],
                   [((0, 0), "the reading handed in is not a grid,"
                             " nothing is confirmed")] * len(unreadable)))

    # -- reading it as a board against reading it square by square ---------
    # All ten positions, three noise draws each, at the two things that go
    # wrong. `pull` shrinks every margin without changing the ranking, so on
    # its own it only ever costs confirmed squares; `noise` is what makes a top
    # pick wrong, so the bottom two blocks are the only ones that can produce
    # a wrong piece at all.
    #
    # The floors here are below MIN_PIN and are not it. `pull` of 0.10 leaves
    # every margin on these boards under a tenth by construction, so a floor of
    # 0.12 confirms almost nothing here and the band would say nothing about
    # the shape. What the floor is gets measured further down, on pixels.
    print("\n      pull noise  floor   confirmed named/wrong  as read"
          " named/wrong")
    seen = {}
    for pull, noise in ((1.00, 0.000), (0.10, 0.010),
                        (0.10, 0.020), (0.10, 0.030)):
        cases = read_both(pull, noise, 1 if noise == 0 else 3)
        for floor in (0.00, 0.06, 0.10):
            seen[(noise, floor)] = counted(cases, floor)
            solved, flat_read = seen[(noise, floor)]
            print("      %.2f %.3f  %.2f      %4d / %-4d          %4d / %-4d"
                  % (pull, noise, floor, solved[0], solved[1],
                     flat_read[0], flat_read[1]))

    r.append(check("a clean board is untouched by every floor in the band",
                   [seen[(0.000, f)][0] for f in (0.00, 0.06, 0.10)],
                   [(640, 0)] * 3))

    # The whole point of the module, and it is a subtraction rather than an
    # addition. The solver may only confirm a square the reader named, so it
    # can never name more of them; what it is for is that it names fewer of
    # them wrong.
    for noise in (0.010, 0.020, 0.030):
        solved, flat_read = seen[(noise, 0.06)]
        print("      noise %.3f: %d squares confirmed of %d read, %+.0f%%, "
              "%d wrong against %d"
              % (noise, solved[0], flat_read[0],
                 100.0 * (solved[0] - flat_read[0]) / max(flat_read[0], 1),
                 solved[1], flat_read[1]))
        r.append(check("  reading it as a board confirms a subset and gets no"
                       " more of them wrong",
                       (solved[0] <= flat_read[0], solved[1] <= flat_read[1]),
                       (True, True)))
    # Only the bottom block. At the two quieter settings the reader gets
    # nothing wrong over these draws, so there is nothing there to take away
    # and "strictly fewer" would be asking for a number that was never there.
    # Three against nine on this seed block, and the inequality rather than
    # those two numbers, because they are the one thing here a seed decides.
    r.append(check("  and where the reader does get squares wrong, strictly"
                   " fewer",
                   seen[(0.030, 0.06)][0][1] < seen[(0.030, 0.06)][1][1],
                   True))

    # Which direction that comes from. Two of these three are now structural
    # rather than measured: a wrong answer cannot be turned right, because the
    # only name this module may hand back is the one the reader gave, and a
    # square nobody read cannot come back wrong, because it cannot come back
    # at all. They are counted anyway, over fifteen draws rather than three,
    # because that is the whole of what the contract promises and a promise
    # nothing checks is a comment.
    print()
    for noise in (0.020, 0.030):
        rescued = refused = lost = 0
        for truth, got, flat_read in read_both(0.10, noise, 15, base=0):
            solved = at(got, 0.06)
            for row in range(8):
                for col in range(8):
                    want, mine, theirs = (truth[row][col], solved[row][col],
                                          flat_read[row][col])
                    if theirs not in (P.UNKNOWN, want):
                        rescued += mine == want
                        refused += mine == P.UNKNOWN
                    elif theirs == P.UNKNOWN and mine != P.UNKNOWN:
                        lost += 1
        print("      noise %.3f: %d wrong answers turned right, %d refused,"
              " %d squares the reader left blank came back named"
              % (noise, rescued, refused, lost))
        if noise == 0.020:
            # The reader gets nothing wrong over this seed block, so all three
            # are zero and the two that matter are zero for a reason rather
            # than for want of anything to count.
            r.append(check("  nothing is rescued and nothing blank is filled"
                           " in", (rescued, lost), (0, 0)))
        else:
            r.append(check("  and where it is wrong the gain is refusal",
                           (rescued, lost, refused > 0), (0, 0, True)))

    # -- the floor, measured on boards with things drawn over them ---------
    # The floor used to be a number about the synthetic noise above, and issue
    # 33 is that it had never seen an obstructed board. This is the same
    # question asked of real pixels: pieces.py reads a rendered board with a
    # pointer, a popup or a covered block on it, and the solver is handed both
    # what it read and the scores underneath.
    #
    # Nothing random in any of it. The corpus is the fixtures, so there is no
    # seed for one of these numbers to be true of and no other seed for it to
    # be false of, which is the failing the floor had before.
    print()
    started = time.time()
    boards = 0
    reader_named = reader_wrong = reader_blank = 0
    filled = {f: [0, 0] for f in FLOORS}      # naming what the reader refused
    agreed = {f: [0, 0] for f in FLOORS}      # confirming what it did read
    carried = carried_wrong = 0
    invented = contradicted = conjured = 0
    weakest_clear = 1.0
    for label, truth, read, scores, prior, moved in rendered():
        boards += 1
        cold = P.solve(scores, read, floor=0.0)
        warm = P.solve(scores, read, prior=prior, moved=moved, floor=0.0)
        board = whole(scores)
        for row in range(8):
            for col in range(8):
                want, got = truth[row][col], read[row][col]
                mine, sure = cold.rows[row][col], cold.confidence[row][col]
                if got == P.UNKNOWN:
                    reader_blank += 1
                    # The contract, both halves of it. Cold there is nothing
                    # to confirm, so nothing may be named; with a previous
                    # position a square the move did not touch may be carried,
                    # and only that.
                    invented += mine != P.UNKNOWN
                    if warm.rows[row][col] != P.UNKNOWN:
                        if (row, col) in P._pinned(prior, moved):
                            carried += 1
                            carried_wrong += warm.rows[row][col] != want
                        else:
                            conjured += 1
                    for floor in FLOORS:
                        if board.confidence[row][col] >= floor:
                            filled[floor][0] += 1
                            filled[floor][1] += board.rows[row][col] != want
                else:
                    reader_named += 1
                    reader_wrong += got != want
                    contradicted += mine not in (P.UNKNOWN, got)
                    if label == "nothing" and got == want:
                        weakest_clear = min(weakest_clear, sure)
                    for floor in FLOORS:
                        if mine != P.UNKNOWN and sure >= floor:
                            agreed[floor][0] += 1
                            agreed[floor][1] += mine != want
    print("      %d boards in %.0fs, five in six with something drawn over"
          " them" % (boards, time.time() - started))
    print("      pieces.py named %d squares, %d of them wrong, and refused %d"
          % (reader_named, reader_wrong, reader_blank))

    r.append(check("the answer is only ever squares the caller had read",
                   (invented, contradicted), (0, 0)))
    r.append(check("  and the one exception is a square carried over from the"
                   " previous position",
                   (conjured, carried > 0, carried_wrong), (0, True, 0)))
    kept, kept_wrong = agreed[P.MIN_PIN]
    # A subset, and nothing wrong in it. The reader itself now names nothing
    # wrong on this corpus, so "fewer wrong than the reader" is a comparison of
    # two zeroes; what is left to check is that confirming never adds one.
    r.append(check("  so it names a subset of the reading with nothing wrong"
                   " in it",
                   (kept <= reader_named, kept_wrong, kept_wrong <= reader_wrong),
                   (True, 0, True)))

    # What the floor would have been asked to do if this module filled blanks
    # in, which is the thing issue 33 proposed raising it to fix. It cannot:
    # the counting rules have to put sixty four names somewhere whether the
    # pixels said anything or not, and a name with no pixels behind it is
    # certain for exactly that reason. Raising the floor takes the cheap wrong
    # answers off the top and leaves the confident ones.
    print("\n      floor  confirmed  wrong |  filling a blank would name"
          "  wrong")
    for floor in FLOORS:
        print("      %.2f   %8d %6d |  %25d %6d"
              % (floor, agreed[floor][0], agreed[floor][1],
                 filled[floor][0], filled[floor][1]))
    top = FLOORS[-1]
    r.append(check("filling a square the reader refused is wrong at every"
                   " floor there is",
                   (min(filled[f][1] for f in FLOORS) > 0,
                    filled[top][1] * 5 > filled[top][0]), (True, True)))

    # Where the floor itself goes. Above it an agreement is worth having and
    # below it one is not, so it is pinned by where that changes rather than
    # chosen: the weakest confidence on a correctly read square of a board
    # with nothing drawn over it decides how high it may go, and the error
    # rate underneath decides that it should go that high.
    low, low_wrong = (agreed[0.0][0] - kept, agreed[0.0][1] - kept_wrong)
    print("\n      an agreement under the floor is wrong %.0f%% of the time,"
          " over it %.2f%%" % (100.0 * low_wrong / max(low, 1),
                               100.0 * kept_wrong / max(kept, 1)))
    print("      weakest confidence on a square read right off an unobstructed"
          " board: %.4f" % weakest_clear)
    # It used to be that an agreement under the floor was wrong ten times as
    # often as one over it, and that is what put the floor where it is. The
    # reader gets nothing wrong on this corpus now, either side of it, so the
    # error rate no longer says anything and the other half of the measurement
    # is the whole of it: the floor is the highest one that still leaves a
    # board with nothing drawn over it read whole.
    r.append(check("an agreement is not wrong either side of the floor",
                   (low_wrong, kept_wrong), (0, 0)))
    r.append(check("  the floor leaves an unobstructed board whole",
                   weakest_clear >= P.MIN_PIN, True))
    r.append(check("  and it is the last floor that does",
                   weakest_clear < P.MIN_PIN + 0.02, True))
    r.append(check("  which is what makes MIN_PIN this number",
                   P.MIN_PIN, 0.015))

    # -- speed --------------------------------------------------------------
    # The best of twenty warmed passes, for the reason piecetest.py gives: a
    # mean over a few cold runs tracks what else the machine is doing more
    # than it tracks this code.
    scores = matrix(BANK[0])
    read = by_square(scores)
    P.solve(scores, read)
    runs = []
    for _ in range(20):
        t0 = time.perf_counter()
        P.solve(scores, read)
        runs.append(time.perf_counter() - t0)
    per = min(runs) * 1000
    print("\n      solve: %.0f ms per board, best of 20" % per)
    r.append(check("  a whole board is solved in under 40 ms", per < 40, True))

    print("\n%d/%d passed" % (sum(bool(x) for x in r), len(r)))
    return 0 if all(r) else 1


if __name__ == "__main__":
    sys.exit(main())
