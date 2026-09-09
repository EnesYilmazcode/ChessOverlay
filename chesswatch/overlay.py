"""An arrow drawn on the real board, over whatever program is showing it.

A click-through, always on top, colour-keyed window sitting exactly on the
board rectangle the watcher found. It draws one arrow: the move the coach
suggests, from square to square, on the actual pixels you are looking at.

The awkward part is that the recorder is reading the same pixels this paints
on. It cannot be allowed to corrupt a game. read_occupancy() converts the board
to grey and counts only pixels brighter than 244 or darker than 70, so the
arrow is drawn in a colour that lands between those two cutoffs and is
therefore invisible to the reader. Even blended against pure white or pure
black underneath it stays inside the band, which overlaytest.py measures rather
than assumes.

There are three arrow colours. Two are for the move to play, because the coach
answers for whoever is to move and you need to see at a glance whether you are
looking at your plan or theirs: yours is cyan and greys to 165, theirs is violet
and greys to 123. The third is the move to avoid, and it is red.

None of them is safe on account of the others. Each one has to sit inside the
band on its own, blended over anything, which is why overlaytest.py puts all
three over pure black and over pure white and reads the greys back off the
screen.

That band is why the red is not as dark as a warning colour wants to be. The
reader counts a pixel as part of a piece below grey 70, and at the window's
alpha that puts a floor of grey 83 on any colour drawn here. A proper dark red
is under it: #8B0000 greys to 42 and pure #FF0000 to 76, and either of them
would be read as a black piece wherever the arrow crossed a square. #E04242 is
about as deep as a red can be and still be invisible to the reader, and it
clears the floor by 26 greys where the violet clears it by 34.

The worst that arrow coverage can do is change what a square reads as: a white
pawn with the shaft painted down its file loses more bright pixels than dark
ones and the square comes back black. That position matches no legal move, so
the frame is ignored and the recorder waits. It cannot write down a move that
did not happen.
"""

import ctypes
import sys
import tkinter as tk

import chess

YOURS = "#00E8FF"         # greys to 165, between the reader's 70 and 244
THEIRS = "#A64BFF"        # greys to 123, in the same band
MISTAKE = "#E04242"       # greys to 113, and see the note above about red
KEY = "#010101"           # becomes transparent; never drawn
ALPHA = 0.85

# The shaft, as a share of a square. The move to avoid is drawn thinner: it is
# the smaller of the two things being said, and two arrows cover more of the
# board than one, which is coverage the reader has to survive.
WEIGHT = 0.16
BAD_WEIGHT = 0.11

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000


def colour_for(mine, bad=False):
    """Which colour an arrow gets: whose move it is, and whether it is the move
    to avoid rather than the move to play.

    A caller cannot hand in a colour of its own, because only these three have
    been measured against the reader; see the module docstring before inventing
    a fourth. It is a function rather than an expression inside Arrow.show so
    that overlaytest, which paints a model of the arrow in PIL, asks this rather
    than re-deriving it and then measuring its own answer.

    The move to avoid is one colour for both sides. Whose move it is, is already
    said by the arrow beside it and by the line above the board, and a second
    pair of reds would be two more things to learn before the picture means
    anything.
    """
    if bad:
        return MISTAKE
    return YOURS if mine else THEIRS


def plan_for(move, mine, bad=None):
    """The arrows to draw, back to front, as (move, colour, share of a square).

    The move to avoid goes down first, so where the two cross it is the move to
    play that is on top and unbroken.
    """
    plan = []
    if bad is not None:
        plan.append((bad, colour_for(mine, True), BAD_WEIGHT))
    if move is not None:
        plan.append((move, colour_for(mine), WEIGHT))
    return plan


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


def wanted(on, region, position, advice_for, advice_uci, cleared,
           flipped=False, mine=True, bad_uci=None, bad_for=None):
    """What the arrow should be showing right now, or None for nothing at all.

    The whole staleness question with no window in it, so it can be reasoned
    about and checked on its own. Every argument is state the app already
    holds: whether the switch is on, where the board is, the position now on
    screen, the position the last engine reply was about, the move it named, a
    position whose arrow was cleared by hand, and whose move the reply was for,
    which is the colour.

    An arrow is only ever right about one position. The app used to draw it
    once, when advice arrived, and never look at it again, so it stayed on the
    board through the move that made it wrong, stayed put when the window moved
    under it, and stayed up when the board went away entirely. Deciding it
    fresh from the current state is what fixes all three: what comes back is a
    complete description of the arrow, so a caller that redraws whenever this
    changes cannot leave a stale one behind.

    The move to avoid arrives seconds after the move to play and is its own
    answer about its own position, so it gets the same rule and its own fen to
    be judged against. It is dropped rather than the whole picture when it is
    the stale half, which is the ordinary case: for the second or so between
    the two answers there is a move to play and nothing yet to avoid.
    """
    if not (on and region and advice_uci and position):
        return None            # nothing on screen to be advising about
    if advice_for != position:
        return None            # the board has moved past this advice
    if cleared is not None and advice_for == cleared:
        return None            # taken down by hand, and not for one frame only
    bad = bad_uci if (bad_uci and bad_for == position) else None
    return tuple(region), advice_uci, bool(flipped), bool(mine), bad


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
        try:
            self.win.withdraw()
            self.win.overrideredirect(True)
            self.win.attributes("-topmost", True)
            try:
                self.win.attributes("-transparentcolor", KEY)
                self.win.attributes("-alpha", ALPHA)
            except tk.TclError:
                pass                  # not Windows; the arrow still draws
            self.canvas = tk.Canvas(self.win, bg=KEY, highlightthickness=0,
                                    borderwidth=0)
            self.canvas.pack(fill="both", expand=True)
            self.win.update_idletasks()
            self.click_through = make_click_through(self.win)
        except BaseException:
            # Take the window with us. Whoever asked for the Arrow never gets a
            # reference back, so nothing after this could destroy it and it
            # would sit on the board for the rest of the session. Tearing it
            # down must not replace the exception that caused the teardown.
            try:
                self.win.destroy()
            except BaseException:
                pass
            raise
        self._styled = False
        self._shown = False
        self._last = None

    def show(self, region, move, flipped=False, mine=True, bad=None):
        """region is the board on screen as (x, y, w, h), in absolute desktop
        coordinates. move is a chess.Move and mine says whose it is, which is
        the colour. bad is the move to avoid, drawn in red, or None while the
        engine is still working out whether there is one."""
        if region is None or move is None:
            self.hide()
            return
        plan = plan_for(move, mine, bad)
        # The colours are part of the picture, so the same move for the other
        # side has to count as a different one or it will not repaint.
        key = (tuple(region), flipped,
               tuple((drawn.uci(), colour) for drawn, colour, _ in plan))
        x, y, w, h = region
        if key != self._last:
            self._last = key
            self.win.geometry("%dx%d+%d+%d" % (w, h, x, y))
            self._draw(region, flipped, plan)
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

    def _draw(self, region, flipped, plan):
        """Both arrows on the one canvas, in the order plan_for put them.

        One window rather than two, so nothing is ever drawn over an arrow that
        is itself half transparent. Every colour here has been measured over the
        board and over pure black and white at one coat of alpha; two coats is a
        fourth colour nobody has measured.
        """
        x, y, w, h = region
        step = w / 8.0
        head = (step * 0.42, step * 0.52, step * 0.30)
        self.canvas.delete("all")
        self.canvas.configure(width=w, height=h)
        for move, colour, weight in plan:
            local = []
            for px, py in path_points(region, move, flipped):
                local += [px - x, py - y]
            self.canvas.create_line(*local, fill=colour,
                                    width=max(2, step * weight),
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
