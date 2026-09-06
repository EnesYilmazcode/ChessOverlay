"""Read the 64 squares as one position instead of as 64 separate pictures.

pieces.py scores every square against every template and then settles each
square on its own: the best template wins if it beats the runner up by a
margin, and the square comes back as "?" when it does not. That throws away
the one fact that is free, which is that the 64 squares are a chess position
and not 64 unrelated photographs. A square that is a coin flip between a rook
and a bishop is often settled outright by the rest of the board, because the
other bishop of that colour is already accounted for, or because a second
queen would need a promotion the pawns still standing cannot pay for.

This takes the same scores and picks the whole board at once. Squares and
piece names are the two sides of a min cost flow, and the counting rules a
legal position obeys are capacities on the piece side: one king a side, at
most eight pawns, at most sixteen men, no pawn on the top or the bottom row,
and every queen past the first, rook past the second, knight past the second
or same coloured bishop past the first paid for out of the pawn budget,
because it had to be promoted to get there. The cheapest flow is the highest
scoring board that obeys all of that at once, which is not in general the
board you get by taking each square's best guess.

Confidence falls out of the same solve rather than out of a second pass. Give
a square another name and the name has to come from wherever it is now, and
whatever that square becomes has to come from somewhere else again, so the
question "how much worse is the board if this square is something else" is
the cheapest cycle through what the flow left behind. That number, and not
the raw gap between the top two templates, is what has to clear a floor
before a square is named; below the floor it stays "?". It is the answer a
full re-solve would give, not an estimate of it, which positiontest.py checks
square by square against actually re-solving.

A caller that knows the position before the move and which squares the move
touched gets two more things. The squares the move did not touch are carried
across instead of read again, so a piece the mouse pointer or the last move
highlight sat on top of costs nothing. And no side may end up holding more
men, or more of any one kind, than it held a move ago.

That second one is only safe because the previous position is put through the
same counting rules first. Its counts become ceilings, and a board can never
contradict ceilings taken from itself, so a misread previous position would
otherwise hand itself back confidently while the same board read cold was
refused. One that fails is dropped whole, its ceilings with its squares,
because the same misreading produced both.

What comes back is a second opinion and never a reading. A square is named
only where the caller had already read it as that same name, or where it was
carried over from the caller's own previous position; everything else comes
back "?". So this can take a name away and cannot add one, which is why the
caller's own reading is an argument here rather than an option.

That is a contract and not a convention, because the rest of the program is
built on "?" meaning no evidence: a frame with a blank on it is dropped whole
and read again a moment later, a grid with a blank in it is not turned into a
position at all, and the highlight reader will not say whose man moved onto
one. A module that turned "?" into a letter would switch all of that off
without saying so, and the letters it puts there are mostly wrong. Over 768
rendered boards, five in six of them with a pointer, a popup or a covered
block drawn over them, naming the squares the reader refused is wrong 29% of
the time when the board is allowed to answer whatever it likes, and still 72%
wrong at a floor of 0.50. No floor makes it safe, because the counting rules
have to put sixty four names somewhere whether the pixels said anything or
not. Refusing to answer is the fix and the floor is not.

Rows are in screen order, row 0 at the top, the same way pieces.py hands them
over. Which way the board faces never comes into it. Turning a board half way
round maps row 0 to row 7 and leaves every square's colour alone, so "no pawn
on the top or the bottom row" and the two bishop colours read the same from
either side.

The counting rules are necessary, not sufficient. A position can satisfy
every one of them and still be unreachable from the opening for reasons no
count can see, so this narrows the field rather than proving anything.
"""

import heapq

ORDER = "KQRBNPkqrbnp"
EMPTY = "."
UNKNOWN = "?"
_KNOWN = frozenset(ORDER + EMPTY + UNKNOWN)

# Scores arrive as floats around 0 to 1 and shortest paths want integers, so
# every score is multiplied out. A millionth of a score point is far below the
# noise on any template match, and integers keep the path costs exact.
SCALE = 1000000

# What a square pays for being called anything other than a king. Larger than
# the entire score range of 64 squares, so finding both kings always beats any
# amount of shape evidence: that is what makes "one king a side" a hard rule
# instead of a preference. It is charged on every arc that is not a king, so
# it cancels out of every reduced cost and never reaches a confidence.
KING_PUSH = 100 * SCALE

# How far clear the board has to prefer a name before that name is allowed to
# confirm the square the caller read.
#
# It used to be 0.05, measured against score noise generated inside
# positiontest.py. Measured instead on real pixels, over 768 boards rendered
# from both fixture piece sets in both orientations at two sizes and four
# capture variants, five in six of them with something drawn over the board.
# No random numbers anywhere in that: the corpus is the fixtures, and it is
# the same corpus every run.
#
# Pinned from above rather than chosen. The weakest confidence on a correctly
# read square of a board with nothing drawn over it is what decides how high it
# may go, since a floor above that refuses squares that were right.
#
# It was 0.12, measured against a reader that told the pieces apart by mask
# overlap. That reader is gone and this number moved with it: correlation
# scores sit in a narrower band and the gap between two piece types is smaller,
# so a confidence measured on those scores is smaller too, and the weakest one
# on an unobstructed board is now 0.0153 rather than 0.1262. 0.015 is the last
# floor that leaves such a board whole.
#
# What used to justify taking the last free step was that the agreements below
# it were wrong 19% of the time against 0.5% above. That is not measurable any
# more: over the same 768 boards the reader now names 47899 squares with none
# of them wrong, so neither side of the floor has an error rate to compare.
# The floor is kept for what it still does, which is refuse an agreement the
# board barely preferred, and it is pinned by the one measurement that is left.
#
# Still a rate and not a promise, and the residue is not the floor's to fix.
# What this module promises is that it never adds a wrong answer of its own.
MIN_PIN = 0.015


# ------------------------------------------------------------------ network

# Node numbering. Fixed rather than allocated, because it is the same graph
# every time and the arithmetic is cheaper than a dictionary.
_SRC, _SINK = 0, 1
_SQUARE = 2                 # 64 of them, row major
_LABEL = 66                 # side * 7 + index into _KINDS
_BUDGET = 80                # side * 2, then 0 pawn budget, 1 side total
_EMPTY_NODE = 84
_NODES = 85

# Bishops are two kinds and not one, because a bishop can never change square
# colour. Counting them apart is what makes a third light squared bishop cost
# a promotion while a second bishop of the other colour costs nothing.
_KINDS = ("K", "Q", "R", "N", "B0", "B1", "P")

# What each side starts a game with, per kind, which is how many of that kind
# it can hold without any pawn having been promoted.
_ARMY = {"K": 1, "Q": 1, "R": 2, "N": 2, "B0": 1, "B1": 1, "P": 8}


def _kind_of(symbol, row, col):
    """The capacity bucket a piece letter falls in on a given square."""
    kind = symbol.upper()
    return "B%d" % ((row + col) % 2) if kind == "B" else kind


def _label(symbol, row, col):
    side = 0 if symbol.isupper() else 1
    return _LABEL + side * 7 + _KINDS.index(_kind_of(symbol, row, col))


class _Flow:
    """Min cost flow by successive shortest paths, on integer costs.

    Forty lines because there is no scipy here and the graph does not need
    more: 85 nodes, and every augmenting path is four or five hops. Dijkstra
    with potentials rather than Bellman-Ford, which is why a square's cost is
    measured downward from the best score on the board instead of upward from
    zero, so that no arc cost is ever negative.
    """

    def __init__(self, nodes):
        self.dest = []
        self.cap = []
        self.cost = []
        self.adj = [[] for _ in range(nodes)]

    def add(self, u, v, cap, cost):
        """Returns the arc id, so the caller can read afterwards whether the
        flow used it."""
        arc = len(self.dest)
        self.adj[u].append(arc)
        self.dest.append(v)
        self.cap.append(cap)
        self.cost.append(cost)
        self.adj[v].append(arc + 1)
        self.dest.append(u)
        self.cap.append(0)
        self.cost.append(-cost)
        return arc

    def run(self, want):
        """Push up to `want` units from _SRC to _SINK. Returns how many got
        through, and the node potentials, which are the dual prices the
        confidences are read off."""
        nodes = len(self.adj)
        pot = [0] * nodes
        sent = 0
        while sent < want:
            dist = [None] * nodes
            dist[_SRC] = 0
            came = [-1] * nodes
            seen = [False] * nodes
            heap = [(0, _SRC)]
            while heap:
                d, u = heapq.heappop(heap)
                if seen[u]:
                    continue
                seen[u] = True
                if u == _SINK:
                    break
                base = d + pot[u]
                for arc in self.adj[u]:
                    if self.cap[arc] <= 0:
                        continue
                    v = self.dest[arc]
                    if seen[v]:
                        continue
                    step = base + self.cost[arc] - pot[v]
                    if dist[v] is None or step < dist[v]:
                        dist[v] = step
                        came[v] = arc
                        heapq.heappush(heap, (step, v))
            if dist[_SINK] is None:
                break
            # A node the search never reached is given the sink's distance, so
            # that every residual arc out of a node that was reached still has
            # a non-negative reduced cost on the next pass.
            for v in range(nodes):
                pot[v] += dist[_SINK] if dist[v] is None else min(dist[v],
                                                                  dist[_SINK])
            push = want - sent
            v = _SINK
            while v != _SRC:
                arc = came[v]
                push = min(push, self.cap[arc])
                v = self.dest[arc ^ 1]
            v = _SINK
            while v != _SRC:
                arc = came[v]
                self.cap[arc] -= push
                self.cap[arc ^ 1] += push
                v = self.dest[arc ^ 1]
            sent += push
        return sent, pot


# ------------------------------------------------------------------- inputs

def _candidates(scores):
    """The names each square may be given, and what the caller scored each at.

    Every name a square could hold gets an arc rather than only the best few.
    Cutting to the best five per square took the solve from 9.4 ms to 5.6 ms
    and is not worth it: the whole point is to let the board overrule a square
    whose top guesses are wrong, and a cut list decides in advance how far
    down the board is allowed to reach.

    A name the caller did not score is offered at zero rather than refused,
    and a square the caller said nothing about gets all of them at zero. Both
    come back unknown once the confidences are worked out, which is better
    than a square with nowhere to go, and it leaves the hard rules somewhere
    to put a king when the caller found none.
    """
    out = {}
    for row in range(8):
        for col in range(8):
            # A pawn on the top or the bottom row is not a position, it is a
            # misread, so the name is not offered there at all.
            back = row in (0, 7)
            here = scores.get((row, col)) or {}
            out[(row, col)] = [(symbol, float(here.get(symbol, 0.0)))
                               for symbol in ORDER + EMPTY
                               if not (back and symbol in "Pp")]
    return out


def _count(prior):
    """Per side, how many of each kind a grid holds, and how many squares of
    it went unread. A symbol outside the twelve letters, "." and "?" makes it
    not a grid at all, which is None rather than a crash."""
    if len(prior) != 8 or any(len(row) != 8 for row in prior):
        return None
    counts = [{kind: 0 for kind in _KINDS}, {kind: 0 for kind in _KINDS}]
    unread = 0
    for row in range(8):
        for col in range(8):
            symbol = prior[row][col]
            # A set rather than "in ORDER", which would take the empty string,
            # and rather than a length check, which would not take None. This
            # runs on whatever the caller passed and must not raise on it.
            if symbol not in _KNOWN:
                return None
            if symbol == UNKNOWN:
                unread += 1
            elif symbol != EMPTY:
                counts[0 if symbol.isupper() else 1][_kind_of(symbol, row,
                                                              col)] += 1
    return counts, unread


def _is_position(prior):
    """Whether a previous position is a position at all.

    This has to be asked before its counts are used as ceilings, and it is the
    one check the rest of the module cannot make for itself: a previous
    position can never contradict ceilings taken from its own counts, so
    without this a board holding four queens and eight pawns is handed back
    unchanged and confident, while the same board read cold is refused. One
    misreading would confirm itself.

    The rules are the cold ones, run over the squares that were read. An
    unread square could hold anything, so the men that are there have to fit
    on their own, and nothing has to be complete: only a fully read prior is
    made to show both kings.
    """
    counted = _count(prior)
    if counted is None:
        return False
    counts, unread = counted
    for row in (0, 7):
        if any(symbol in "Pp" for symbol in prior[row]):
            return False
    for side in counts:
        if side["K"] > 1 or (unread == 0 and side["K"] != 1):
            return False
        promoted = sum(max(0, side[kind] - _ARMY[kind])
                       for kind in ("Q", "R", "N", "B0", "B1"))
        if side["P"] + promoted > _ARMY["P"] or sum(side.values()) > 16:
            return False
    return True


def _caps(prior):
    """Per side, how many of each kind the position is allowed to hold.

    Cold that is the opening army. With a previous position it is what that
    position held, because no move has ever put a man on the board that was
    not already on it. A promotion is not an exception: it turns a pawn into a
    queen and spends the pawn, so it goes through the pawn budget instead of
    adding to any count.

    A prior with a "?" in it is counted cold. Its totals would be short by
    however many squares went unread, and a total that is too small silently
    forbids pieces that are really there. Callers hand this only priors that
    have already passed _is_position, which is what makes the counts safe to
    copy rather than something to clamp afterwards.
    """
    if prior is None or any(UNKNOWN in row for row in prior):
        return [dict(_ARMY), dict(_ARMY)]
    return _count(prior)[0]


def _pinned(prior, moved):
    """The squares the move could not have changed, and what is on them.

    `moved` is every square the move touched, which is four for a castle and
    three for an en passant capture. The rest of the board is what it was, so
    it is carried over rather than read again. moved=None means the move is
    not known and nothing is carried over; moved=() means the move touched
    nothing, so the whole position is.
    """
    if prior is None or moved is None:
        return {}
    moved = set(moved)
    return {(row, col): prior[row][col]
            for row in range(8) for col in range(8)
            if (row, col) not in moved and prior[row][col] != UNKNOWN}


# ------------------------------------------------------------------ solving

class Reading:
    """What the solver made of a board.

    rows        8 lists of 8, each a piece letter, "." for empty, or "?".
                Only ever a square the caller already read the same way, or
                one carried over from the caller's previous position, so a
                letter here is a confirmation and never a fresh answer
    confidence  8 lists of 8 floats, how far clear the board preferred the
                name it gave over the next one, capped at 1. Measured on
                every square, the ones `rows` refuses included, since it
                describes the board rather than the answer. The name it
                belongs to is deliberately not handed back beside it: that
                name is the filler this module exists to withhold
    total       the summed score of the names chosen, for comparing one whole
                board against another
    fallback    None when the counting rules were applied, otherwise why they
                were not
    """

    def __init__(self, rows, confidence, total, fallback=None):
        self.rows = rows
        self.confidence = confidence
        self.total = total
        self.fallback = fallback


def solve(scores, read, prior=None, moved=None, floor=MIN_PIN):
    """Which of the squares the caller read the whole board agrees with.

    `scores` maps (row, col) to {piece letter or ".": score}, row 0 at the top
    of the screen. A name a square does not mention scores zero rather than
    being forbidden, so that "one king a side" always has somewhere to put a
    king; a square left out entirely scores every name zero and comes back
    unknown.

    `read` is the caller's own reading of the same board, 8 rows of 8, in the
    same screen order and the same alphabet, "?" where it could not say. It is
    required, and requiring it is what stops this being used as a filler: a
    square is named back only where `read` named it and the whole board came
    to the same name, so the answer is always a subset of what the caller
    already had. A `read` that is not a grid confirms nothing rather than
    raising, and says so in `fallback`, which is the fail safe direction and
    what a `prior` that is not a position gets too.

    `prior` and `moved` are the position before the move and the squares the
    move touched, both optional. Given them, the squares the move did not
    touch are carried over instead of read, and no side may hold more men, or
    more of any one kind, than it held before. A carried square is named even
    where `read` said "?", and it is the one place that happens: what carries
    it is the caller's own accepted position from a move ago rather than an
    inference drawn from these scores. So it is exactly as good as `moved` is
    complete, four squares for a castle and three for an en passant capture.
    """
    names = _candidates(scores)
    note = None
    if _count(read) is None:
        # Checked the way a previous position is, and for the same reason: it
        # arrives from the same reader and must not raise here either. Nothing
        # to compare against means nothing confirmed rather than everything.
        note = "the reading handed in is not a grid, nothing is confirmed"
        read = [[UNKNOWN] * 8 for _ in range(8)]
    if prior is not None and not _is_position(prior):
        # It goes whole, its ceilings with its squares, because the same
        # misreading produced both. Keeping the ceilings would be worse than
        # useless: they are the only thing that could have caught the squares.
        note = note or "the previous position is not a position, read cold"
        prior = None

    caps = _caps(prior)
    pins = _pinned(prior, moved)
    got = _assign(names, caps, pins)
    if got is None and prior is not None:
        # Unreachable while _is_position holds, since a prior that fits the
        # cold rules always fits ceilings taken from itself. Kept as the net
        # under that argument, because reading cold beats the last resort
        # below, which throws the counting rules away entirely.
        note = note or ("the previous position does not fit these scores,"
                        " read cold")
        pins = {}
        got = _assign(names, _caps(None), pins)
    if got is None:
        # Reachable only if the caller pinned nothing and the scores still
        # leave no way to fill 64 squares, which the always available empty
        # name should prevent. Say what pieces.py would have said rather than
        # return nothing, and put that through the same agreement: a fallback
        # is the last place to start handing out names of its own.
        why = "no assignment satisfies the counting rules"
        lone = _ungoverned(names, floor, why)
        return Reading(_agreed(lone.rows, lone.confidence, read, {}, floor),
                       lone.confidence, lone.total, why)

    raw, confidence, total = got
    # Against the assignment rather than against the answer, because a board
    # that is all "?" is a board nobody was sure of, not a board with no king
    # on it. This only fires when no square was allowed to be a king at all,
    # which takes a caller that pinned every square through `prior`.
    for letter, who in (("K", "white king"), ("k", "black king")):
        if not any(letter in row for row in raw):
            note = note or "no square could be called a %s" % who
    return Reading(_agreed(raw, confidence, read, pins, floor),
                   confidence, total, note)


def _agreed(raw, confidence, read, pins, floor):
    """The names this module is allowed to hand back.

    A square is named where the caller read it as that name and the board is
    at least `floor` sure of it, or where it was carried over from the
    previous position. Everything else is "?", a square the flow was certain
    of and the caller could not read at all included: certainty there is the
    counting rules having nowhere else to put a name, which is not evidence
    about what is on the screen.

    Disagreement comes back "?" rather than as the caller's own letter. Two
    readings of one square that do not match is a reason to look again, and
    handing the letter back would leave this module with no way to say so.
    """
    rows = [[UNKNOWN] * 8 for _ in range(8)]
    for row in range(8):
        for col in range(8):
            name = raw[row][col]
            if name == UNKNOWN:
                continue
            if (row, col) in pins or (read[row][col] == name
                                      and confidence[row][col] >= floor):
                rows[row][col] = name
    return rows


def _assign(names, caps, pins):
    """One solve. Returns (assignment, confidence, total), or None when 64
    squares cannot all be filled under these capacities.

    The assignment names every square, because the flow has to put sixty four
    names somewhere. It is what the caller's own reading is checked against
    and is never handed back on its own: see _agreed.
    """
    net = _Flow(_NODES)
    top = max(1.0, max(score for options in names.values()
                       for _, score in options))
    arcs = {}
    allowed = {}
    for (row, col), options in names.items():
        square = _SQUARE + row * 8 + col
        net.add(_SRC, square, 1, 0)
        if (row, col) in pins:
            # Narrowed rather than replaced, so that a previous position
            # claiming something the square cannot hold, a pawn on the back
            # row, leaves the square with nowhere to go and the whole carry
            # over is dropped instead of the rule being bypassed.
            options = [pair for pair in options if pair[0] == pins[(row, col)]]
        allowed[(row, col)] = options
        for symbol, score in options:
            node = _EMPTY_NODE if symbol == EMPTY else _label(symbol, row, col)
            push = 0 if symbol in "Kk" else KING_PUSH
            cost = int(round((top - score) * SCALE)) + push
            arcs[(row, col, symbol)] = net.add(square, node, 1, cost)

    net.add(_EMPTY_NODE, _SINK, 64, 0)
    for side in (0, 1):
        cap = caps[side]
        budget = _BUDGET + side * 2
        held = budget + 1
        pawns = cap["P"]
        net.add(_LABEL + side * 7, held, cap["K"], 0)
        net.add(_LABEL + side * 7 + 6, budget, pawns, 0)
        for kind in ("Q", "R", "N", "B0", "B1"):
            node = _LABEL + side * 7 + _KINDS.index(kind)
            net.add(node, held, cap[kind], 0)
            # Anything past what the side started with had to be promoted, and
            # a promotion spends a pawn, so it is charged the pawn budget.
            net.add(node, budget, pawns, 0)
        net.add(budget, held, pawns, 0)
        # Implied by the arcs above rather than binding on its own, but the
        # sixteen man limit is a rule in its own right and is cheaper to state
        # here than to re-derive from them.
        net.add(held, _SINK, sum(cap.values()), 0)

    placed, pot = net.run(64)
    if placed < 64:
        return None

    back = _reach(net, pot)
    raw = [[UNKNOWN] * 8 for _ in range(8)]
    confidence = [[0.0] * 8 for _ in range(8)]
    total = 0.0
    for (row, col), options in allowed.items():
        square = _SQUARE + row * 8 + col
        chosen, picked = None, 0.0
        for symbol, score in options:
            if net.cap[arcs[(row, col, symbol)]] == 0:
                chosen, picked = symbol, score
                break
        total += picked

        # How much worse the whole board gets if this square is called
        # something else. Giving it another name means taking that name from
        # wherever it is now, and giving whatever that square becomes to
        # somewhere else again, which is a cycle through what the flow left
        # behind. The cheapest such cycle is the arc's own reduced cost plus
        # the distance back from the name to the square, and it is exact
        # rather than a guess, because a cheaper board than this one would
        # have been a negative cycle and the flow would already have taken it.
        # A carried over square has no other name to be given, so it comes out
        # certain, which is what carrying it forward meant.
        margin = None
        for symbol, score in options:
            if symbol == chosen:
                continue
            node = _EMPTY_NODE if symbol == EMPTY else _label(symbol, row, col)
            home = back[node][square]
            if home is None:
                continue        # the name cannot be moved here at any price
            push = 0 if symbol in "Kk" else KING_PUSH
            swap = (int(round((top - score) * SCALE)) + push
                    + pot[square] - pot[node] + home)
            if margin is None or swap < margin:
                margin = swap
        gap = 1.0 if margin is None else max(0.0, margin / SCALE)
        confidence[row][col] = min(1.0, gap)
        raw[row][col] = chosen
    return raw, confidence, total


def _reach(net, pot):
    """From each piece name, the reduced cost of getting back to every square,
    over what is left of the network once the flow has run.

    Fifteen searches, one per name, rather than one per square, which is what
    makes exact per square confidence affordable. Reduced costs are used so
    that the distances are non-negative and Dijkstra applies; around a cycle
    the potentials cancel, so the number that comes out is the real cost.
    """
    nodes = len(net.adj)
    out = {}
    for start in list(range(_LABEL, _LABEL + 14)) + [_EMPTY_NODE]:
        dist = [None] * nodes
        dist[start] = 0
        seen = [False] * nodes
        heap = [(0, start)]
        while heap:
            d, u = heapq.heappop(heap)
            if seen[u]:
                continue
            seen[u] = True
            for arc in net.adj[u]:
                if net.cap[arc] <= 0:
                    continue
                v = net.dest[arc]
                if seen[v]:
                    continue
                step = d + net.cost[arc] + pot[u] - pot[v]
                if dist[v] is None or step < dist[v]:
                    dist[v] = step
                    heapq.heappush(heap, (step, v))
        out[start] = dist
    return out


def _ungoverned(names, floor, why):
    """What pieces.py would have said: every square on its own, named only if
    its best template clears the floor over the runner up."""
    rows = [[UNKNOWN] * 8 for _ in range(8)]
    confidence = [[0.0] * 8 for _ in range(8)]
    total = 0.0
    for (row, col), options in names.items():
        ranked = sorted(options, key=lambda pair: -pair[1])
        gap = ranked[0][1] - (ranked[1][1] if len(ranked) > 1 else 0.0)
        confidence[row][col] = min(1.0, max(0.0, gap))
        total += ranked[0][1]
        if gap >= floor:
            rows[row][col] = ranked[0][0]
    return Reading(rows, confidence, total, why)
