"""Test helper: render any position using real chess.com pixels.

The 12 piece sprites and 2 empty squares are cut out of a real screenshot, so a
rendered position exercises the same classifier thresholds as a live board. It
is not pixel-perfect chess.com (a sprite keeps whatever square colour it was cut
from) but the reader only ever counts very bright and very dark pixels, so the
square colour underneath is irrelevant to what it measures.
"""

import chess
from PIL import Image

# Where each sprite lives in the reference screenshot, as (row, col) on the
# board grid with row 0 being rank 8.
SOURCES = {
    "r": (0, 0), "n": (0, 1), "b": (0, 2), "q": (0, 3), "k": (0, 4), "p": (1, 0),
    "R": (7, 0), "N": (7, 1), "B": (7, 2), "Q": (7, 3), "K": (7, 4), "P": (6, 0),
    "light": (2, 0), "dark": (3, 0),
}


LIGHT = (235, 236, 208)
DARK = (115, 149, 82)


def _cutout(sprite):
    """Mask of the pixels belonging to the piece rather than to the square it
    was cut from, so the piece can be dropped onto the right colour square and
    the checkerboard survives."""
    px = sprite.load()
    mask = Image.new("L", sprite.size, 0)
    mp = mask.load()
    for y in range(sprite.height):
        for x in range(sprite.width):
            r, g, b = px[x, y][:3]
            to_light = (r - LIGHT[0]) ** 2 + (g - LIGHT[1]) ** 2 + (b - LIGHT[2]) ** 2
            to_dark = (r - DARK[0]) ** 2 + (g - DARK[1]) ** 2 + (b - DARK[2]) ** 2
            if min(to_light, to_dark) > 2000:
                mp[x, y] = 255
    return mask


class Renderer:
    def __init__(self, reference_png, rect):
        img = Image.open(reference_png).convert("RGB")
        x0, y0, size = rect
        self.step = size // 8
        self.size = self.step * 8
        self.sprites = {}
        self.masks = {}
        for key, (row, col) in SOURCES.items():
            left = x0 + col * size // 8
            top = y0 + row * size // 8
            sprite = img.crop((left, top, left + self.step, top + self.step))
            self.sprites[key] = sprite
            if key not in ("light", "dark"):
                self.masks[key] = _cutout(sprite)

    def render(self, board, flipped=False, lit=(), highlight=None):
        """`lit` names board squares to paint with the last-move highlight, in
        the (light, dark) pair of colours given. The reference screenshot has no
        highlighted square to cut a sprite from, so those squares are filled
        flat, which is what chess.com draws under a piece anyway."""
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
                    out.paste(self.sprites[key], pos, self.masks[key])
        return out
