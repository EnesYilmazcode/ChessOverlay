"""Reading a board the capture has damaged.

The reader in the tree is accurate and appropriately cautious on a clean
capture and confidently wrong on a spoiled one. This is about that, and it
started as a fix for blur alone.

WHAT THE WRONG ANSWERS ACTUALLY ARE

On the quick corpus the baseline gets 252 squares wrong and 214 of them are not
misidentifications at all. They are a white piece read as an *empty square*:
116 pawns, 32 rooks, 28 bishops, 15 kings, 12 queens, 11 knights, every one of
them on a 400px board blurred by 1.1 pixels, and not one of them a black piece.
The shape matcher never runs on those squares. `_judge` returns "." with a
score of 1.0, which is full confidence, before a single template is scored.

The chain is exact and it decides the fix. A white piece in these sets is a
light body inside a hairline dark edge. The body is caught by a cutoff anchored
a fixed fraction of the way from the light square colour up to the brightest
ink on the board; the edge by one anchored the same way below the dark square
colour.

  * On msgothic at 400px the brightest ink on the board falls from 255 to 246
    under blur. That is 13 levels above the light square, and `_tables` wants
    14.3 before it will believe there is any light ink at all, so it puts the
    bright cutoff at 255 and the bright layer is switched off for every square
    on the board at once.
  * The dark edge is one screen pixel wide on a 50px square, and a one pixel
    black line smeared by sigma 1.1 bottoms out near 165, nowhere near the dark
    cutoff at 60.

A white piece is then left with no body and no edge and reads as bare board. A
black piece is a solid dark fill, blur cannot erase it, and every one survives.
That is why every wrong answer is one colour.

The other 38 are real confusions and a different problem: 32 black rooks read
as black pawns and 6 white queens read as black ones, all on the flat set at
824, scoring 0.30 to 0.54 with margins of 0.052 to 0.107 against a MIN_MARGIN
of 0.05. Those are squares that should have been refused, not read.

THE SAME DEFECT SOMEWHERE ELSE

Blur is not the biggest source of wrong answers on the full corpus. `contrast`
is: 700 against blur's 539. Measured square by square, all 700 of them happen
with the bright layer switched off board-wide, which is the same terminal state
as the vanishing white pieces above, reached by a different road. 1.25 contrast
clips the light square to 247 and the white fill to 255, `ceil - hi` is 8
against a bar of 17.8, and the layer goes off. 267 of the 350 per side are then
a piece read in the wrong colour, which is what a reader that cannot see light
ink must do, 73 are a piece read as empty, and 10 are shape confusions.

So one defect, two causes: blur smears the light ink out of reach of the
cutoff, contrast clips it into the square colour. Sharpening answers the first
and cannot answer the second, because clipping destroys the picture rather than
spreading it. What both want is for the reader to stop being confident about
what it can no longer see.

WHAT IS DONE ABOUT IT

Four things, each aimed at something that was measured.

1. Measure the blur and sharpen it back. The board carries its own ruler: a
   square boundary is a step edge of known height, since the two square colours
   are already measured, and a step of height h blurred by sigma peaks at a
   gradient of h/(sigma*sqrt(2pi)). Read off the seven internal boundaries and
   taken as a median, that recovers 1.108 against the 1.1 the corpus applies,
   and gives the same answer on every set, size, orientation and position,
   because it measures the board and not the pieces. A matched unsharp mask
   puts the ink extremes back, which un-switches the bright layer, and puts
   enough of the hairline edge back to clear MIN_COVERAGE.

2. Refuse more readily when the picture is soft at all. Sharpening is an
   estimate of what was there, so on a soft board a call that only just clears
   the margin is not evidence. The margin the winner must beat the best other
   piece type by rises with the measured softness.

3. Refuse colour when a whole ink layer has been switched off. The reader
   decides colour on which layer a square's ink is in, so with one of them off
   it is not deciding colour at all, it is reporting which layer survived. In
   that state the runner up is taken over all twelve symbols rather than over
   piece types, so a piece that does not clearly beat its own opposite colour
   comes back "?" instead of coming back the wrong colour.

4. Never let a damaged board call an occupied square empty. This is the safety
   net under all three, and the one part that does not depend on any of them
   having worked. A square both ink layers have gone quiet on is still asked
   the plainest question available: how far apart are the brightest and darkest
   pixel in the middle of it. Over 8192 squares of soft board that is 0 on
   every empty square and at least 88 on every occupied one, 0.00 against 0.86
   as a fraction of board contrast, so the threshold sits in a gap rather than
   inside a distribution.

WHY EACH NUMBER IS WHERE IT IS

The two gates are separate and that is the whole difference between this
helping and hurting. Sharpening anything softer than a sharp render costs 606
right answers on `small`, which is a downsample and an upsample rather than a
convolution: it reads as mildly soft, 0.53 to 0.69, and sharpening it pushes it
away from the templates instead of towards them. Sharpening only above 0.80
leaves `small` alone entirely while catching every blurred board, which sits at
1.09 to 1.12. The margin still rises from 0.50, because `small` does produce
confident wrong answers and refusing them is free.

The sharpening strength is a real optimum and not a slope. Over the quick
corpus 140 percent leaves 60 wrong answers, 150 leaves none, 160 leaves none
and gives up 93 right ones. All 60 survivors at 140 are white pawns read as
empty, the same failure as the baseline's, which is exactly why (4) exists: it
catches them whatever the sharpening did.

Sharpening in colour rather than in grey is worth 38 right answers with that as
the only difference, and costs about 60ms on an 824px board. Rounding to one
channel before amplifying throws away the fraction of a level the amplification
is there to recover.

The blind test asks for two things and not one. A layer being off is not enough
on its own, because it is also the right answer for a board with no light
pieces left on it, and refusing colour there would be refusing a board that
reads perfectly. The second half is that the ink is pressed against the end of
the range, 255 or 0, which is what a clipped capture looks like and an absent
colour does not. Measured over 8192 `plain` and 8192 `dim` squares the test
fires on nothing at all, and those rows come back byte for byte the same as the
baseline's. Its colour margin alone removes 326 of `contrast`'s 700 wrong
answers for 32 right ones; the emptiness test underneath it removes 146 more.

WHAT IT SCORES

  quick corpus, 12288 squares    baseline 89.18 / 2.05 / 8.77
                                 here     91.33 / 0.00 / 8.67

  full corpus, 65536 squares     baseline 86.48 / 2.74 / 10.78
                                 here     87.16 / 1.11 / 11.72

By variant on the full corpus, as right / WRONG / unknown:

  blur       6155/539/1498  ->  6698/  0/1494     +543 right, every wrong gone
  contrast   6272/700/1220  ->  6240/228/1724      -32 right, 67% of wrong gone
  small      6235/ 54/1903  ->  6169/  0/2023      -66 right, every wrong gone
  thin       7510/328/ 354      unchanged
  heavy      6523/144/1525      unchanged
  plain, dim, grey_body          unchanged

The 98 right answers given up are all on `contrast` and `small`, and they are
given up on purpose: 54 wrong answers on `small` cost 66 right ones, and the
colour refusals on `contrast` cost 32. Nothing is given up on blur, which gains
543. It costs about 2% in wall clock over the quick corpus, 44.8s against 44.0s.

Away from the corpus's own single blur strength, over the same boards blurred
by hand: 0.0 gives 99.15/0.00/0.85 and is untouched, 0.6 gives 91.09/0.00/8.91,
1.1 gives 86.87/0.00/13.13, 1.5 gives 78.20/0.00/21.80, 2.0 gives
66.38/0.00/33.62, 2.5 gives 57.37/0.00/42.63 and 3.0 gives 58.96/0.00/41.04.
The baseline over the same seven is 0.00, 0.98, 5.76, 7.23, 13.96, 16.26 and
17.14 percent wrong. No wrong answer at any of them, and the reader degrades
into refusals rather than into guesses, which is the whole point.

On the three axes bench.py does not sweep yet, all four board themes crossed
with the last-move wash and coordinate labels, over 73728 squares: baseline
91.96 / 3.27 / 4.77, here 94.12 / 0.00 / 5.88. The labels rows come back within
two squares of the undecorated ones, which is the emptiness test reading the
middle of a square and the labels being drawn in the corners.

WHAT THIS STILL DOES NOT FIX

`thin` and `heavy` cost the baseline 328 and 144 wrong answers and are untouched
here. They are morphological rather than optical: an opening and a closing move
ink without softening the square boundary, so (1) cannot see them and does not
fire. They want the same answer as the rest of `contrast` does, which is each
square normalised against its own levels rather than against the board's. That
is a different reader and this one composes with it rather than competing:
sharpening happens before any reduction, and the two refusals are about what
the capture has destroyed rather than about how a square is reduced.

Every number in this file is from bench.py, except the hand-blurred sweep and
the theme and decoration sweep, which use bench.py's corpus and scoring with
one axis added.
"""

import math

from PIL import ImageFilter

import pieces as P

# ---------------------------------------------------------------- measuring

_ROOT2PI = math.sqrt(2.0 * math.pi)

# Half-widths of the profile read across each square boundary, narrowest
# first. Three pixels either side is the more precise instrument and it is what
# the rest of this file is tuned against; past about 1.9 pixels of blur the
# step no longer fits inside it, every row fails the height check and the
# measurement comes back as nothing at all. Widening is the fallback for
# exactly that, and it is not free: measured on the same boards at 1.1 pixels
# the wide window reads 1.130 against the narrow one's 1.108 and costs 35 right
# answers, so it is asked second and only when the first has no answer.
#
# What it buys is the whole range past 2 pixels. Left at three alone the reader
# treats a board blurred by 2, 2.5 or 3 pixels as sharp, does nothing to it and
# gets 548, 640 and 702 squares wrong; with the fallback it gets none wrong at
# any of the three.
_HALVES = (3, 7)

# Rows are sampled this far apart down each boundary. Every row would be four
# thousand profile reads on an 824px board for a number that is a median over
# hundreds of samples either way.
_ROW_STEP = 3

# A row only counts as a square boundary when the two ends of the profile
# really are the two square colours. A row where a piece overhangs the boundary
# is a different edge of a different height and would report a different blur.
_STEP_TOL = 0.15


def _sigma_at(grey, step, span, half):
    """The median blur estimate off one profile width, or None."""
    wide, high = grey.size
    tol = span * _STEP_TOL
    width = 2 * half + 1
    got = []
    for k in range(1, 8):
        x = int(round(k * step))
        if x - half < 0 or x + half + 1 > wide:
            continue
        # One strip of bytes per boundary rather than a pixel at a time. The
        # same profile, and the difference between 28ms and 5ms a board.
        strip = grey.crop((x - half, 0, x + half + 1, high)).tobytes()
        for y in range(2, high - 2, _ROW_STEP):
            base = y * width
            drop = abs(strip[base] - strip[base + width - 2])
            if abs(drop - span) > tol:
                continue
            peak = 0
            for i in range(base, base + width - 1):
                d = strip[i + 1] - strip[i]
                if d < 0:
                    d = -d
                if d > peak:
                    peak = d
            if peak:
                got.append(drop / (peak * _ROOT2PI))
    if not got:
        return None
    got.sort()
    return got[len(got) // 2]


def sigma_of(board_img, levels=None):
    """The capture's blur in pixels, measured off the board's own geometry.

    None when the board offers nothing to measure, which is one whose two
    square colours are not far enough apart to tell from each other, or one too
    small to hold a profile, or one so smeared that even the wide window cannot
    find a square boundary in it. The caller then treats it as sharp, which is
    what the reader already does.
    """
    grey = board_img.convert("L")
    step = grey.size[0] / 8.0
    if step < 8:
        return None
    lo, hi = (levels or P._levels(board_img))[:2]
    span = hi - lo
    if span < 24:
        return None
    for half in _HALVES:
        got = _sigma_at(grey, step, span, half)
        if got is not None:
            return got
    return None


def blind(levels):
    """True when a whole ink layer has been switched off by a spoiled capture.

    Two things, not one. That `_tables` is about to select nothing for a layer
    is the first, and on its own it is also the right answer for a board with
    no light pieces or no dark ones left on it. The second is that the ink is
    pressed against the end of the range, which is what a capture whose levels
    have been clipped looks like and what a board missing a colour does not.
    """
    lo, hi, floor, ceil = levels
    bar = (hi - lo) * P.INK_F
    return (ceil - hi < bar and ceil >= 254) or (lo - floor < bar and floor <= 1)


# ---------------------------------------------------------------- sharpening

# Below this a capture is not sharpened. Over every set, size, orientation and
# position in the corpus a blurred board reads 1.086 to 1.117, `small` reads
# 0.528 to 0.667 and an untouched render reads 0.399 to 0.452, so the gate sits
# in the gap between the second and the third and not inside a distribution.
#
# It is deliberately above `small`. Sharpening a picture that lost its detail
# to resampling rather than to a convolution pushes it away from the templates:
# with the gate at 0.50 instead, `small` gives up 606 right answers.
SHARPEN_AT = 0.80

# The sigma at which the full push is applied, the push scaling in between so a
# picture that is barely soft is barely touched.
FULL_AT = 1.10

# How hard to push, as a percentage. Unsharp adds this much of the difference
# between the picture and a blurred copy of it, so 100 subtracts one pass of
# the blur and over 100 is what starts to invert it.
PERCENT = 150

# Past this the estimate is not a radius worth using: a board that soft has
# lost the boundary it was measured from as well as the pieces.
MAX_SIGMA = 4.0

# Not worth a convolution.
MIN_PERCENT = 5


def _push(sigma):
    """How hard to sharpen a capture measured this soft, as a percentage."""
    if sigma is None or sigma <= SHARPEN_AT or sigma > MAX_SIGMA:
        return 0
    reach = min(1.0, (sigma - SHARPEN_AT) / max(1e-6, FULL_AT - SHARPEN_AT))
    return int(round(PERCENT * reach))


def unblur(board_img, sigma):
    """The capture with a matched unsharp mask on it, or as it came.

    In colour. Converting to grey first rounds every pixel to one level before
    the amplification, and that is worth 38 right answers over the quick
    corpus.
    """
    percent = _push(sigma)
    if percent < MIN_PERCENT:
        return board_img
    return board_img.filter(ImageFilter.UnsharpMask(sigma, percent, 0))


# ---------------------------------------------------------------- judging

# Where caution starts, which is well below where sharpening starts. A picture
# too mildly soft to be worth sharpening is still soft enough to be worth not
# guessing on.
CAREFUL_AT = 0.50

# And where it is fully applied. Sooner than FULL_AT on purpose: the confident
# wrong answers `small` produces sit at 0.53 to 0.69, so a ramp that only
# reached full at 1.10 would never reach them. It costs 39 right answers over
# `small` and `blur` and removes the last wrong one.
CAREFUL_FULL_AT = 0.75

# The margin a call has to clear once caution is fully applied, against
# MIN_MARGIN's 0.05 otherwise. It is what turns the 38 real confusions into
# refusals: their margins run from 0.052 to 0.107.
#
# The same number is used for the colour margin on a blind board, where it
# removes 326 of the 700 `contrast` wrong answers for 32 right ones. 0.25 there
# would remove 506 for 190, which is a worse rate for a number that would then
# be tuned on one variant.
SOFT_MARGIN = 0.16


def _margin(sigma):
    """How far clear the winner has to be, given how soft the capture is."""
    if sigma is None or sigma <= CAREFUL_AT:
        return P.MIN_MARGIN
    reach = min(1.0, (sigma - CAREFUL_AT) / max(1e-6, CAREFUL_FULL_AT - CAREFUL_AT))
    return P.MIN_MARGIN + (SOFT_MARGIN - P.MIN_MARGIN) * reach


def _judge(feat, templates, floor, margin, colour_blind=False):
    """`pieces._judge` with the margin as an argument and one extra refusal.

    The same three tests in the same order and with the same meaning: a floor
    under the winning score, a margin over the runner up, and the ink share
    having to agree with the shape.

    Two things move. The margin, upwards, when the capture is soft. And who
    counts as the runner up: normally the best score belonging to a different
    piece *type*, because a white knight beating a black one is one shape
    scored twice and not two things to be uncertain between. That reasoning
    holds only while something else decides colour, and on a blind board
    nothing does, so there the colour twin is a rival like any other.
    """
    if feat is None or feat.coverage < P.MIN_COVERAGE:
        return ".", 1.0
    ranked = P.ranking(feat, templates)
    score, best = ranked[0]
    if colour_blind:
        runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    else:
        runner_up = next((s for s, symbol in ranked[1:]
                          if symbol.lower() != best.lower()), 0.0)
    if score < floor or score - runner_up < margin:
        return None, score
    flip = templates.get(best.swapcase())
    if flip is not None and (P._nearest(feat, templates[best])
                             > P._nearest(feat, flip)):
        return None, score
    return best, score


# ---------------------------------------------------------------- occupancy

# How much of the square the emptiness test looks at. The middle only: a
# square's own boundary is a step between two board colours, blur widens it,
# and the outer band would then read as contrast on a square with nothing on
# it. Every set here draws its pieces inside 0.78 of the square, and the
# coordinate labels a real board draws sit in the corners, outside this.
INSIDE = 0.62

# And how far apart the brightest and the darkest pixel in there have to be, as
# a fraction of the gap between the two square colours, before a square both
# ink layers went quiet on is refused rather than called empty.
#
# On damaged boards an empty square reaches 0.00 and an occupied one at least
# 0.86. The threshold is put an order of magnitude under the occupied floor
# rather than midway, because the two mistakes are not the same size: too low
# costs an unknown on an empty square, which the caller retries a second later,
# and too high puts back the confident wrong answer this exists to remove.
OCCUPIED_F = 0.10


def holds_something(square_img, span):
    """Is anything at all drawn in the middle of this square?

    The plainest question available, and deliberately so. It is only ever asked
    of squares the layered reduction has already given up on, and its answer is
    only ever used to refuse: it never names a piece and never overrules one.
    """
    wide, high = square_img.size
    dx, dy = int(wide * (1 - INSIDE) / 2), int(high * (1 - INSIDE) / 2)
    mid = square_img.crop((dx, dy, wide - dx, high - dy)).convert("L")
    low, top = mid.getextrema()
    return (top - low) >= span * OCCUPIED_F


# ---------------------------------------------------------------- the reader

class BlurReader:
    """The reader in the tree, shown a sharpened picture, asked harder
    questions when the picture needed sharpening, refused a colour it cannot
    see, and stopped from calling a square empty when something is plainly
    drawn on it.

    It holds a PieceReader rather than subclassing one. Nothing inside that
    class is changed: only what is handed to it, how much room a call has to
    clear, and what happens to a square it declines to look at.
    """

    def __init__(self, sheet=P.TEMPLATE_SHEET, cache_dir=P.CACHE_DIR):
        self.base = P.PieceReader(sheet, cache_dir)

    @property
    def templates(self):
        return self.base.templates

    @property
    def ready(self):
        return self.base.ready

    @property
    def source(self):
        return self.base.source

    def learn(self, board_img, board, flipped=False):
        """Learn from a picture of the same acuity boards are read at.

        A learning frame can be soft too, and templates cut from one describe a
        piece that has already lost its edge. Measuring and undoing it here as
        well is what keeps both sides of every later comparison the same kind
        of picture.
        """
        return self.base.learn(unblur(board_img, sigma_of(board_img)), board,
                               flipped)

    def observe(self, board_img, board, flipped=False):
        return self.base.observe(unblur(board_img, sigma_of(board_img)), board,
                                 flipped)

    def relearn(self, board_img, board, flipped=False):
        return self.base.relearn(unblur(board_img, sigma_of(board_img)), board,
                                 flipped)

    def restore(self, board_img):
        return self.base.restore(unblur(board_img, sigma_of(board_img)))

    def stale(self, board_size):
        return self.base.stale(board_size)

    def classify(self, board_img, believed=None):
        """Read the whole board. Eight rows of piece letters, "." and "?", and
        the weakest score, exactly as PieceReader.classify returns them.

        The sigma is measured on the picture as it arrived, since that is the
        capture being described. Everything after works on the sharpened copy,
        and the levels are measured again on that copy rather than carried
        over, because putting the ink extremes back is most of what sharpening
        is for and the cutoffs are anchored on them.
        """
        rows = [["."] * 8 for _ in range(8)]
        if not self.base.ready:
            return rows, 0.0

        raw_levels = P._levels(board_img)
        sigma = sigma_of(board_img, raw_levels)
        fixed = unblur(board_img, sigma)
        levels = raw_levels if fixed is board_img else P._levels(fixed)
        margin = _margin(sigma)
        colour_blind = blind(levels)
        span = max(1, levels[1] - levels[0])
        # Whether to second-guess an empty square at all. On an undamaged
        # capture the layers are the better witness and this would be
        # second-guessing a reading that works.
        damaged = colour_blind or (sigma is not None and sigma > CAREFUL_AT)
        if colour_blind:
            margin = max(margin, SOFT_MARGIN)

        feats = P._board_features(fixed, levels)
        floor = self.base._floor(feats, levels, fixed.size[0] / 8.0)
        weakest = 1.0
        for (r, c, square), feat in zip(P.squares(fixed), feats):
            symbol, score = _judge(feat, self.base.templates, floor, margin,
                                   colour_blind)
            if symbol == ".":
                if not damaged or not holds_something(square, span):
                    continue
                symbol = None
            if symbol is None:
                held = believed[r][c] if believed else "."
                rows[r][c] = (held if P._confirms(feat, self.base.templates, held)
                              else "?")
                # Zero either way. A confirmed square was refused by the
                # scores, so a caller reading the weakest score to decide how
                # far to trust the board must not be told this one scored well.
                weakest = 0.0
            else:
                rows[r][c] = symbol
                weakest = min(weakest, score)
        return rows, weakest


def reader():
    """The factory bench.py calls."""
    return BlurReader()
