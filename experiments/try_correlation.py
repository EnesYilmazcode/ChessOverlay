"""Telling the pieces apart by greyscale correlation instead of by mask overlap.

The reader in the tree cuts a square into bright ink and dark ink at levels it
picks per board, then scores a candidate by how much of one mask lands on the
other. Everything it does to survive a piece set that draws its white body grey,
or a capture that came out dim, is a hand-built defence of that cut.

Normalised cross-correlation gets the same defence for free. Subtract the
square's own mean, divide by its own spread, and any picture of the same thing
under `a*x + b` scores identically. There is no cut and so nothing to defend.

    zncc(v, t) = <v - mean(v), t - mean(t)> / (||v - mean(v)|| * ||t - mean(t)||)

Two things make it cheap. Store each template already zero-meaned and scaled to
unit length and the numerator collapses to one raw dot product against the
crop: because sum(t) is zero, subtracting the crop's own mean changes nothing.
The crop's own norm is then a single positive number common to every template,
so it divides out of the argmax entirely and is computed once, at the end, to
turn the winning dot product back into a real correlation for the refusal test.

Refusal is the point of the exercise, so it is two tests rather than one. A
square is left unread when the best correlation is weak in absolute terms
(nothing here looks like any piece), and when the best and the runner-up are
too close (something is here and there is no saying which). Empty squares reach
neither test: they are flat, flatness is measured before anything is
correlated, and a flat crop has no spread to divide by.

Three things had to be added before that argument survived contact with the
bench, and each of them was measured rather than assumed.

The emptiness test is where every wrong answer came from. It is two-sided and
the sides cost differently: set it too high and a white piece whose outline a
heavy set has swallowed reads as an empty square, which is a wrong answer in a
permanent record, and set it too low and a blurred empty square gets correlated
against twelve pieces and refused, which is a retry a second later. Moving it
from 8 to 3 turned 0.38% wrong into 0.02% wrong at no cost in refusals.

A set drawn with a heavier or lighter outline than the one that was learned is
not an intensity change, so correlation is not free of it. The bank carries a
thinned and a thickened copy of every learned piece, made by morphology at a
stated square size. That took 0.38% wrong and 0.92% unread down to 0.02% and
0.29%.

The corners of a square are where a board draws its rank and file coordinates,
and are the one part of a square a piece never reaches, so they are dropped
from the template and the crop alike. Free on the bench corpus and the whole
of the defence against coordinates.

Entrant for bench.py:

    import bench, try_correlation
    bench.score(try_correlation.entrant, quick=True)
"""

import math
import operator

from PIL import Image, ImageChops, ImageFilter

import watcher as W

# The square is cut and reduced to RES x RES greyscale before anything is
# compared. Wide plateau: 12 through 20 land within a tenth of a point of each
# other on the full sweep, and it falls away above 24.
RES = 16

# Area-averaging, not point sampling. Every source pixel has to reach the
# reduced square or a one-pixel outline drops out of half the board sizes.
RESAMPLE = Image.BOX

# How far inside its square the crop sits, as a fraction of the square. A
# blurred neighbour bleeds over the boundary and a board draws gridlines on it;
# neither belongs to this piece.
INSET = 0.12

# A crop whose greyscale spread is under this is empty. Two-sided, and the two
# sides cost differently: too low and a blurred empty square gets correlated
# against twelve pieces and refused, which is a retry a second later; too high
# and a faint piece is called an empty square, which is a wrong answer in a
# permanent game record. Every wrong answer left after the augmentation was
# this test firing on a real piece, so it is set low and the refusals are
# allowed to absorb the difference.
EMPTY_STD = 3.0

# ...and also relative to the most contrasty square on this board, so that a
# capture that came out dim does not drag every piece under the fixed cutoff.
# The board is read as a whole before any square is named, which the reader in
# the tree already does for its own reasons.
EMPTY_REL = 0.06

# Refusals. FLOOR is on the correlation itself, MARGIN on the gap between the
# best piece and the best of any other piece.
FLOOR = 0.55
MARGIN = 0.02

# Extra renderings of each learned piece, so the bank covers a set that draws
# its outline heavier or lighter than the one that was learned from.
AUGMENT = ("thin", "heavy")

# The square size those renderings are made at. A three-pixel morphological
# filter is a different thing on a 35-pixel square than on a 103-pixel one, so
# the augmentation is done at a stated size rather than at whatever size the
# learning board happened to be. 96 was measured as well and changed nothing:
# at 96 pixels a three-pixel filter is half of one cell of a 16x16 reduction,
# so it makes a copy of the template rather than a variation on it. 24 was
# measured and hurt, being coarse enough to blur two pieces into each other.
AUGMENT_PX = (40,)

# How much of the square to look at, as a radius from its centre in units of
# half a square. The corners of a square are where a board draws its rank and
# file coordinates, and are the one part of a square a piece never reaches, so
# they are dropped from the template and from the crop alike. sqrt(2) keeps the
# whole square, 1.0 is the inscribed circle, and this is inside that.
#
# It is worth more than tidiness. Over the full sweep the last wrong answers go
# with the corners: 18 wrong at 0.75, 14 at 0.80, then none at all at 0.85,
# 0.90 and 0.95, back to 6 at 1.15 and 8 with no trimming. 0.90 sits in the
# middle of that flat-zero stretch rather than on the edge of it. The same
# change is the whole of the defence against a board that draws coordinates:
# with them on, trimming at 0.90 reads 99.78% against 98.42% at 1.15.
CORNERS = 0.90

# The cascade. Every piece is ranked on an 8x8 reduction of the square first
# and only the best few are settled at full resolution. Measured at coarse 6
# and 8 and at 2, 3, 4 and 5 kept, and every one of them scored identically to
# no cascade at all, so this is speed bought for nothing.
COARSE = 8
KEEP = 3

# The program paints its own coach arrow on the board it is reading, so some of
# what a square shows is this program's ink and not the board's. overlay.py
# chose a colour that greys into the band read_occupancy ignores, and says so as
# its safety argument, but that is a fact about brightness levels and a matcher
# that does not decide by brightness levels inherits none of it: with the arrow
# drawn this reader went from every board read whole to none of them.
#
# The colours are exact constants this program owns and nothing else draws, so
# they can be recognised rather than tolerated. The overlay window is layered,
# so a drawn pixel on a real screen is alpha of the arrow colour over whatever
# was underneath, which makes the set of pixels the arrow can produce
#
#     { a*A + (1-a)*u : a in [ALPHA, 1], u a colour }
#
# and membership of it collapses to one box per channel. p = a*A + (1-a)*u with
# u in [0, 255] forces a <= p/A and a <= (255-p)/(255-A), and both are upper
# bounds, so the whole test is that every channel satisfies
#
#     ALPHA*A <= p <= 255 - ALPHA*(255 - A)
#
# For the two colours in use that is cyan and violet, tightly: nothing a board
# or a piece set draws comes near either. See ARROW_TOL for the slack.
ARROW = True

# Slack on those boxes, in levels, for a screen that does not hand back exactly
# the colour that was painted. There is a lot of room: over the four board
# themes and both real piece sets, the closest pixel any of them draws to
# either box sits 52 levels outside it, and the bench agrees, being unchanged
# from 0 slack all the way to 64 and only starting to refuse squares at 80.
# Widening it can cost refusals and cannot cost a wrong answer, because a
# masked pixel is one this no longer votes on rather than one it invents.
ARROW_TOL = 8.0

# How much of a square may be under the arrow before the square is refused out
# of hand. On this bench it never fires: the weighted correlation below refuses
# the squares that need refusing by itself, and the shaft never takes more than
# 29% of a square. It is here for a square the arrow covers outright, where
# there is no correlation to be had and the flatness that is left would
# otherwise read as an empty square, and a square with nothing visible at all
# is refused whatever this is set to.
ARROW_REFUSE = 0.75

# What a square with the arrow across it has to clear before it is believed.
# Higher than FLOOR and MARGIN on purpose: the square is being read off less
# evidence than a clear one, so it should be harder to convince. Measured over
# every covered square of the full sweep, the answers that were wrong topped
# out at 0.772 correlation while the right ones ran to 0.962, and demanding
# 0.85 refuses all of the former with headroom, where 0.75 would sit under the
# worst of them and only escape by the margin test. It is not free: it costs
# 215 whole boards of the full sweep, 2616 down to 2401, and that is the right
# way round, because a refused board is read again a second later and a wrong
# one is in the game record for good.
ARROW_FLOOR = 0.85
ARROW_MARGIN = 0.05


# ------------------------------------------------------------------ reduction

def _reduce(img, box, res, resample, soften=0.0):
    """One square of a board as a flat list of greyscale levels.

    Cropping and reducing in one call, with the box in floats, so a board whose
    squares do not land on whole pixels is cut where the squares actually are
    rather than where int() puts them.
    """
    small = img.resize((res, res), resample, box)
    if soften:
        small = small.filter(ImageFilter.GaussianBlur(soften))
    return list(small.getdata())


def _inside(res, radius):
    """Which cells of a res x res reduction are inside the radius. None when
    the radius keeps the whole square, so the fast path stays a plain list."""
    if radius is None or radius >= 1.4143:
        return None
    out = []
    for r in range(res):
        for c in range(res):
            dy = (2.0 * r + 1.0) / res - 1.0
            dx = (2.0 * c + 1.0) / res - 1.0
            if dx * dx + dy * dy <= radius * radius:
                out.append(r * res + c)
    return out


def _boxes(size, inset):
    """The 64 crop rectangles, in floats."""
    step = size / 8.0
    pad = inset * step
    return [(c * step + pad, r * step + pad,
             (c + 1) * step - pad, (r + 1) * step - pad)
            for r in range(8) for c in range(8)]


def _stats(v):
    """(mean, norm of the zero-mean vector). norm / sqrt(n) is the spread."""
    n = len(v)
    mean = sum(v) / n
    return mean, math.sqrt(sum((x - mean) ** 2 for x in v))


def _moments(v, w):
    """(sum of weights, weighted sum of v, weighted variance of v). The third
    is what a covered square's spread is measured on."""
    sw = sum(w)
    if sw <= 1e-9:
        return 0.0, 0.0, 0.0
    swv = sum(map(operator.mul, w, v))
    swv2 = sum(a * b * b for a, b in zip(w, v))
    return sw, swv, swv2 / sw - (swv / sw) ** 2


def _unit(v):
    """The template as the dot product wants it: zero mean, unit length."""
    mean, norm = _stats(v)
    if norm < 1e-9:
        return None
    return [(x - mean) / norm for x in v]


def _cells(img, box, res, resample, soften, idx):
    """A square reduced and then trimmed to the cells inside the radius."""
    v = _reduce(img, box, res, resample, soften)
    return [v[i] for i in idx] if idx else v


def _arrow_paint():
    """The colours the arrow is painted in and how solidly, read off overlay
    rather than copied into here, so a third colour cannot be added there
    without this seeing it. Importing overlay defines constants and functions
    and opens nothing."""
    try:
        import overlay as OV
        names, alpha = (OV.YOURS, OV.THEIRS), OV.ALPHA
    except Exception:                      # overlay is a GUI module; if it will
        names, alpha = ("#00E8FF", "#A64BFF"), 0.85   # not import, fall back
    out = []
    for name in names:
        h = name.lstrip("#")
        out.append(tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)))
    return tuple(out), alpha


ARROW_COLOURS, ARROW_ALPHA = _arrow_paint()


def _arrow_mask(rgb, colours, alpha, tol):
    """255 where a pixel could have been painted by the program's own arrow.

    One lookup table per channel per colour and two whole-image boolean ops,
    all of it inside the imaging library, because this runs on every frame.
    """
    bands = rgb.split()
    found = None
    for colour in colours:
        hit = None
        for band, level in zip(bands, colour):
            lo = alpha * level - tol
            hi = 255 - alpha * (255 - level) + tol
            table = [255 if lo <= v <= hi else 0 for v in range(256)]
            here = band.point(table)
            hit = here if hit is None else ImageChops.multiply(hit, here)
        found = hit if found is None else ImageChops.lighter(found, hit)
    return found


def _weighted(img, op):
    """A copy of one square drawn with a heavier or a lighter outline.

    Both are what a change of piece set does to a piece: a closing swallows a
    hairline dark edge, an opening eats a thin light body. Applied to a square
    that has been scaled to a stated size, so the filter is the same fraction
    of a piece whatever size the learning board was.
    """
    if op == "thin":
        return img.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    return img.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))


class _Bank:
    """One piece's template: the unit vector the plain dot product wants, a
    coarse copy for the cascade, the raw levels and their squares for the
    weighted path, and which shade of square it was drawn on.

    The raw levels are kept because a square with the program's own arrow
    across it has to be correlated over the part of it that is still visible,
    and a template that has already been zero-meaned over the whole square
    cannot be re-centred on a subset of it.
    """

    __slots__ = ("fine", "rough", "raw", "sq", "light")

    def __init__(self, fine, rough, raw, light):
        self.fine = fine
        self.rough = rough
        self.raw = raw
        self.sq = [x * x for x in raw]
        self.light = light


# ------------------------------------------------------------------ the reader

class CorrelationReader:
    """learn() once from a position known to be on screen, classify() after.

    Every knob is a constructor argument because every one was swept; the
    defaults are what the sweep settled on.
    """

    def __init__(self, res=RES, resample=RESAMPLE, inset=INSET,
                 empty_std=EMPTY_STD, empty_rel=EMPTY_REL,
                 floor=FLOOR, margin=MARGIN,
                 soften=0.0, parity="off", synth=True,
                 augment=AUGMENT, augment_px=AUGMENT_PX,
                 corners=CORNERS, offsets=0, coarse=COARSE, keep=KEEP,
                 arrow=ARROW, arrow_tol=ARROW_TOL,
                 arrow_refuse=ARROW_REFUSE, arrow_floor=ARROW_FLOOR,
                 arrow_margin=ARROW_MARGIN):
        self.res = res
        self.resample = resample
        self.inset = inset
        self.empty_std = empty_std
        self.empty_rel = empty_rel
        self.floor = floor
        self.margin = margin
        self.soften = soften
        self.parity = parity          # "off" | "prefer" | "strict"
        self.synth = synth            # rebuild a piece onto the other shade
        self.augment = tuple(augment or ())
        self.augment_px = tuple(augment_px or ())
        self.offsets = offsets        # +/- pixels of crop alignment to try
        self.coarse = coarse          # cascade: shortlist at this resolution
        self.keep = keep              # how many pieces the shortlist holds
        self.arrow = arrow            # subtract the program's own coach arrow
        self.arrow_tol = arrow_tol
        self.arrow_refuse = arrow_refuse
        self.arrow_floor = arrow_floor
        self.arrow_margin = arrow_margin
        self.corners = corners
        self.idx = _inside(res, corners)
        self.cidx = _inside(coarse, corners) if coarse else None
        self.cells = len(self.idx) if self.idx else res * res
        self.templates = {}           # symbol -> [_Bank]
        self.shades = {}              # on-light? -> mean level of that ground
        self.ready = False

    # -------------------------------------------------------------- learning

    def learn(self, board_img, board, flipped=False):
        """Take templates off a labelled board, one per piece per square shade.

        Both shades, because a piece standing on a light square and the same
        piece on a dark one are not related by any change of intensity: the
        body keeps its own level while the ground under it moves, which is the
        one transform correlation is blind to being told about.

        The opening position only ever shows eight of the twelve on both
        shades. A king starts on one colour and a queen on the other, so four
        pieces would be learned on one ground and then met on the other. synth
        fills those in.
        """
        grid = W.grid_of(board, flipped)
        gray = board_img.convert("L")
        size = board_img.size[0]
        full = _boxes(size, 0.0)

        # The two ground levels, off whatever empty squares are going.
        shades = {}
        for r in range(8):
            for c in range(8):
                if grid[r][c] != ".":
                    continue
                light = (r + c) % 2 == 0
                v = _reduce(gray, full[r * 8 + c], 8, self.resample)
                shades.setdefault(light, []).append(sum(v) / len(v))
        self.shades = {k: sum(v) / len(v) for k, v in shades.items()}

        found = {}
        seen = set()
        for r in range(8):
            for c in range(8):
                symbol = grid[r][c]
                light = (r + c) % 2 == 0
                if symbol == "." or (symbol, light) in seen:
                    continue
                seen.add((symbol, light))
                box = full[r * 8 + c]
                crop = gray.crop((int(round(box[0])), int(round(box[1])),
                                  int(round(box[2])), int(round(box[3]))))
                for square, shade in self._renderings(crop, light):
                    for bank in self._banks_of(square, shade):
                        found.setdefault(symbol, []).append(bank)

        if not found:
            return False
        self.templates = found
        self.ready = True
        return len(found) == 12

    def _renderings(self, crop, light):
        """This square as learned, and the same piece moved to the other shade
        of ground if synth is on."""
        out = [(crop, light)]
        if self.synth:
            other = self._transplant(crop, light)
            if other is not None:
                out.append((other, not light))
        return out

    def _banks_of(self, square, shade):
        """One square as several templates: as drawn, and drawn with a heavier
        and a lighter outline at each augmentation size."""
        out = []
        for img in [square] + self._reweighted(square):
            pad = self.inset * img.size[0]
            inner = (pad, pad, img.size[0] - pad, img.size[1] - pad)
            fine = _unit(_cells(img, inner, self.res, self.resample,
                                self.soften, self.idx))
            if fine is None:
                continue
            rough = None
            if self.coarse:
                rough = _unit(_cells(img, inner, self.coarse, self.resample,
                                     self.soften, self.cidx))
            raw = _cells(img, inner, self.res, self.resample, self.soften,
                         self.idx)
            out.append(_Bank(fine, rough, raw, shade))
        return out

    def _reweighted(self, square):
        out = []
        for px in self.augment_px:
            canon = square.resize((px, px), Image.BOX)
            for op in self.augment:
                out.append(_weighted(canon, op))
        return out

    def _transplant(self, crop, light):
        """The same piece, drawn on the other shade of square.

        The board has two flat colours and both have just been measured, so a
        pixel close to the one this piece is standing on is ground and every
        other pixel is the piece. Repaint the ground and the piece is left
        where it is. A bad transplant costs nothing: it is one more candidate
        to be beaten, never a replacement for a template that was really seen.
        """
        here = self.shades.get(light)
        there = self.shades.get(not light)
        if here is None or there is None:
            return None
        tol = max(10.0, abs(here - there) * 0.18)
        level = int(round(there))
        out = Image.new("L", crop.size)
        out.putdata([level if abs(p - here) <= tol else p
                     for p in crop.getdata()])
        return out

    # ---------------------------------------------------------- classifying

    def _candidates(self, light):
        """(symbol, bank) worth scoring for a square of this shade.

        "strict" refuses a template taken on the other shade, "prefer" reaches
        for one only when a piece has nothing on this shade, "off" scores
        everything and lets the correlation sort it out.
        """
        if self.parity == "off":
            for symbol, banks in self.templates.items():
                for bank in banks:
                    yield symbol, bank
            return
        for symbol, banks in self.templates.items():
            same = [b for b in banks if b.light == light]
            if same:
                for bank in same:
                    yield symbol, bank
            elif self.parity == "prefer":
                for bank in banks:
                    yield symbol, bank

    def _best(self, v, norm, light, only=None):
        """Best and runner-up correlation, over distinct pieces.

        The crop's norm is the same for every template, so the ranking is over
        raw dot products and the division happens twice at the end rather than
        once per template.
        """
        top_symbol, top, second = "", -1e18, -1e18
        for symbol, bank in self._candidates(light):
            if only is not None and symbol not in only:
                continue
            dot = sum(map(operator.mul, v, bank.fine))
            if dot > top:
                if top_symbol != symbol:
                    second = top
                top_symbol, top = symbol, dot
            elif dot > second and symbol != top_symbol:
                second = dot
        return top_symbol, top / norm, second / norm

    def _weighted_best(self, wv, w, sw, swv, dv, light, only=None):
        """Best and runner-up correlation over the part of a square the arrow
        has left visible, each cell counting for as much of it as survived.

        This is the same zero-mean normalised correlation, with a weight on
        every term, so a covered cell contributes nothing at all rather than
        contributing something invented:

            num = sum(w.v.t) - sum(w.v).sum(w.t)/sum(w)
            den = sqrt( [sum(w.v.v) - sum(w.v)^2/sum(w)]
                      . [sum(w.t.t) - sum(w.t)^2/sum(w)] )

        Three dot products a template instead of one, and only on the dozen or
        so squares the arrow actually crosses. It matters that nothing is
        filled in: when the shaft takes the part of a piece that said which
        piece it was, the runner-up closes up and the square is refused, which
        is the answer. Filling the hole with the square's own average instead
        manufactured a confident wrong answer on exactly those squares.
        """
        top_symbol, top, second = "", -1e18, -1e18
        for symbol, bank in self._candidates(light):
            if only is not None and symbol not in only:
                continue
            t = bank.raw
            swt = sum(map(operator.mul, w, t))
            swt2 = sum(map(operator.mul, w, bank.sq))
            dt = swt2 - swt * swt / sw
            if dt <= 1e-9:
                continue
            num = sum(map(operator.mul, wv, t)) - swv * swt / sw
            score = num / math.sqrt(dv * dt)
            if score > top:
                if top_symbol != symbol:
                    second = top
                top_symbol, top = symbol, score
            elif score > second and symbol != top_symbol:
                second = score
        return top_symbol, top, second

    def _shortlist(self, gray, box, light):
        """The cascade's first pass: rank every piece on a coarse reduction of
        the square and hand the best few on to the full-resolution pass."""
        v = _cells(gray, box, self.coarse, self.resample, self.soften,
                   self.cidx)
        _, norm = _stats(v)
        if norm < 1e-9:
            return None
        scores = {}
        for symbol, bank in self._candidates(light):
            dot = sum(map(operator.mul, v, bank.rough))
            if dot > scores.get(symbol, -1e18):
                scores[symbol] = dot
        best = sorted(scores, key=lambda s: -scores[s])
        return set(best[:self.keep])

    def _painted(self, board_img, gray):
        """What of this board is the program's own arrow.

        Returns the levels with the arrow blacked out, a matching image of
        which pixels survived, which squares to ask about, and where the first
        two sit on the board. The first two are None when there is no arrow on
        screen, which is most frames, and that path costs six lookup tables and
        a bounding box.
        """
        if not self.arrow:
            return None, None, None, (0, 0)
        mask = _arrow_mask(board_img.convert("RGB"), ARROW_COLOURS,
                           ARROW_ALPHA, self.arrow_tol)
        bbox = mask.getbbox()
        if bbox is None:
            return None, None, None, (0, 0)

        # Only that corner of the board is turned into floats. Two float
        # images the size of the whole board and the pastes into them were the
        # most expensive thing this reader did, and the arrow is never more
        # than a few squares wide. Which squares fall inside that corner is a
        # rectangle test; how much of each of them is actually covered is
        # measured exactly, afterwards, off the weights themselves.
        step = gray.size[0] / 8.0
        x0 = int(math.floor(int(bbox[0] / step) * step))
        y0 = int(math.floor(int(bbox[1] / step) * step))
        x1 = int(math.ceil(min(8, math.ceil(bbox[2] / step)) * step))
        y1 = int(math.ceil(min(8, math.ceil(bbox[3] / step)) * step))
        window = (x0, y0, min(x1, gray.size[0]), min(y1, gray.size[1]))
        touched = [x0 <= (i % 8) * step and (i % 8 + 1) * step <= x1 + 1
                   and y0 <= (i // 8) * step and (i // 8 + 1) * step <= y1 + 1
                   for i in range(64)]
        cut = mask.crop(window)
        seen = Image.new("F", cut.size, 1.0)
        seen.paste(0.0, (0, 0), cut)
        lit = gray.crop(window).convert("F")
        lit.paste(0.0, (0, 0), cut)
        return lit, seen, touched, (x0, y0)

    def _through(self, lit, seen, box):
        """One square as the mean level of each cell's surviving pixels, and
        how much of each cell survived.

        Nothing is filled in. A cell the arrow covers outright comes back with
        a weight of zero and takes no part in anything after this: not the
        emptiness test, not the correlation, not the margin. That is the whole
        point. Returns (levels, weights, fraction of the square lost).
        """
        num = _reduce(lit, box, self.res, self.resample)
        den = _reduce(seen, box, self.res, self.resample)
        if self.idx:
            num = [num[i] for i in self.idx]
            den = [den[i] for i in self.idx]
        total = sum(den)
        if total <= 1e-6:
            return None, None, 1.0
        v = [n / d if d > 1e-6 else 0.0 for n, d in zip(num, den)]
        return v, den, 1.0 - total / len(den)

    def classify(self, board_img):
        """8 rows of piece letters, "." for empty and "?" for unread."""
        if not self.ready:
            return [["?"] * 8 for _ in range(8)]
        rows = [["."] * 8 for _ in range(8)]
        gray = board_img.convert("L")
        boxes = _boxes(board_img.size[0], self.inset)
        root = math.sqrt(self.cells)
        lit, seen, touched, (ox, oy) = self._painted(board_img, gray)

        # Whole board first, because the emptiness cutoff is partly a fraction
        # of the most contrasty square on it and there is no knowing that until
        # all 64 have been looked at.
        cut, weight, lost = [], [], []
        for i, box in enumerate(boxes):
            if lit is None or not touched[i]:
                cut.append(_cells(gray, box, self.res, self.resample,
                                  self.soften, self.idx))
                weight.append(None)
                lost.append(0.0)
            else:
                v, w, gone = self._through(
                    lit, seen, (box[0] - ox, box[1] - oy,
                                box[2] - ox, box[3] - oy))
                if w is not None and gone <= 1e-4:
                    # inside the arrow's bounding box but not actually under
                    # it, so it is an ordinary square and is held to the
                    # ordinary bar
                    cut.append(v)
                    weight.append(None)
                    lost.append(0.0)
                else:
                    cut.append(v)
                    weight.append(w)
                    lost.append(gone)

        # The spread of a covered square is measured over what survived, so a
        # square whose only ink is under the shaft reads as flat rather than as
        # a piece; the coverage test above catches that case first.
        spread = []
        for v, w in zip(cut, weight):
            if v is None:
                spread.append(0.0)
            elif w is None:
                spread.append(_stats(v)[1])
            else:
                # in the same units as the plain path: spread times sqrt(cells)
                spread.append(math.sqrt(max(_moments(v, w)[2], 0.0)) * root)
        empty = max(self.empty_std, self.empty_rel * max(spread) / root) * root

        for r in range(8):
            for c in range(8):
                i = r * 8 + c
                # Our own ink standing on the square. Too much of it and there
                # is not enough board left to answer from; a piece under the
                # shaft has to come back unreadable, never confidently empty.
                if cut[i] is None or lost[i] > self.arrow_refuse:
                    rows[r][c] = "?"
                    continue
                if spread[i] < empty:
                    continue
                light = (r + c) % 2 == 0
                floor, margin = self.floor, self.margin
                if weight[i] is not None:
                    floor, margin = self.arrow_floor, self.arrow_margin
                    w = weight[i]
                    sw, swv, dv = _moments(cut[i], w)
                    if dv <= 1e-9:
                        rows[r][c] = "?"
                        continue
                    wv = [a * b for a, b in zip(w, cut[i])]
                    # No cascade on this path. Profiled: what a covered board
                    # costs is building the float images, not the dot
                    # products, so shortlisting here buys under a millisecond
                    # and it would be paid for in dropped candidates.
                    symbol, best, second = self._weighted_best(
                        wv, w, sw, swv, dv * sw, light)
                else:
                    v, norm = cut[i], spread[i]
                    only = (self._shortlist(gray, boxes[i], light)
                            if self.coarse else None)
                    symbol, best, second = self._best(v, norm, light, only)
                    if self.offsets:
                        symbol, best, second = self._nudge(
                            gray, boxes[i], light, only, symbol, best, second)
                if best < floor or best - second < margin:
                    rows[r][c] = "?"
                else:
                    rows[r][c] = symbol
        return rows

    def _nudge(self, gray, box, light, only, symbol, best, second):
        """Re-cut the square a pixel or two off in each direction and keep the
        alignment that correlates best. A board's squares land on fractional
        pixels and any cut of them is a rounding of somebody's intent."""
        span = self.offsets
        w, h = gray.size
        for dy in range(-span, span + 1):
            for dx in range(-span, span + 1):
                if dx == 0 and dy == 0:
                    continue
                shifted = (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)
                if shifted[0] < 0 or shifted[1] < 0 or shifted[2] > w \
                        or shifted[3] > h:
                    continue
                v = _cells(gray, shifted, self.res, self.resample, self.soften,
                           self.idx)
                _, norm = _stats(v)
                if norm < 1e-9:
                    continue
                s, b, sec = self._best(v, norm, light, only)
                if b > best:
                    symbol, best, second = s, b, sec
        return symbol, best, second


# ------------------------------------------------------------------- factory

def factory(**kw):
    """A callable bench.score can use, with these settings baked in."""
    return lambda: CorrelationReader(**kw)


def entrant():
    """The configuration the sweep settled on."""
    return CorrelationReader()
