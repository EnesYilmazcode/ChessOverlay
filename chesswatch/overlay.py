"""An arrow drawn on the real board, over whatever program is showing it.

A click-through, always on top, colour-keyed window sitting exactly on the
board rectangle the watcher found. It draws one arrow: the move the coach
suggests, from square to square, on the actual pixels you are looking at.

The awkward part is that the recorder is reading the same pixels this paints
on. It cannot be allowed to corrupt a game. read_occupancy() converts the board
to grey and counts only pixels brighter than 244 or darker than 70, so the
arrow is drawn in a colour that lands between those two cutoffs and is
therefore invisible to the reader. Cyan (0,232,255) greys to 165. Even blended
against pure white or pure black underneath it stays inside the band, which
overlaytest.py measures rather than assumes.

The worst that arrow coverage can do is hide a piece, which makes a square read
empty. That position matches no legal move, so the frame is ignored and the
recorder waits. It cannot write down a move that did not happen.
"""

import ctypes
import sys
import tkinter as tk

import chess

COLOUR = "#00E8FF"        # greys to 165, between the reader's 70 and 244
KEY = "#010101"           # becomes transparent; never drawn
ALPHA = 0.85

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000


def square_centre(region, square, flipped):
    """Where a chess square is on the screen, in absolute desktop pixels."""
    x, y, w, h = region
    step = w / 8.0
    file, rank = chess.square_file(square), chess.square_rank(square)
    col = 7 - file if flipped else file
    row = rank if flipped else 7 - rank
    return x + (col + 0.5) * step, y + (row + 0.5) * step


def path_points(region, move, flipped):
    """The corners of the arrow. A knight turns a corner the way a knight
    moves, because a straight diagonal across an L is hard to read."""
    x0, y0 = square_centre(region, move.from_square, flipped)
    x1, y1 = square_centre(region, move.to_square, flipped)
    df = abs(chess.square_file(move.to_square) - chess.square_file(move.from_square))
    dr = abs(chess.square_rank(move.to_square) - chess.square_rank(move.from_square))
    if {df, dr} == {1, 2}:
        # Long leg first, then the short one, so the head sits square on.
        if df == 2:
            return [(x0, y0), (x1, y0), (x1, y1)]
        return [(x0, y0), (x0, y1), (x1, y1)]
    return [(x0, y0), (x1, y1)]


def make_click_through(win):
    """Let the mouse straight through. Without this the arrow sits between you
    and the board and you cannot play. Windows only; elsewhere the arrow still
    draws and you would have to turn it off to click underneath it."""
    if sys.platform != "win32":
        return False
    user32 = ctypes.windll.user32
    hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED
                          | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
                          | WS_EX_NOACTIVATE)
    return True


class Arrow:
    """One arrow over one board. show() moves and redraws it, hide() takes it
    away without destroying the window, since it is shown and hidden often."""

    def __init__(self, root):
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-transparentcolor", KEY)
            self.win.attributes("-alpha", ALPHA)
        except tk.TclError:
            pass                      # not Windows; the arrow still draws
        self.canvas = tk.Canvas(self.win, bg=KEY, highlightthickness=0,
                                borderwidth=0)
        self.canvas.pack(fill="both", expand=True)
        self.win.update_idletasks()
        self.click_through = make_click_through(self.win)
        self._styled = False
        self._shown = False
        self._last = None

    def show(self, region, move, flipped=False):
        """region is the board on screen as (x, y, w, h), in absolute desktop
        coordinates. move is a chess.Move."""
        if region is None or move is None:
            self.hide()
            return
        key = (tuple(region), move.uci(), flipped)
        x, y, w, h = region
        if key != self._last:
            self._last = key
            self.win.geometry("%dx%d+%d+%d" % (w, h, x, y))
            self._draw(region, move, flipped)
        if not self._shown:
            self.win.deiconify()
            self.win.update_idletasks()
            # Tk hands a withdrawn toplevel a different window than the one it
            # finally maps, so the styles set in __init__ can land on a handle
            # that is thrown away. Set them again on the window that is really
            # on screen, once.
            if not self._styled:
                self.click_through = make_click_through(self.win)
                self._styled = True
            self.win.attributes("-topmost", True)
            self.win.lift()
            self._shown = True

    def _draw(self, region, move, flipped):
        x, y, w, h = region
        step = w / 8.0
        pts = path_points(region, move, flipped)
        local = []
        for px, py in pts:
            local += [px - x, py - y]
        self.canvas.delete("all")
        self.canvas.configure(width=w, height=h)
        head = (step * 0.42, step * 0.52, step * 0.30)
        self.canvas.create_line(*local, fill=COLOUR, width=max(2, step * 0.16),
                                arrow="last", arrowshape=head,
                                capstyle="round", joinstyle="round")

    def hide(self):
        if self._shown:
            self.win.withdraw()
            self._shown = False

    def destroy(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass
