"""Piece sets to test against, drawn from the fonts already on the machine.

The reader has only ever been measured on two piece sets, and both were cut
from screenshots taken from white's side of the board. That is not enough to
tell a reader that works from one that has learned these two sets by heart.

Unicode carries both halves of a chess set: U+2654 to U+2659 are the white
pieces, drawn as an outline, and U+265A to U+265F are the black ones, drawn
filled. That is the same structure a real set has, a light body inside a dark
edge against a dark body, so a font is a piece set. There are hundreds on an
ordinary Windows install, they differ from each other far more than two
screenshots do, and none of them belongs to anybody who would mind.

Nothing here ships. It renders boards for measurement, and the sets it makes
are held in memory rather than written into the repository.
"""

import glob
import os

import chess
from PIL import Image, ImageDraw, ImageFont

WHITE_GLYPHS = {"K": "\u2654", "Q": "\u2655", "R": "\u2656",
                "B": "\u2657", "N": "\u2658", "P": "\u2659"}
BLACK_GLYPHS = {"k": "\u265A", "q": "\u265B", "r": "\u265C",
                "b": "\u265D", "n": "\u265E", "p": "\u265F"}

# chess.com's own two, so a run can always be compared against the board the
# reader was actually built on.
LIGHT = (235, 236, 208)
DARK = (115, 149, 82)

FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def _ink(face, glyph, box=96):
    """The glyph's own pixels, as a set of coordinates."""
    from PIL import Image, ImageDraw
    img = Image.new("L", (box, box), 0)
    ImageDraw.Draw(img).text((box // 8, 0), glyph, font=face, fill=255)
    return frozenset(i for i, v in enumerate(img.get_flattened_data()) if v > 128)


def fonts(limit=None):
    """Every font on the machine that really draws all twelve chess pieces.

    Asking the font for a glyph's size is not enough. A font without the chess
    block still answers, because the shaper substitutes a fallback box, and it
    answers the same size for every one of the twelve. So the pieces are
    rendered and compared: a real set draws twelve different shapes, a fallback
    draws one shape twelve times. Measured over this machine's 344 candidates,
    that distinction removes all but a handful.
    """
    out = []
    for path in sorted(glob.glob(os.path.join(FONT_DIR, "*.ttf"))
                       + glob.glob(os.path.join(FONT_DIR, "*.ttc"))):
        try:
            face = ImageFont.truetype(path, 64)
            shapes = [_ink(face, g) for g in
                      list(WHITE_GLYPHS.values()) + list(BLACK_GLYPHS.values())]
        except Exception:
            continue
        if any(len(sh) < 200 for sh in shapes):
            continue
        if len(set(shapes)) < 12:
            continue
        out.append(path)
        if limit and len(out) >= limit:
            break
    return out


def render(board, font_path, size=824, flipped=False,
           light=LIGHT, dark=DARK, decoration="none", lit=()):
    """One position drawn in one font, as a board image.

    The glyph is fitted to the square by its own ink box rather than by the
    font's metrics, because the two disagree wildly across faces and a piece
    that fills a quarter of its square is not a piece set anybody ships.
    """
    step = size // 8
    img = Image.new("RGB", (step * 8, step * 8))
    pen = ImageDraw.Draw(img)
    for row in range(8):
        for col in range(8):
            pen.rectangle([col * step, row * step,
                           (col + 1) * step - 1, (row + 1) * step - 1],
                          fill=light if (row + col) % 2 == 0 else dark)

    face = ImageFont.truetype(font_path, int(step * 0.78))
    ranks = range(8) if flipped else range(7, -1, -1)
    for row, rank in enumerate(ranks):
        files = range(7, -1, -1) if flipped else range(8)
        for col, file in enumerate(files):
            piece = board.piece_at(chess.square(file, rank))
            if not piece:
                continue
            symbol = piece.symbol()
            # A white piece is a light body inside a dark edge, so it takes
            # both glyphs: the filled one in white for the body, the outlined
            # one in black on top for the edge. Drawing the outline alone
            # leaves board colour where the body should be, and the square then
            # reads as a black piece, which is the bug this set exists to find.
            solid = BLACK_GLYPHS[symbol.lower()]
            edge = WHITE_GLYPHS[symbol.upper()]
            box = face.getbbox(solid)
            gw, gh = box[2] - box[0], box[3] - box[1]
            if gw <= 0 or gh <= 0:
                continue
            x = col * step + (step - gw) // 2 - box[0]
            y = row * step + (step - gh) // 2 - box[1]
            if piece.color:
                pen.text((x, y), solid, font=face, fill=(255, 255, 255))
                pen.text((x, y), edge, font=face, fill=(0, 0, 0))
            else:
                pen.text((x, y), solid, font=face, fill=(0, 0, 0))
    if decoration in ("highlight", "both") and lit:
        img = highlight(img, lit)
    if decoration in ("labels", "both"):
        img = labels(img, light, dark)
    return img


def readable(font_path, size=824):
    """True when a board drawn in this font still looks like a board.

    Only the checkerboard is asked about. The occupancy reader is deliberately
    not consulted: it decides white from black by counting a square's bright
    pixels against its dark ones, and on a set whose white pieces are mostly
    outline it calls them black. Measured on seguisym at 824px, the white rook
    on a1 carries 74 bright pixels against 131 dark. That is the defect these
    sets exist to find, so a set is not disqualified for having it.
    """
    import watcher as W
    img = render(chess.Board(), font_path, size)
    return W.grid_score(img, 0, 0, img.size[0]) >= 0.95


THEMES = {
    "green": ((235, 236, 208), (115, 149, 82)),      # chess.com's own
    "brown": ((240, 217, 181), (181, 136, 99)),      # lichess default
    "blue":  ((222, 227, 230), (140, 162, 173)),
    "grey":  ((220, 220, 220), (150, 150, 150)),     # low contrast on purpose
}

HIGHLIGHT = (246, 246, 105)


def highlight(img, squares, light=LIGHT, dark=DARK, tol=40):
    """Paint the last-move wash over some squares, under the pieces.

    Worth carrying because it is a silent failure rather than a loud one: the
    shipped reader names a WRONG piece on almost every highlighted square that
    holds one, and marks nothing unreadable while doing it. A bench that never
    draws the wash cannot see that, and this one did not.

    Only pixels that are board colour are replaced, which is what the site
    does: the wash goes under the piece rather than over it. Matched against
    the two square colours by distance rather than by a brightness band,
    because a band tuned to a green board misses a brown one.
    """
    from PIL import Image as _I
    step = img.size[0] // 8
    out = img.copy()
    wash = _I.new("RGB", (step, step), HIGHLIGHT)
    for row, col in squares:
        left, top = col * step, row * step
        square = out.crop((left, top, left + step, top + step))
        px = square.load()
        mask = _I.new("L", square.size, 0)
        mp = mask.load()
        for y in range(square.size[1]):
            for x in range(square.size[0]):
                r, g, b = px[x, y][:3]
                for want in (light, dark):
                    if (abs(r - want[0]) <= tol and abs(g - want[1]) <= tol
                            and abs(b - want[2]) <= tol):
                        mp[x, y] = 255
                        break
        square.paste(wash, (0, 0), mask)
        out.paste(square, (left, top))
    return out


def labels(img, light, dark):
    """Rank and file coordinates in the square corners, the way a real board
    draws them. Free for a matcher that thresholds and expensive for one that
    normalises, so leaving them out favours one design over the other."""
    from PIL import ImageDraw, ImageFont
    step = img.size[0] // 8
    out = img.copy()
    pen = ImageDraw.Draw(out)
    try:
        face = ImageFont.truetype("arialbd.ttf", max(9, step // 7))
    except Exception:
        face = ImageFont.load_default()
    for row in range(8):
        pen.text((3, row * step + 2), str(8 - row), font=face,
                 fill=dark if row % 2 == 0 else light)
    for col in range(8):
        pen.text((col * step + step - step // 6, 8 * step - step // 4),
                 "abcdefgh"[col], font=face,
                 fill=dark if (7 + col) % 2 == 0 else light)
    return out


def variants(img, kind):
    """One board picture, altered the way real piece sets and real captures
    differ from each other.

    Piece sets differ in how heavy the outline is, how light the body is and
    how much of the square the piece fills. Captures differ in size, sharpness
    and brightness. These are the axes, applied one at a time so a result can
    be attributed to one of them rather than to a soup.
    """
    from PIL import ImageEnhance, ImageFilter
    if kind == "plain":
        return img
    if kind == "thin":                     # a lighter outline
        return img.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    if kind == "heavy":                    # a heavier one
        return img.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    if kind == "grey_body":                # a body that is not pure white
        return ImageEnhance.Brightness(img).enhance(0.88)
    if kind == "small":                    # a piece that sits smaller in its square
        w = img.size[0]
        return img.resize((int(w * 0.72), int(w * 0.72)), Image.LANCZOS).resize(
            (w, w), Image.LANCZOS)
    if kind == "blur":
        return img.filter(ImageFilter.GaussianBlur(1.1))
    if kind == "contrast":
        return ImageEnhance.Contrast(img).enhance(1.25)
    if kind == "dim":
        return ImageEnhance.Brightness(img).enhance(0.8)
    raise ValueError("no such variant: " + kind)


VARIANTS = ("plain", "thin", "heavy", "grey_body", "small",
            "blur", "contrast", "dim")

# Drawn while the board is made rather than done to the picture afterwards.
DECORATIONS = ("none", "highlight", "labels")
