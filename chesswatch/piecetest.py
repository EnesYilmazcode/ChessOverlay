"""Headless checks for the piece reader specifically.

selftest.py checks that the two reference screenshots read correctly. This
checks the harder things: that they keep reading correctly when the capture is
imperfect, that a piece set the templates were not drawn from is read rather
than refused wholesale, and that the reader says "?" instead of naming a piece
when it is not sure. Every floor below is a number that was actually measured,
not a target, so a drop here is a real regression and not a moved goalpost. The
one deliberate slack is the speed ceiling at the end, 30 ms against a reading
of 17, so that a slower machine does not read as a slowdown in this code.

Run:  python piecetest.py
"""

import os
import sys
import tempfile
import time

import chess
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

import pieces as P
import watcher as W
from shots import shot

# 1.png is the position after 1.e4 c5 2.d4 e6 on a maximised window. 5.png is a
# small window over a wallpaper, white to mate, with the last-move highlight on
# h7 and the check marker on h4, both of which must not disturb anything. 6.png
# is the starting position on the same chess.com board colours drawn with a
# different, flat piece set: the case the reader used to fail outright.
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


def vanished(reader, cases):
    """How many squares that hold a piece the reader calls empty.

    Counted apart from `wrong` because it is a different failure with a
    different cause: not the matcher choosing badly but the reader answering
    "." before a single template is scored, which check() believes and records.
    """
    out = 0
    for name, board_img in cases:
        rows, _ = reader.classify(board_img)
        for r in range(8):
            for c in range(8):
                if TRUTH[name][r][c] != "." and rows[r][c] == ".":
                    out += 1
    return out


def top_pick(reader, cases):
    """How often the best scoring piece type is the right one, before the
    confidence gate refuses anything. This is the honest measure of whether the
    reader can tell the pieces apart at all; what it names is that filtered."""
    hit = total = 0
    for name, board_img in cases:
        levels = P._levels(board_img)
        feats = P._board_features(board_img, levels)
        for i, feat in enumerate(feats):
            row, col = divmod(i, 8)
            want = TRUTH[name][row][col]
            if want == ".":
                continue
            total += 1
            ranked = P.ranking(feat, reader.templates)
            if ranked and ranked[0][1] == want:
                hit += 1
    return hit, total


def shrunk(boards):
    """The same boards in a smaller browser window. Templates are stored at
    96px, so this is the resampling the reader has to survive."""
    return [(name, b.resize((size, size), Image.LANCZOS))
            for name, b in boards for size in (560, 400, 280, 200)]


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

    This is the case the reader before this one grew a whole apparatus for. It
    measured the blur off the board's own square boundaries, put it back with a
    matched unsharp mask, and widened the margin on whatever was left. Nothing
    normalises per square there, so a white piece whose body and hairline edge
    had both been smeared out of reach of the two cutoffs read as an empty
    square, sixty of the pieces these six captures hold.
    """
    return [(name, b.resize((size, size), Image.LANCZOS)
             .filter(ImageFilter.GaussianBlur(radius)))
            for name, b in boards for size, radius in ((280, 1.5), (400, 2.0))]


def distorted(boards):
    """A screen that is not at the reference brightness, and the blur display
    scaling adds. This used to be the genuinely hard one, because the bright
    and dark cutoffs were absolute and a brightness shift moved what counted as
    a piece pixel at all. Correlation divides each square by its own spread, so
    every one of these is the same picture to it."""
    out = []
    for name, b in boards:
        for f in (0.85, 0.92, 1.08, 1.15):
            out.append((name, ImageEnhance.Brightness(b).enhance(f)))
        for f in (0.80, 0.90, 1.25):
            out.append((name, ImageEnhance.Contrast(b).enhance(f)))
        for radius in (1.0, 1.5, 2.5):
            out.append((name, b.filter(ImageFilter.GaussianBlur(radius))))
    return out


def obstructed(boards):
    """The same boards with a pointer, a popup and a painted-over square.

    Three different things, and the reader owes a different answer to each. A
    cursor takes a corner of a piece and the belief can sometimes put that
    square back. A popup takes several squares outright and nothing can. A
    square painted edge to edge shows no contrast at all, which is what an
    empty square shows, and it must still never read as one.
    """
    import fakeboard as _F
    out = []
    for name, b in boards:
        for row in range(0, 8, 2):
            for col in range(0, 8, 2):
                out.append((name, _F.with_pointer(b, row, col)))
                out.append((name, _F.with_pointer(b, row, col, size=1.8)))
        for row in range(8):
            for col in (1, 3, 5):
                for wide in (1, 2):
                    out.append((name, _F.with_panel(b, row, col, wide)))
                    out.append((name, _F.with_panel(b, row, col, wide,
                                                    dark=False)))
        for row, col in ((0, 4), (3, 2), (6, 1), (4, 4)):
            out.append((name, _F.cover(b, row, col)))
    return out


def with_hints(board_img, at, frac=0.28, alpha=0.16, dark=True):
    """The board with a site's move-hint dot painted on each of `at`.

    Drawn the way chess.com and lichess draw a legal destination: a flat disc
    a third of the square across, translucent, in the middle. Supersampled so
    its edge is antialiased like a real one, which is what puts cells part way
    between the dot and the square and is the part a shape test has to survive.
    """
    size = board_img.size[0]
    step = size / 8.0
    out = board_img.copy()
    ink = (0, 0, 0) if dark else (255, 255, 255)
    for row, col in at:
        box = (int(col * step), int(row * step),
               int((col + 1) * step), int((row + 1) * step))
        wide, high = box[2] - box[0], box[3] - box[1]
        mask = Image.new("L", (wide * 4, high * 4), 0)
        pen = ImageDraw.Draw(mask)
        radius = frac * min(wide, high) * 4 / 2.0
        cx, cy = wide * 2.0, high * 2.0
        pen.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                    fill=int(255 * alpha))
        patch = out.crop(box)
        patch.paste(Image.new("RGB", (wide, high), ink), (0, 0),
                    mask.resize((wide, high), Image.LANCZOS))
        out.paste(patch, box)
    return out


def squares_of(name, held):
    """The squares of one reference board that do or do not hold a piece."""
    return [(row, col) for row in range(8) for col in range(8)
            if (TRUTH[name][row][col] != ".") == held]


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
    print("      down to a 200px window: %d correct, %d wrong, %d unclear" % got)
    # All 512 of them, against the 485 the mask reader named and the 27 it gave
    # up. A small board is where a hairline outline is resampled away, which is
    # most of what a thresholded mask was made of and none of what a correlation
    # is.
    r.append(check("every square down to a 200px window, none wrong",
                   got, (512, 0, 0)))

    got = tally(reader, offset(boards))
    print("      misaligned crops: %d correct, %d wrong, %d unclear" % got)
    r.append(check("a misaligned crop yields nothing wrong at all", got[1], 0))
    # 863, against the 897 the mask reader named. This is the one row that
    # costs: a crop the board width is wrong by eight pixels on samples every
    # square off centre by a growing fraction, and the registered comparison
    # the mask reader had put the piece back in the middle of its own bounding
    # box where a correlation cannot. It is paid for in refusals rather than in
    # wrong pieces, and FLOOR is what buys it: at 0.65 this row names 34 more
    # squares and two of them are a knight read as a bishop.
    r.append(check("  and still names 863 of 1024 squares", got[0] >= 863, True))

    got = tally(reader, distorted(boards))
    print("      wrong brightness or blur: %d correct, %d wrong, %d unclear" % got)
    # Every square of all twenty captures, against 1133 named and 55 WRONG.
    # This row is the whole argument for the change. Brightness and contrast
    # are affine on pixel values, and a correlation of two zero-meaned unit
    # vectors is invariant to that by construction rather than by measurement.
    r.append(check("brightness, contrast and blur cost nothing at all",
                   got, (1280, 0, 0)))

    bad = smeared(boards + six)
    got = tally(reader, bad)
    gone = vanished(reader, bad)
    print("      badly captured: %d correct, %d wrong, %d unclear, %d pieces "
          "read as empty" % (got + (gone,)))
    # 351 correct and none wrong, against 228 correct and none wrong once the
    # mask reader had grown its blur apparatus, and 228 correct with 60 read as
    # empty squares before that.
    r.append(check("a smeared capture reads no piece as an empty square",
                   (gone, got[1]), (0, 0)))
    r.append(check("  and names 351 of the 384 squares", got[0] >= 351, True))

    # And there is no apparatus behind that. The reader measures no blur, puts
    # none back and widens no margin: a board smeared four pixels reads with
    # nothing wrong on it because every square is divided by its own spread.
    heavy = [(name, b.filter(ImageFilter.GaussianBlur(radius)))
             for name, b in boards + six for radius in (2.0, 3.0, 4.0)]
    r.append(check("  nor does one blurred four pixels, with nothing measuring it",
                   tally(reader, heavy)[1], 0))

    # -- what a call has to clear -----------------------------------------
    # On a clean board the closest correct call clears the runner up by a long
    # way, so the margin has to stay well under that or good boards start
    # coming back unclear.
    tight, weakest = 1.0, 1.0
    for name, b in boards:
        levels = P._levels(b)
        for i, feat in enumerate(P._board_features(b, levels)):
            row, col = divmod(i, 8)
            if TRUTH[name][row][col] == ".":
                continue
            ranked = P.ranking(feat, reader.templates)
            best = ranked[0][1]
            runner_up = next((s for s, sym in ranked[1:]
                              if sym.lower() != best.lower()), 0.0)
            tight = min(tight, ranked[0][0] - runner_up)
            weakest = min(weakest, ranked[0][0])
    print("      on a clean board the closest correct call clears by %.3f "
          "and the weakest scores %.3f" % (tight, weakest))
    r.append(check("the margin and the floor both leave clean boards room",
                   (P.MARGIN < tight / 2, P.FLOOR < weakest), (True, True)))

    # -- a piece set the templates were never drawn from ------------------
    got = top_pick(reader, six)
    print("      bundled templates on 6.png: right piece top of the ranking "
          "%d times in %d" % got)
    # 21 in 32, against the 23 the split mask managed. The gate then keeps 14
    # of those, against 8, and none of the 32 comes back wrong either way.
    r.append(check("the bundled set picks the right piece on 6.png 21 times in 32",
                   got[0] >= 21, True))
    got = named(reader, six)
    print("      and names %d, refuses %d, gets %d wrong" % (got[0], got[2], got[1]))
    r.append(check("  names 14 of them and none of them wrongly",
                   (got[0] >= 14, got[1]), (True, 0)))

    # -- learning the set in front of it ----------------------------------
    start = chess.Board()
    own = P.PieceReader(cache_dir=cache.name)
    r.append(check("a foreign set is learnable in one frame",
                   own.learn(six[0][1], start), True))
    got = named(own, six)
    print("      learned from 6.png, reading 6.png: %d named, %d refused, "
          "%d wrong" % (got[0], got[2], got[1]))
    r.append(check("  and then reads all 32 of its own pieces, none wrong",
                   (got[0], got[1]), (32, 0)))

    # A piece is taught on one square colour and met on the other, which is the
    # one difference between two squares that normalising cannot remove: the
    # ground moves and the piece standing on it does not. So a taught piece is
    # repainted onto the other colour, and the tolerance that decides which
    # pixels are ground has to stay under the nearest a piece comes to the
    # square beneath it. chess.com draws a white body at 250 over a light
    # square at 233, seventeen levels or 0.167 of the span; at 0.18, which is
    # where the old ink-share formula put it, the repaint eats that pawn's body
    # and a white pawn moved onto a dark square becomes a rook.
    closest = 99.0
    for name, b in boards:
        lo, hi = P._levels(b)
        span = max(1, hi - lo)
        grey = b.convert("L")
        for i in range(64):
            row, col = divmod(i, 8)
            if TRUTH[name][row][col] == ".":
                continue
            here = hi if (row + col) % 2 == 0 else lo
            box = P._square_box(b.size[0], row, col)
            mid = grey.crop((box[0] + (box[2] - box[0]) // 4,
                             box[1] + (box[3] - box[1]) // 4,
                             box[2] - (box[2] - box[0]) // 4,
                             box[3] - (box[3] - box[1]) // 4))
            low, top = mid.getextrema()
            closest = min(closest, max(abs(top - here), abs(here - low)) / span)
    print("      the nearest a piece comes to the square under it is %.3f "
          "of the span" % closest)
    r.append(check("the transplant tolerance stays under the nearest piece",
                   P.GROUND_TOL_F < closest, True))

    # -- the trust gate ----------------------------------------------------
    from fakeboard import Renderer
    render = Renderer(shot("1"), W.find_board(Image.open(shot("1")).convert("RGB")))
    other = P.PieceReader(cache_dir=cache.name)
    other.learn(render.render(start), start)

    print("      chess.com templates on 6.png:  %d named, %d refused, %d wrong"
          % tuple(named(other, six)[i] for i in (0, 2, 1)))
    print("      6.png templates on 1 and 5:    %d named, %d refused, %d wrong"
          % tuple(named(own, boards)[i] for i in (0, 2, 1)))
    r.append(check("chess.com templates refuse 6.png rather than guess it",
                   named(other, six)[1], 0))
    r.append(check("  and 6.png templates name 18 of them and none wrongly",
                   named(own, boards)[:2], (18, 0)))

    def sig_of(board_img):
        levels = P._levels(board_img)
        return P._signature(P._board_features(board_img, levels), levels,
                            board_img.size[0] / 8.0)

    def apart(a, b):
        parts = [abs(x - y) for x, y in zip(a[:3], b[:3])
                 if x is not None and y is not None]
        return round(max(parts), 3) if parts else None

    r.append(check("a learned set knows which board it came from",
                   [P._trusted(other.signature, sig_of(b)) for _, b in boards]
                   + [P._trusted(own.signature, sig_of(six[0][1]))],
                   [True, True, True]))
    r.append(check("  and distrusts the other set both ways round",
                   [P._trusted(own.signature, sig_of(b)) for _, b in boards]
                   + [P._trusted(other.signature, sig_of(six[0][1]))],
                   [False, False, False]))
    print("      own set %s and %s away, foreign set %s and %s away"
          % (apart(other.signature, sig_of(boards[0][1])),
             apart(own.signature, sig_of(six[0][1])),
             apart(own.signature, sig_of(boards[0][1])),
             apart(other.signature, sig_of(six[0][1]))))

    # What being mistrusted then costs a call. Over every capture of both
    # fixtures scored against the other set's templates, the highest scoring
    # WRONG call a foreign set makes is 0.723 and the highest a trusted one
    # makes is 0.673, so MISTRUST_FLOOR clears the first and FLOOR the second.
    r.append(check("  a mistrusted board is held to a higher floor",
                   P.FLOOR < P.MISTRUST_FLOOR, True))
    lifted = [reader._floor(P._board_features(img, P._levels(img)),
                            P._levels(img), img.size[0] / 8.0)
              for _, img in six]
    r.append(check("  which is the floor 6.png is actually read at",
                   lifted, [P.MISTRUST_FLOOR]))

    # -- an empty square is a different question from what is on one -------
    # The plainest measurement available, and deliberately so: how far apart
    # the brightest and the darkest pixel in the middle of the square are. It
    # names no piece and never overrules one. Pinned as a gap rather than as a
    # threshold, because a threshold inside a distribution is not a test.
    empty_top, held_low = 0.0, 99.0
    for name, img in bad + shrunk(boards) + distorted(boards):
        levels = P._levels(img)
        for i, feat in enumerate(P._board_features(img, levels)):
            row, col = divmod(i, 8)
            if TRUTH[name][row][col] == ".":
                empty_top = max(empty_top, feat.reach)
            else:
                held_low = min(held_low, feat.reach)
    print("      an empty square reaches %.4f, an occupied one %.4f"
          % (empty_top, held_low))
    r.append(check("the emptiness test sits in a gap, not a distribution",
                   (empty_top < P.OCCUPIED_F < held_low,
                    held_low > 5 * empty_top), (True, True)))

    # And the other half of it, which is that flatness alone is not emptiness.
    # A popup painted over a whole square shows no contrast either. What tells
    # the two apart is the colour underneath: an empty square is flat at a
    # colour the board paints squares with and a painted one is not.
    import fakeboard as _F
    covered_ground, empty_ground = 99.0, 0.0
    for name, b in boards:
        for row, col in ((0, 4), (3, 2), (6, 1), (4, 4)):
            img = _F.cover(b, row, col)
            levels = P._levels(img)
            feats = P._board_features(img, levels)
            span = max(1, levels[1] - levels[0])
            here = feats[row * 8 + col]
            covered_ground = min(covered_ground,
                                 max((levels[0] - here.ground) / span,
                                     (here.ground - levels[1]) / span))
            for i, feat in enumerate(feats):
                fr, fc = divmod(i, 8)
                if (fr, fc) == (row, col) or TRUTH[name][fr][fc] != ".":
                    continue
                empty_ground = max(empty_ground,
                                   max(0.0, (levels[0] - feat.ground) / span,
                                       (feat.ground - levels[1]) / span))
    print("      a painted square's colour sits %.3f spans outside the board's "
          "two, an empty one's %.3f" % (covered_ground, empty_ground))
    r.append(check("a square painted over is not read as an empty one",
                   (empty_ground < P.EMPTY_GROUND_F < covered_ground,
                    vanished(reader, [(n, _F.cover(b, 0, 4)) for n, b in boards])),
                   (True, 0)))

    # -- the arrow this program draws on the board it is reading -----------
    # overlay.py keeps the arrow invisible to the occupancy reader by choosing
    # a brightness it ignores. That is a fact about cutoffs and this reader has
    # none, so it recognises the arrow instead and weights it out. What has to
    # hold is the direction: the arrow may cost an answer and may never change
    # one.
    import bench as _bench
    import overlay as _OV
    r.append(check("the arrow colours are read off overlay, not copied",
                   P.ARROW_COLOURS,
                   tuple(tuple(int(name.lstrip("#")[i:i + 2], 16)
                               for i in (0, 2, 4))
                         for name in (_OV.YOURS, _OV.THEIRS))))
    r.append(check("  at the alpha overlay actually paints with",
                   P.ARROW_ALPHA, _OV.ALPHA))

    withdrawn = added = altered = 0
    drawn_on = boards + six + [(n, b.resize((400, 400), Image.LANCZOS))
                               for n, b in boards + six]
    for name, img in drawn_on:
        plain_rows, _ = reader.classify(img)
        for uci in ("e2e4", "g1f3"):
            arrow_rows, _ = reader.classify(_bench._draw_arrow(img, uci))
            for row in range(8):
                for col in range(8):
                    was, now = plain_rows[row][col], arrow_rows[row][col]
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

    # The mutation. A covered cell contributes nothing to the weighted
    # correlation, which is the whole of why the arrow costs refusals rather
    # than answers. Fill the hole in instead, with the mean of what survived,
    # and the score is grading the reconstruction: the same corpus comes back
    # with answers the arrow changed outright. This is the check that the check
    # above can fail.
    def _filled(feat, templates, only=None):
        held = feat.weights
        feat.weights = None
        try:
            return P._plain_scores(feat, templates, only)
        finally:
            feat.weights = held

    real = P._weighted_scores
    P._weighted_scores = _filled
    try:
        mutated = 0
        for name, img in drawn_on:
            plain_rows, _ = reader.classify(img)
            for uci in ("e2e4", "g1f3"):
                arrow_rows, _ = reader.classify(_bench._draw_arrow(img, uci))
                mutated += sum(1 for row in range(8) for col in range(8)
                               if plain_rows[row][col] != arrow_rows[row][col]
                               and "?" not in (plain_rows[row][col],
                                               arrow_rows[row][col]))
    finally:
        P._weighted_scores = real
    print("      filling the hole instead changes %d answers outright" % mutated)
    r.append(check("  and weighting the covered cells out is what does that",
                   mutated > 0, True))

    # -- the marks the SITE draws on the board ------------------------------
    # A dot on every legal destination, a ring on a capture, a mark on a
    # premove. A dot on an empty square is board plus ink, so it is neither a
    # piece nor an empty square and one of them anywhere refused the whole
    # frame, which is what left the board of issue #52 unreadable on a crop
    # that was otherwise perfect. Unlike the arrow above, its colour belongs to
    # the site and cannot be known here, so it is recognised by shape.
    #
    # Two answers are owed and they are not the same. A dot on bare board is a
    # decoration and the square is empty. A dot on a piece is a piece with
    # something drawn on it, and reading that as an empty square would put a
    # piece that is still standing into the game record.
    # Stated against the same board without the dots rather than against a
    # number, so 6.png, which the bundled sheet already refuses eighteen pieces
    # of, is held to what it reads clean and not to perfection.
    DOTS = ((0.20, 0.16, True), (0.28, 0.16, True), (0.36, 0.20, True),
            (0.28, 0.22, False))
    every_size = [(name, b) for name, b in boards + six] + \
                 [(name, b.resize((400, 400), Image.LANCZOS))
                  for name, b in boards + six]
    changed = 0
    for name, b in every_size:
        clean, _ = reader.classify(b)
        for frac, alpha, dark in DOTS:
            rows, _ = reader.classify(
                with_hints(b, squares_of(name, False), frac, alpha, dark))
            changed += sum(1 for row in range(8) for col in range(8)
                           if rows[row][col] != clean[row][col])
    print("      a move hint on every empty square changes %d answers"
          % changed)
    r.append(check("a move-hint dot on an empty square is read through",
                   changed, 0))

    on_pieces = [(name, with_hints(b, squares_of(name, True), frac, alpha, dark))
                 for name, b in every_size for frac, alpha, dark in DOTS]
    got = tally(reader, on_pieces)
    gone = vanished(reader, on_pieces)
    print("      and one on every piece: %d correct, %d wrong, %d unclear, "
          "%d pieces read as empty" % (got + (gone,)))
    r.append(check("  a dot drawn on a piece never deletes it", (got[1], gone),
                   (0, 0)))

    # The safety argument, stated as the gap it rests on rather than as the
    # answers above. MARK_SPAN is a bar between two measured distributions and
    # has to stay in the gap between them: what a real dot reaches on one side,
    # what the smallest piece these fixtures hold reaches on the other. A
    # widened bar fails here before it deletes a piece anywhere else.
    def reaches(feat):
        mid = sorted(feat.cells)[len(feat.cells) // 2]
        bar = P.MARK_INK_F * feat.span
        ink = [i for i, v in enumerate(feat.cells) if abs(v - mid) > bar]
        if not ink:
            return 0.0
        at = [(P._IDX[i] if P._IDX else i) for i in ink]
        rows = [i // P.RES for i in at]
        cols = [i % P.RES for i in at]
        return max(max(rows) - min(rows), max(cols) - min(cols)) + 1.0

    widest_mark, narrowest_piece, looked_like = 0.0, float(P.RES), 0
    for name, img in ([(n, with_hints(b, squares_of(n, False), frac, alpha, dark))
                       for n, b in every_size for frac, alpha, dark in DOTS]
                      + every_size + shrunk(boards) + smeared(boards + six)):
        levels = P._levels(img)
        for i, feat in enumerate(P._board_features(img, levels)):
            row, col = divmod(i, 8)
            if TRUTH[name][row][col] == ".":
                widest_mark = max(widest_mark, reaches(feat))
            else:
                narrowest_piece = min(narrowest_piece, reaches(feat))
                looked_like += feat.marked
    print("      a mark reaches %.3f of the square's grid, the smallest piece "
          "%.3f" % (widest_mark / P.RES, narrowest_piece / P.RES))
    r.append(check("  and MARK_SPAN is a bar in the gap between the two",
                   (widest_mark / P.RES <= P.MARK_SPAN < narrowest_piece / P.RES,
                    looked_like), (True, 0)))

    # -- unclear rather than wrong ----------------------------------------
    name, b = boards[0]
    nudged = b.crop((2, 0, 2 + b.size[0], b.size[0]))
    rows, weakest = reader.classify(nudged)
    r.append(check("nothing a two pixel crop error names is wrong",
                   [rows[row][col] for row in range(8) for col in range(8)
                    if rows[row][col] not in ("?", TRUTH[name][row][col])], []))
    # The "?" is the only thing that refuses it. A wrong letter is stopped
    # nowhere downstream: the same board with a file guessed at is a position
    # the rules allow and would be recorded as one.
    guessed = [list(row) for row in TRUTH[name]]
    unclear = sum(row.count("?") for row in rows)
    if unclear:
        r.append(check("  and a \"?\" is refused where a guess would not be",
                       (W.board_from_grid(rows), weakest,
                        W.board_from_grid(guessed) is not None),
                       (None, 0.0, True)))
    else:
        r.append(check("  and the board it reads is a legal position",
                       W.board_from_grid(rows) is not None, True))

    # -- what the reader refuses, a belief can sometimes put back ----------
    # Choosing between twelve pieces is a question a pointer or a popup spoils:
    # the correlation divides by the square's own spread, which an overlay
    # moves, so all twelve scores sink together and the winner stops beating
    # the runner up. Asking whether ONE named piece is present counts that
    # piece's own ink instead, which nothing on the square can move.
    covered = obstructed(boards)
    refused = put_back = worst = offered = swallowed = 0
    nearest_lie, lowest_yes = 0.0, 1.0
    for name, img in covered:
        want = TRUTH[name]
        levels = P._levels(img)
        feats = P._board_features(img, levels)
        blind, _ = reader.classify(img)
        left = 0
        for i, feat in enumerate(feats):
            row, col = divmod(i, 8)
            if blind[row][col] != "?":
                continue
            refused += 1
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
            for symbol in P.ORDER:
                held, _ = P._contains(feat, reader.templates, symbol)
                if not held:
                    continue
                got = held[symbol]
                rival = max(v for sym, v in held.items() if sym != symbol)
                if symbol != want[row][col]:
                    nearest_lie = max(nearest_lie, got - rival)
                elif got - rival >= P.CONFIRM_MARGIN:
                    lowest_yes = min(lowest_yes, got)
        worst = max(worst, left)
    print("      obstructed: %d captures, %d squares refused, %d put back, "
          "worst %d left" % (len(covered), refused, put_back, worst))
    r.append(check("a belief puts back 4 of the 340 squares an obstruction costs",
                   (refused, put_back), (340, 4)))
    r.append(check("  and no capture is left with more than 2 unreadable",
                   worst, 2))
    print("      %d wrong beliefs offered, the nearest short by %.4f"
          % (offered, P.CONFIRM_MARGIN - nearest_lie))
    r.append(check("a piece the square is not holding is never confirmed",
                   (offered, swallowed), (4080, 0)))
    r.append(check("  the nearest of them reaches 0.0465 of the 0.08",
                   round(nearest_lie, 4), 0.0465))
    # The containment floor refused nothing on any of these. Pinning the gap is
    # what stops the floor being raised into the answers, or dropped as
    # decorative: on the margin alone a square holding almost nothing confirms
    # as long as nothing else is there either.
    r.append(check("the containment floor sits below every confirm it allows",
                   (round(lowest_yes, 4), P.CONFIRM_CONTAIN < lowest_yes),
                   (0.6279, True)))
    # And nothing this corpus reads is wrong, whether it is told the position
    # or not, which is the number that decides whether a game record survives.
    told_wrong = blind_wrong = 0
    for name, img in covered:
        want = [list(row) for row in TRUTH[name]]
        for rows, count in ((reader.classify(img)[0], "blind"),
                            (reader.classify(img, want)[0], "told")):
            bad_here = sum(1 for row in range(8) for col in range(8)
                           if rows[row][col] != "?"
                           and rows[row][col] != want[row][col])
            if count == "told":
                told_wrong += bad_here
            else:
                blind_wrong += bad_here
    r.append(check("  and an obstructed board is never read wrong either way",
                   (blind_wrong, told_wrong), (0, 0)))

    # Colour is what stops a pawn taken by the other side's pawn confirming as
    # still standing, and it is the one place this parts company with _judge:
    # there the colour twin is one shape scored twice, here it is the failure
    # the whole test exists to refuse.
    start_img = render.render(start)
    levels = P._levels(start_img)
    feats = P._board_features(start_img, levels)
    home = W.grid_of(start, False)
    itself = swapped = 0
    for i, feat in enumerate(feats):
        row, col = divmod(i, 8)
        if home[row][col] == ".":
            continue
        itself += P._confirms(feat, reader.templates, home[row][col])
        swapped += P._confirms(feat, reader.templates,
                               home[row][col].swapcase())
    r.append(check("on a clean board 31 of the 32 pieces confirm themselves",
                   (itself, swapped), (31, 0)))

    # And that the reader is wired to ask at all.
    helped, honest = 0, True
    shown = covered[0][1]
    for name, img in covered:
        want = [list(row) for row in TRUTH[name]]
        blind, _ = reader.classify(img)
        told, _ = reader.classify(img, want)
        moved = [(row, col) for row in range(8) for col in range(8)
                 if told[row][col] != blind[row][col]]
        if moved:
            helped += 1
            shown = img
            honest = honest and all(blind[row][col] == "?"
                                    and told[row][col] == want[row][col]
                                    for row, col in moved)
    r.append(check("classify puts the belief back, and only where it refused",
                   (helped, honest), (4, True)))
    blind, _ = reader.classify(shown)
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
    # The four that were only seen on one colour are the four that get repainted
    # onto the other. A piece seen on both is not, because a real rendering is
    # already in hand and a made up one would be a worse copy of it.
    r.append(check("  and only makes up the colours it was never shown",
                   sorted(s for s in P.ORDER
                          for t in reader.templates[s] if t.synth),
                   ["K", "Q", "k", "q"]))

    r.append(check("a set learned at a usable size is never stale",
                   [reader.stale(s) for s in (200, render.size // 2,
                                              render.size, render.size * 2)],
                   [False, False, False, False]))
    small = P.MIN_LEARN_PX - 8
    tiny = P.PieceReader(cache_dir=cache.name)
    tiny.learn(render.render(start).resize((small, small), Image.LANCZOS), start)
    r.append(check("  but one learned under the floor is, once the board grows",
                   (tiny.stale(small * 2), tiny.stale(small), tiny.stale(small // 2)),
                   (True, False, False)))

    # -- learning part of a position --------------------------------------
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
    r.append(check("  and a reader back on the sheet is never stale at any size",
                   [tiny.stale(s) for s in (200, render.size, render.size * 3)],
                   [False, False, False]))

    # -- folding in more frames -------------------------------------------
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
    moved_on = chess.Board()
    moved_on.push_san("e4")
    r.append(check("  and a square whose label has moved on is refused",
                   folding.observe(six[0][1], moved_on) < 32, True))

    # -- the cache ---------------------------------------------------------
    only6 = tempfile.TemporaryDirectory()
    P.PieceReader(cache_dir=only6.name).learn(six[0][1], start)
    fresh = P.PieceReader(cache_dir=only6.name)
    r.append(check("the set learned from 6.png comes back from disk",
                   (fresh.restore(six[0][1]), fresh.source, fresh.learned_size),
                   (True, "learned in an earlier game", six[0][1].size[0])))
    # The square each piece was seen on is what is written, and every rendering
    # is made from it again on the way back in, so a restored set holds exactly
    # what a freshly learned one holds and reads exactly what it read.
    r.append(check("  and reads exactly what the learned set read",
                   named(fresh, six), named(own, six)))
    r.append(check("  a piece set never learned here misses the cache",
                   P.PieceReader(cache_dir=only6.name).restore(boards[0][1]),
                   False))
    r.append(check("  as does the same set in a different sized window",
                   P.PieceReader(cache_dir=only6.name).restore(
                       six[0][1].resize((560, 560), Image.LANCZOS)), False))
    r.append(check("  and the cache directory ignores itself",
                   open(os.path.join(only6.name, ".gitignore")).read().strip(),
                   "*"))
    only6.cleanup()

    # A sheet with slots missing has to fail rather than load. crop() pads out
    # of bounds with black, and a black block is a shape like any other, so the
    # seven missing slots would otherwise load as templates of nothing.
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
    # tracks this code. The best case moves only when the work per board does.
    #
    # 17 ms against the 20 the mask reader took on the same board, and 23 with
    # the arrow drawn against the 40 the mask reader took on a soft capture it
    # had to sharpen. There is one ceiling now rather than two, because there
    # is no unsharp mask to pay for.
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

    with_arrow = _bench._draw_arrow(boards[0][1], "g1f3")
    reader.classify(with_arrow)
    runs = []
    for _ in range(20):
        t0 = time.perf_counter()
        reader.classify(with_arrow)
        runs.append(time.perf_counter() - t0)
    arrow_per = min(runs) * 1000
    print("      classify: %.0f ms with the coach arrow on the board" % arrow_per)
    r.append(check("  and under 30 ms with the arrow drawn on it too",
                   arrow_per < 30, True))

    cache.cleanup()
    print("\n%d/%d passed" % (sum(bool(x) for x in r), len(r)))
    return 0 if all(r) else 1


if __name__ == "__main__":
    sys.exit(main())
