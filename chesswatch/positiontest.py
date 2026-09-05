"""Headless checks for the whole board solver.

No pixels here. The solver takes a table of scores and nothing else, so the
tables are built by hand and made as good or as bad as each check needs. That
keeps the two failures apart: piecetest.py says whether the templates match,
this says whether the board is read correctly given whatever the templates
came back with.

Every number below was measured on the ten positions in BANK and then written
down, not picked as a target first. Two of them are rates rather than zeroes,
because zero is not what was measured. The one deliberate slack is the speed
ceiling at the end: 40 ms against readings of 9 to 11 ms, so that a slower
machine does not read as a slowdown in this code.

Takes about fifteen seconds, most of it re-solving the board once per square
to check the confidences against something other than themselves.

Run:  python positiontest.py
"""

import random
import sys
import time

import position as P

NAMES = P.ORDER + P.EMPTY

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
    thirty solves rather than a hundred and twenty.
    """
    return [[symbol if score >= floor else P.UNKNOWN
             for symbol, score in zip(row, scores)]
            for row, scores in zip(reading.rows, reading.confidence)]


def alone(scores, floor):
    """The same scores read square by square, which is what pieces.py does."""
    return P._ungoverned(P._candidates(scores), floor, "square by square")


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
    """Every board in BANK smeared `draws` times over, solved once and read
    square by square once. Floors are applied afterwards by at()."""
    out = []
    for seed in range(draws):
        rnd = random.Random(base + seed)
        for truth in BANK:
            scores = smeared(truth, pull, noise, rnd)
            out.append((truth, P.solve(scores, floor=0.0), alone(scores, 0.0)))
    return out


def counted(cases, floor):
    """(named, wrong) solved and (named, wrong) square by square."""
    solved = flat = (0, 0)
    for truth, got, raw in cases:
        a = tally(at(got, floor), truth)
        b = tally(at(raw, floor), truth)
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
    return P.solve(cut, floor=0.0)


def forced(scores, row, col, symbol):
    """The best board that has `symbol` on that square, whatever the scores
    say. Held there through the carry over path, with the rest of the board
    left unknown so that this one square is the only thing fixed."""
    prior = [[P.UNKNOWN] * 8 for _ in range(8)]
    prior[row][col] = symbol
    moved = [(r, c) for r in range(8) for c in range(8) if (r, c) != (row, col)]
    return P.solve(scores, prior=prior, moved=moved, floor=0.0)


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


def main():
    r = []

    # -- a clean board comes back untouched --------------------------------
    # Including the two positions with promoted material. The counting rules
    # have to let a third queen and a second dark squared bishop stand, or
    # they would only be trading one class of wrong answer for another.
    read = []
    weakest = 1.0
    for truth in BANK:
        got = P.solve(matrix(truth))
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
        got = P.solve(scores, floor=0.0)
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
    got = P.solve(scores, floor=0.0)
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
            if other.fallback is not None:
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
    scores[(4, 5)]["B"] = 0.90
    got = P.solve(scores)
    r.append(check("a second queen nobody can pay for is read as the bishop",
                   (alone(scores, 0.0).rows[4][5],
                    alone(scores, P.MIN_PIN).rows[4][5], got.rows[4][5]),
                   ("Q", P.UNKNOWN, "B")))
    r.append(check("  and nothing else on the board moved",
                   ["".join(row) for row in got.rows], MID))
    print("      f4 as a bishop, by %.3f, against a top pick 0.02 the other way"
          % got.confidence[4][5])

    # A bishop cannot change square colour, so the same score means different
    # things on the two colours. White's only bishop is on f1, a light square:
    # a second light squared bishop needs a promotion white cannot afford, a
    # dark squared one is simply the other bishop.
    both = []
    for square in ((4, 5), (4, 2)):
        scores = matrix(ONE_BISHOP)
        scores[square][P.EMPTY] = 0.90
        scores[square]["B"] = 0.97          # 0.07 clear, either way round
        got = P.solve(scores)
        both.append((got.rows[square[0]][square[1]],
                     round(got.confidence[square[0]][square[1]], 3)))
    r.append(check("the same bishop score is taken on dark and refused on light",
                   both, [("B", 0.07), (P.EMPTY, 0.53)]))

    # -- two kings on the board, one of them not real ----------------------
    # The phantom scores higher than the real one, so nothing local saves this.
    # What settles it is that giving d5 the king costs e1 its own best name as
    # well, and the pair is worth more than the point of score the phantom won.
    scores = matrix(MID)
    scores[(3, 3)]["K"] = 0.97
    got = P.solve(scores)
    r.append(check("the higher scoring of two white kings is not the one kept",
                   (alone(scores, 0.0).rows[3][3], got.rows[3][3],
                    got.rows[7][4]), ("K", "p", "K")))
    r.append(check("  one king a side and one white queen survive",
                   [sum(row.count(letter) for row in got.rows)
                    for letter in "KkQ"], [1, 1, 1]))

    # -- a reading that would need a ninth pawn ----------------------------
    scores = matrix(BANK[1])
    scores[(5, 0)]["P"] = 0.97              # a ninth white pawn, over the hole
    got = P.solve(scores)
    r.append(check("a ninth pawn is refused however well it scores",
                   (got.rows[5][0], sum(row.count("P") for row in got.rows)),
                   (P.EMPTY, 8)))
    print("      the ninth pawn is refused by %.3f" % got.confidence[5][0])

    # -- a board with nothing to go on -------------------------------------
    # Every template ties everywhere. Legal boards fit and the solver picks
    # one, but no square has a reason to prefer what it was given, so none of
    # them is named. Inventing 64 pieces here is the failure this whole module
    # has to avoid, and it is the failure a legality solver invites.
    flat = {(row, col): {symbol: 0.5 for symbol in NAMES}
            for row in range(8) for col in range(8)}
    got = P.solve(flat)
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
    got = P.solve(half)
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
    cold = P.solve(scores)
    warm = P.solve(scores, prior=MID, moved=[(6, 4), (4, 4)])
    r.append(check("a square the move did not touch is carried over, not read",
                   (cold.rows[2][5], cold.confidence[2][5],
                    warm.rows[2][5], warm.confidence[2][5]),
                   (P.UNKNOWN, 0.0, "n", 1.0)))

    # The square the move did land on is still read from the pixels, and its
    # confidence is the ordinary one, not the carried certainty.
    after = [list(row) for row in BANK[1]]
    scores = matrix(["".join(row) for row in after])
    scores[(4, 4)] = {symbol: 0.20 for symbol in NAMES}
    scores[(4, 4)]["P"] = 0.55
    scores[(4, 4)][P.EMPTY] = 0.45
    warm = P.solve(scores, prior=BANK[1], moved=[(6, 4), (4, 4)])
    r.append(check("  and the square it did land on is read as usual",
                   (warm.rows[4][4], round(warm.confidence[4][4], 3)),
                   ("P", 0.1)))

    # Material never increases. A black bishop where black has none is a legal
    # board on its own, and impossible one move on from a board without one.
    scores = matrix(MID)
    scores[(3, 4)][P.EMPTY] = 0.90
    scores[(3, 4)]["b"] = 0.97
    cold = P.solve(scores)
    warm = P.solve(scores, prior=MID)
    r.append(check("a man appearing out of nowhere is refused, cold it is not",
                   (cold.rows[3][4], warm.rows[3][4]), ("b", P.EMPTY)))

    # -- a previous position that is not a position ------------------------
    # This is the one the module cannot catch on its own once it has adopted
    # the counts, because a previous position never contradicts ceilings taken
    # from itself. IMPOSSIBLE is the shape that matters: every square of it is
    # a legal thing to see, the board as a whole is not, and it agrees with
    # its own counts perfectly. Read cold the four queens are refused; handed
    # in as a prior it has to come out the same way, whatever `moved` says.
    cold = P.solve(matrix(IMPOSSIBLE))
    warm = [P.solve(matrix(IMPOSSIBLE), prior=IMPOSSIBLE, moved=moved)
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
    got = [P.solve(matrix(BANK[1]), prior=prior, moved=()) for prior in bad]
    r.append(check("a prior that is not a position at all is dropped whole",
                   [(["".join(row) for row in one.rows] == BANK[1],
                     one.fallback) for one in got],
                   [(True, "the previous position is not a position,"
                           " read cold")] * len(bad)))

    # -- reading it as a board against reading it square by square ---------
    # All ten positions, three noise draws each, at the two things that go
    # wrong. `pull` shrinks every margin without changing the ranking, so on
    # its own it only ever costs named squares; `noise` is what makes a top
    # pick wrong, so the bottom two blocks are the only ones that can produce
    # a wrong piece at all.
    print("\n      pull noise  floor   solved named/wrong  alone named/wrong")
    seen = {}
    for pull, noise in ((1.00, 0.000), (0.10, 0.010),
                        (0.10, 0.020), (0.10, 0.030)):
        cases = read_both(pull, noise, 1 if noise == 0 else 3)
        for floor in (0.03, P.MIN_PIN, 0.07):
            seen[(noise, floor)] = counted(cases, floor)
            solved, flat_read = seen[(noise, floor)]
            print("      %.2f %.3f  %.2f    %4d / %-4d       %4d / %-4d"
                  % (pull, noise, floor, solved[0], solved[1],
                     flat_read[0], flat_read[1]))

    r.append(check("a clean board is untouched by every floor in the band",
                   [seen[(0.000, f)][0] for f in (0.03, P.MIN_PIN, 0.07)],
                   [(640, 0)] * 3))

    # The whole point of the module. Same scores, same floor, one board read
    # square by square and the other read as a position.
    for noise in (0.010, 0.020, 0.030):
        solved, flat_read = seen[(noise, P.MIN_PIN)]
        print("      noise %.3f: %d squares named against %d, %+.0f%%, "
              "%d wrong against %d"
              % (noise, solved[0], flat_read[0],
                 100.0 * (solved[0] - flat_read[0]) / flat_read[0],
                 solved[1], flat_read[1]))
        r.append(check("  reading it as a board names more and gets no more"
                       " of them wrong",
                       (solved[0] > flat_read[0], solved[1] <= flat_read[1]),
                       (True, True)))

    # Which direction that gain comes from, because the percentage hides it.
    # The counting rules almost never turn a wrong answer into a right one.
    # What they do is name squares nobody could read, and refuse squares that
    # would have been read wrong. Once the reading is bad enough, the second
    # of those starts running backwards as well.
    print()
    for noise in (0.020, 0.030):
        rescued = refused = lost = 0
        for truth, got, raw in read_both(0.10, noise, 15, base=0):
            solved, flat_read = at(got, P.MIN_PIN), at(raw, P.MIN_PIN)
            for row in range(8):
                for col in range(8):
                    want, mine, theirs = (truth[row][col], solved[row][col],
                                          flat_read[row][col])
                    if theirs not in (P.UNKNOWN, want):
                        rescued += mine == want
                        refused += mine == P.UNKNOWN
                    elif theirs == P.UNKNOWN and mine not in (P.UNKNOWN, want):
                        lost += 1
        print("      noise %.3f: %d wrong answers turned right, %d refused,"
              " %d squares went from unknown to wrong"
              % (noise, rescued, refused, lost))
        if noise == 0.020:
            r.append(check("  at the measured settings nothing is made worse",
                           (rescued, refused, lost), (0, 0, 0)))
        else:
            r.append(check("  and past them the gain is refusal, not rescue",
                           (rescued, refused > lost), (0, True)))

    # -- what the floor is, and why it is that number ----------------------
    # Thirty draws rather than three, because this is a rate and three draws
    # do not put enough squares under it to measure one. Noise 0.020 is the
    # setting that decides it: below that nothing is read wrong at any floor
    # here, above it the reading is bad either way.
    print()
    cases = read_both(0.10, 0.020, 30, base=0)
    band = {}
    for floor in (0.04, P.MIN_PIN, 0.06):
        band[floor] = counted(cases, floor)
        named, wrong = band[floor][0]
        print("      floor %.2f  %5d named, %d wrong, 1 in %s"
              % (floor, named, wrong, named // wrong if wrong else "none"))

    # No floor in the band reads nothing wrong, so the floor is a rate and
    # not a promise. What picks 0.05 is that the rate stops improving there:
    # 0.04 is three and a half times worse, and 0.06 gives up 2217 more named
    # squares for the same two errors. Both errors at 0.05 are a pawn read as
    # empty, which is a square dropped rather than a piece invented, and the
    # tracker retries a dropped square a second later.
    r.append(check("the floor cuts the error rate, and stops paying past 0.05",
                   (band[0.04][0][1] > band[P.MIN_PIN][0][1],
                    band[0.06][0][1] >= band[P.MIN_PIN][0][1],
                    band[0.06][0][0] < band[P.MIN_PIN][0][0]),
                   (True, True, True)))
    r.append(check("  and MIN_PIN is that number", P.MIN_PIN, 0.05))
    kinds = {}
    for truth, got, _ in cases:
        solved = at(got, P.MIN_PIN)
        for row in range(8):
            for col in range(8):
                if solved[row][col] not in (P.UNKNOWN, truth[row][col]):
                    kinds[truth[row][col] + " read as " + solved[row][col]] = (
                        kinds.get(truth[row][col] + " read as "
                                  + solved[row][col], 0) + 1)
    print("      every error at the floor: %s" % kinds)
    r.append(check("  and what gets through is squares dropped, not invented",
                   sorted(kinds), ["P read as .", "p read as ."]))

    # -- speed --------------------------------------------------------------
    # The best of twenty warmed passes, for the reason piecetest.py gives: a
    # mean over a few cold runs tracks what else the machine is doing more
    # than it tracks this code.
    scores = matrix(BANK[0])
    P.solve(scores)
    runs = []
    for _ in range(20):
        t0 = time.perf_counter()
        P.solve(scores)
        runs.append(time.perf_counter() - t0)
    per = min(runs) * 1000
    print("\n      solve: %.0f ms per board, best of 20" % per)
    r.append(check("  a whole board is solved in under 40 ms", per < 40, True))

    print("\n%d/%d passed" % (sum(bool(x) for x in r), len(r)))
    return 0 if all(r) else 1


if __name__ == "__main__":
    sys.exit(main())
