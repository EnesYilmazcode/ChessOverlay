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
    """Every physical monitor as (left, top, width, height), primary first.

    mss hands them back in EnumDisplayMonitors order, which usually starts with
    the primary but is not promised to, and which one comes first decides where
    a test puts its window. The sort is stable, so a build of mss that does not
    say which monitor is primary leaves the order exactly as it was.
    """
    with _MSS() as sct:
        found = list(sct.monitors[1:])
    found.sort(key=lambda m: not m.get("is_primary", False))
    return [(m["left"], m["top"], m["width"], m["height"]) for m in found]


def grab_reads_bgra():
    """Does the real capture still turn mss's bytes into the right colours?

    Nothing else in a headless run touches chesswatch.grab, because PaperScreen
    replaces it. mss hands back BGRA and PIL is told to read it as "BGRX", and
    swapping that pair swaps red and blue in every frame the app ever reads,
    which no other check in the suite would notice. mss is stubbed out, so this
    costs no screen.

    Returns True, False, or None if chesswatch no longer offers the seam.
    """
    class Shot:
        width, height = 2, 1
        bgra = bytes([0, 0, 255, 0,        # blue, green, red, ignored: red
                      255, 0, 0, 0])       # blue

    class Stub:
        asked = None

        def grab(self, box):
            Stub.asked = box
            return Shot()

        def close(self):
            pass

    real = getattr(C, "_MSS", None)
    if real is None:
        return None
    C._MSS = Stub
    # grab() holds one grabber per thread, so the stub only reaches it if
    # whatever this thread already had is dropped first, and the stub must not
    # be left behind for the rest of the run either.
    C.close_sct()
    try:
        got = list(C.grab((7, 9, 2, 1)).convert("RGB").getdata())
    finally:
        C._MSS = real
        C.close_sct()
    return (got == [(255, 0, 0), (0, 0, 255)]
            and Stub.asked == {"left": 7, "top": 9, "width": 2, "height": 1})


class PaperScreen:
    """A desktop made of one image."""

    mode = "headless"
    pause = 0.0                # nothing has to reach a monitor, so wait for nothing
    settle = 0.0

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
    # Both are at least what the full screen version used to sleep in the same
    # place, since nobody has run this path on a real desktop yet and a repaint
    # that has not landed reads as a board in the wrong place.
    pause = 0.12               # one frame reaching the glass
    settle = 0.30              # a window that has just appeared, moved or resized

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
