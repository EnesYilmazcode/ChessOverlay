"""Where a test paints its board.

Two screens with the same three calls, so a test reads the same either way.

PaperScreen is a desktop that never leaves memory. The capture the worker uses
is pointed at a PIL image instead of at mss, so the worker still hunts for the
board on its own, grabs a region of "screen", classifies the squares and infers
the moves. Nothing is drawn on a monitor and no window is opened.

RealScreen is a window on the actual desktop, sized to the board plus a margin.
Only what lives in real pixels needs it: what mss actually hands back, DPI
scaling, coordinates that land on a second monitor, and another window sitting
over the board.

The tests used to cover the whole desktop, both monitors, so nothing else on
screen could contaminate a run. That bought isolation at the price of the
machine for as long as the test took. PaperScreen buys the same isolation for
nothing. RealScreen gives it up and says so: it reports the region the worker
locked onto, so a run that found something else on your desktop is visible
rather than silent.
"""

import mss
from PIL import Image

import chesswatch as C

BG = "#262421"                 # the app's own grey, nowhere near a square colour
BG_RGB = (0x26, 0x24, 0x21)

_MSS = getattr(mss, "MSS", None) or mss.mss


def monitors():
    """Every physical monitor as (left, top, width, height), primary first."""
    with _MSS() as sct:
        return [(m["left"], m["top"], m["width"], m["height"])
                for m in sct.monitors[1:]]


class PaperScreen:
    """A desktop made of one image."""

    mode = "headless"
    pause = 0.0                # nothing has to reach a monitor, so wait for nothing

    def __init__(self, width, height):
        self.width, self.height = width, height
        self.desktop = Image.new("RGB", (width, height), BG_RGB)
        self._real = (C.grab, C.virtual_screen)
        C.grab = self._grab
        C.virtual_screen = lambda: (0, 0, width, height)

    def show(self, image, at):
        """Put one board at absolute desktop coordinates. Whatever was there
        before is gone, which is how the single moving label behaved."""
        self.desktop.paste(BG_RGB, (0, 0, self.width, self.height))
        self.desktop.paste(image, (int(at[0]), int(at[1])))

    def _grab(self, region):
        left, top, width, height = region
        return self.desktop.crop((left, top, left + width, top + height))

    def footprint(self):
        return "no window, 0 pixels of screen"

    def close(self):
        C.grab, C.virtual_screen = self._real


class RealScreen:
    """A window on the real desktop, no bigger than the board and a margin.

    topmost is off by default. A test that needs the board to stay in front of
    everything else has to ask, because claiming the top of the stacking order
    is most of what made the old full screen version unbearable.
    """

    mode = "on screen"
    pause = 0.06               # long enough for a repaint to reach the glass

    def __init__(self, margin=24, topmost=False):
        # Imported here rather than at the top so a machine with no Tk at all
        # can still run the headless path.
        import tkinter as tk
        from PIL import ImageTk

        self._ImageTk = ImageTk
        self.margin = margin
        self.topmost = topmost
        self.desktop = C.virtual_screen()
        self.size = None
        self.biggest = (0, 0)

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.configure(bg=BG)
        if topmost:
            self.root.attributes("-topmost", True)
        self.label = tk.Label(self.root, bd=0, bg=BG)

    def show(self, image, at):
        m = self.margin
        w, h = image.width + 2 * m, image.height + 2 * m
        self.root.geometry("%dx%d+%d+%d" % (w, h, at[0] - m, at[1] - m))
        photo = self._ImageTk.PhotoImage(image)
        self.label.configure(image=photo)
        self.label.image = photo          # Tk throws away an image nobody holds
        self.label.place(x=m, y=m)
        if not self.topmost:
            self.root.lift()              # in front now, without staying there
        self.size = (w, h)
        if w * h > self.biggest[0] * self.biggest[1]:
            self.biggest = (w, h)
        self.root.update()

    def footprint(self):
        w, h = self.biggest
        dw, dh = self.desktop[2], self.desktop[3]
        return ("biggest window %dx%d, %.1f%% of the %dx%d desktop%s"
                % (w, h, 100.0 * w * h / float(dw * dh), dw, dh,
                   ", topmost" if self.topmost else ""))

    def close(self):
        self.root.destroy()
