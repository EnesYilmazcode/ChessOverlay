"""Photograph one window, and nothing else that happens to be on screen.

Grabbing a rectangle of the desktop is the obvious way and it is wrong here.
This program's arrow overlay is a transparent click-through window sitting over
the board, so its rectangle contains whatever is behind it. Doing that once
captured the owner's terminal and desktop instead of the app, which would have
been published to a public repository had it not been checked.

PrintWindow asks a window to draw itself into a bitmap, so what comes back is
the window's own pixels whatever is stacked above or below it. Layered windows
need PW_RENDERFULLCONTENT, which is why the flag is 2 rather than 0.

Run:  python windowshot.py "ChessWatch" out.png
      python windowshot.py --list
"""

import ctypes
import sys
from ctypes import wintypes

from PIL import Image

PW_RENDERFULLCONTENT = 2

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


def windows():
    """Every visible titled window, as (handle, title, class, w, h)."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        title = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, title, n + 1)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        found.append((hwnd, title.value, cls.value,
                      rect.right - rect.left, rect.bottom - rect.top))
        return True

    user32.EnumWindows(each, 0)
    return found


def shoot(hwnd, path):
    """One window's own pixels, whatever is stacked over it."""
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        raise ValueError("that window has no size")

    src = user32.GetWindowDC(hwnd)
    dst = gdi32.CreateCompatibleDC(src)
    bmp = gdi32.CreateCompatibleBitmap(src, w, h)
    gdi32.SelectObject(dst, bmp)
    try:
        if not user32.PrintWindow(hwnd, dst, PW_RENDERFULLCONTENT):
            raise OSError("the window refused to draw itself")
        # Negative height asks GDI for a top-down bitmap, so the rows arrive in
        # the order PIL expects rather than upside down.
        info = ctypes.create_string_buffer(40)
        ctypes.memset(info, 0, 40)
        hdr = (ctypes.c_uint32 * 10).from_buffer(info)
        hdr[0] = 40
        hdr[1] = w
        hdr[2] = ctypes.c_uint32(-h & 0xFFFFFFFF).value
        hdr[3] = 1 | (32 << 16)          # one plane, 32 bits a pixel
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(dst, bmp, 0, h, buf, info, 0):
            raise OSError("could not read the bitmap back")
        img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
        if _blank(img):
            # Tk keeps its widgets in child windows and PrintWindow does not
            # reach them, so a Tk app comes back as an empty frame. Fall back
            # to reading the screen where the window is, which is only safe
            # because this window is opaque: doing it to the arrow overlay,
            # which is transparent, captures the desktop behind it instead.
            img = _from_screen(hwnd, rect)
        img.save(path)
        return img.size
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(dst)
        user32.ReleaseDC(hwnd, src)


def _blank(img):
    """True when a capture holds one colour, which is what an empty frame from
    PrintWindow looks like."""
    colours = img.getcolors(maxcolors=8)
    return colours is not None and len(colours) <= 2


def _from_screen(hwnd, rect):
    """The window's rectangle, read off the screen, with the window raised
    first so nothing else is in front of it. Only for opaque windows."""
    import time
    import mss
    user32.ShowWindow(hwnd, 9)                      # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    time.sleep(0.4)
    if user32.GetForegroundWindow() != hwnd:
        raise OSError("could not bring the window to the front, refusing to "
                      "photograph whatever is covering it")
    box = {"left": rect.left, "top": rect.top,
           "width": rect.right - rect.left, "height": rect.bottom - rect.top}
    with mss.mss() as sct:
        shot = sct.grab(box)
    return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def pick(title):
    """The best window for a name. Prefers a real top level window over the
    arrow overlay, which is a borderless Toplevel carrying the same title."""
    hits = [w for w in windows() if title.lower() in w[1].lower()]
    if not hits:
        return None
    hits.sort(key=lambda w: (w[2] == "#32770", -(w[3] * w[4])))
    return hits[0]


def main():
    if "--list" in sys.argv:
        for hwnd, title, cls, w, h in windows():
            print("%-10s %-22s %4dx%-4d  %s" % (hwnd, cls, w, h, title[:50]))
        return 0
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    hit = pick(sys.argv[1])
    if not hit:
        print("no visible window named like %r" % sys.argv[1])
        return 1
    hwnd, title, cls, w, h = hit
    print("shooting %r  class %s  %dx%d" % (title, cls, w, h))
    print("saved", sys.argv[2], shoot(hwnd, sys.argv[2]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
