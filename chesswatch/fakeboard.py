"""Test helper: render any position using real pixels from a screenshot.

The piece sprites are cut out of a real screenshot, so a rendered position
exercises the same classifier thresholds as a live board. It is not pixel-perfect
chess.com (a sprite keeps whatever square colour it was cut from) but the reader
only ever counts very bright and very dark pixels, so the square colour
underneath is irrelevant to what it measures.

The reference does not have to be the opening position and does not have to be
chess.com's piece set. Pass the position that is on screen and the sprites are
cut from wherever those pieces happen to stand, which is what lets the same
class render one position in several piece sets and compare them.
"""

import chess
from PIL import Image

# Fallback square colours, used only if a reference square is too busy to
# measure. chess.com's default green, as captured.
LIGHT = (235, 236, 208)
DARK = (115, 149, 82)

# How far off a square colour a pixel has to be to count as part of the piece,
# squared, in RGB. Generous, because it only has to separate piece art from two
# flat colours.
CUTOUT_TOL = 2000


def _grid(board, flipped=False):
    """8 rows of piece letters as they appear on screen, row 0 at the top."""
    ranks = range(8) if flipped else range(7, -1, -1)
    rows = []
    for rank in ranks:
        files = range(7, -1, -1) if flipped else range(8)
        rows.append([(board.piece_at(chess.square(f, rank)).symbol()
                      if board.piece_at(chess.square(f, rank)) else ".")
                     for f in files])
    return rows


def _piece_sources(grid):
    """Where to cut each piece sprite from, as (row, col) on the board grid.

    Scanned down the a file first, then the b file and so on, which on the
    opening position picks the same squares this helper has always used.
    """
    found = {}
    for col in range(8):
        for row in range(8):
            if grid[row][col] != ".":
                found.setdefault(grid[row][col], (row, col))
    return found


def _empty_sources(grid):
    """Every empty square of each colour worth measuring a board colour from.

    Interior squares only. A board draws its rank labels down one outer file
    and its file labels along one outer rank, so the four edges of the grid are
    where a coordinate glyph lives. This used to read a5 and a6, which on a
    labelled board are the squares carrying "5" and "6", and every rendered
    board came out stamped with them. testdata/1.png has no in-square labels
    and hid it; testdata/6.png has them.

    All of them rather than the first one found, which is the same argument one
    step further out. A coordinate label is a minority of one square's pixels
    and loses to the mode; a last-move highlight is the whole square and wins
    it outright. Taking the mode across a dozen squares means a highlight has
    to be on most of the board before it can move the answer, and it never is.
    """
    found = {"light": [], "dark": []}
    for row in range(1, 7):
        for col in range(1, 7):
            if grid[row][col] == ".":
                found["light" if (row + col) % 2 == 0 else "dark"].append((row, col))
    return found


def _square_colour(sprites, fallback):
    """The colour these squares are mostly made of, over all of them at once."""
    counts = {}
    for sprite in sprites:
        for n, colour in sprite.getcolors(sprite.size[0] * sprite.size[1]):
            counts[colour] = counts.get(colour, 0) + n
    if not counts:
        return fallback
    return max(counts.items(), key=lambda pair: pair[1])[0]


def _cutout(sprite, light, dark):
    """Mask of the pixels belonging to the piece rather than to the square it
    was cut from, so the piece can be dropped onto the right colour square and
    the checkerboard survives."""
    px = sprite.load()
    mask = Image.new("L", sprite.size, 0)
    mp = mask.load()
    for y in range(sprite.height):
        for x in range(sprite.width):
            r, g, b = px[x, y][:3]
            to_light = (r - light[0]) ** 2 + (g - light[1]) ** 2 + (b - light[2]) ** 2
            to_dark = (r - dark[0]) ** 2 + (g - dark[1]) ** 2 + (b - dark[2]) ** 2
            if min(to_light, to_dark) > CUTOUT_TOL:
                mp[x, y] = 255
    return mask


class Renderer:
    """Sprites cut from one screenshot, ready to redraw any position.

    reference_png and rect are the screenshot and the board rectangle
    find_board() returned for it. board is the position that screenshot shows,
    and flipped whether it was seen from black's side; the opening position seen
    from white is assumed, which is what the reference shots hold.
    """

    def __init__(self, reference_png, rect, board=None, flipped=False):
        img = Image.open(reference_png).convert("RGB")
        x0, y0, size = rect
        self.step = size // 8
        self.size = self.step * 8
        grid = _grid(board or chess.Board(), flipped)

        def cut(row, col):
            left = x0 + col * size // 8
            top = y0 + row * size // 8
            return img.crop((left, top, left + self.step, top + self.step))

        empties = _empty_sources(grid)
        self.light = _square_colour([cut(*at) for at in empties["light"]], LIGHT)
        self.dark = _square_colour([cut(*at) for at in empties["dark"]], DARK)

        self.sprites = {key: cut(row, col)
                        for key, (row, col) in _piece_sources(grid).items()}
        self.masks = {key: _cutout(sprite, self.light, self.dark)
                      for key, sprite in self.sprites.items()}

        # Empty squares are painted flat from the measured colour rather than
        # pasted from a cut square. One is drawn up to 64 times a board, so
        # anything that came along with it, a coordinate glyph, a last-move
        # highlight, a board border, is drawn 64 times too.
        self.sprites["light"] = Image.new("RGB", (self.step, self.step), self.light)
        self.sprites["dark"] = Image.new("RGB", (self.step, self.step), self.dark)

    @property
    def pieces(self):
        """The piece letters this reference had on the board, so a caller can
        skip the positions it cannot draw. A reference that is not an opening
        position rarely has all twelve."""
        return set(self.masks)

    def can_render(self, board):
        return {p.symbol() for p in board.piece_map().values()} <= self.pieces

    def render(self, board, flipped=False, size=None, lit=(), highlight=None):
        """Draw a position. size resizes the finished board, which is the same
        resampling a smaller browser window puts the reader through.

        lit names board squares to paint with the last-move highlight, in the
        (light, dark) pair of colours given. Those are filled flat, for the same
        reason the empty squares are: the reference screenshot has no
        highlighted square to cut from, and chess.com draws a flat wash anyway.
        """
        # A single colour instead of a pair paints one shade over both and
        # still renders, which would be a fixture quietly disagreeing with the
        # board it claims to be. Cheaper to stop here than to find it later.
        if lit and (highlight is None or len(highlight) != 2):
            raise ValueError("lit squares need a (light, dark) pair of colours")
        out = Image.new("RGB", (self.size, self.size))
        ranks = range(8) if flipped else range(7, -1, -1)
        for row, rank in enumerate(ranks):
            files = range(7, -1, -1) if flipped else range(8)
            for col, file in enumerate(files):
                pos = (col * self.step, row * self.step)
                shade = (row + col) % 2
                if chess.square(file, rank) in lit:
                    out.paste(Image.new("RGB", (self.step, self.step),
                                        highlight[shade]), pos)
                else:
                    out.paste(self.sprites["light" if shade == 0 else "dark"], pos)
                piece = board.piece_at(chess.square(file, rank))
                if piece:
                    key = piece.symbol()
                    if key not in self.sprites:
                        raise KeyError("the reference had no %s to cut" % key)
                    out.paste(self.sprites[key], pos, self.masks[key])
        if size and size != self.size:
            out = out.resize((size, size), Image.LANCZOS)
        return out
