"""Checks for the window's layout: which parts of it are up, in what order,
and whether the rows fit the narrowest window the app allows.

Run:  python layouttest.py

Nothing here opens a window, and that is checked rather than promised: this
file replaces tkinter.Tk and tkinter.Toplevel with functions that raise before
it imports chesswatch, so anything that tried would fail loudly instead of
appearing on somebody's desktop.

What that leaves checkable is the part worth checking anyway.

  showing()        the whole decision about what is on screen, no widgets in it
  App._relayout    driven against widgets that write down what was done to them
  App._render      driven the same way, for what the lines end up saying
  the rows         measured against the real Segoe UI at the pixel size Tk
                   derives from the point size, at 100% and at 150% scaling

What is NOT checked here: how any of it actually looks. Colours, spacing,
whether the move is large enough to read across a room. Those need a screen
and a person.
"""

import os
import sys
import time
import types
import tkinter


class Opened(Exception):
    """Raised in place of a window, so a test can prove it opened none."""


def _forbid(*args, **kwargs):
    raise Opened("this test may not open a window")


tkinter.Tk = _forbid
tkinter.Toplevel = _forbid

import chesswatch as C            # noqa: E402  after the guard, on purpose

R = []


def check(what, got, want):
    ok = got == want
    R.append(ok)
    print("%-4s %s" % ("ok" if ok else "FAIL", what))
    if not ok:
        print("       got  %r" % (got,))
        print("       want %r" % (want,))
    return ok


# ---------------------------------------------------------------- no windows

def nothing_opens():
    print("\n-- no window is opened, and the guard proves it ----------")
    opened = True
    try:
        tkinter.Tk()
    except Opened:
        opened = False
    check("building a root raises instead of opening one", opened, False)
    opened = True
    try:
        tkinter.Toplevel()
    except Opened:
        opened = False
    check("  and so does a second window", opened, False)
    check("chesswatch imported with the guard in place",
          C.__name__, "chesswatch")


# ---------------------------------------------------------------- what shows

QUIET = {"setup": False, "advice": "", "detail": "", "note": "",
         "result": "", "arrow": False, "position": False}


def with_(**changes):
    state = dict(QUIET)
    state.update(changes)
    return state


def what_shows():
    print("\n-- what the window puts up -------------------------------")
    check("a game in progress with nothing to say is three stripes",
          C.showing(QUIET), ("top", "moves", "foot"))
    check("  and every one of them is a stripe the window has",
          [name for name in C.showing(QUIET) if name not in C.STRIPES], [])

    check("advice brings up the move line",
          C.showing(with_(advice="your move  Nf3")),
          ("top", "hero", "advice", "moves", "foot"))
    check("  the small print under it only when there is any",
          C.showing(with_(advice="your move  Nf3", detail="knight: g1 to f3")),
          ("top", "hero", "advice", "detail", "moves", "foot"))
    check("  and the small print never on its own",
          "detail" in C.showing(with_(detail="knight: g1 to f3")), False)

    check("the clear button waits for an arrow to clear",
          "clear" in C.showing(with_(advice="your move  Nf3")), False)
    check("  and turns up when one is drawn",
          C.showing(with_(advice="your move  Nf3", arrow=True)),
          ("top", "hero", "clear", "advice", "moves", "foot"))
    check("  but not while there is no advice to draw",
          "clear" in C.showing(with_(arrow=True)), False)

    check("a note is a stripe while it says something",
          "note" in C.showing(with_(note="board unclear")), True)
    check("  and no stripe at all once it is blank",
          "note" in C.showing(with_(note="")), False)
    check("the result line is the same",
          ("result" in C.showing(with_(result="You won by checkmate")),
           "result" in C.showing(QUIET)), (True, False))

    check("the drawer is shut unless it was asked for",
          ("setup" in C.showing(QUIET),
           "setup" in C.showing(with_(setup=True))), (False, True))
    check("  and it opens under the button that opens it, above the move",
          C.showing(with_(setup=True, advice="your move  Nf3")),
          ("top", "setup", "hero", "advice", "moves", "foot"))

    check("the position copy is off unless it was switched on",
          C.showing(with_(position=True)),
          ("top", "moves", "position", "foot"))

    everything = with_(setup=True, advice="your move  Nf3", detail="knight",
                       note="board unclear", result="You won", arrow=True,
                       position=True)
    check("with everything up, nothing is missing and nothing is doubled",
          C.showing(everything), C.STRIPES)


# ---------------------------------------------------------------- the packer

class Slate:
    """A widget that writes down what was done to it instead of drawing."""

    def __init__(self, name, log):
        self.name = name
        self.log = log

    def pack(self, **how):
        self.log.append("pack " + self.name)

    def pack_forget(self):
        self.log.append("forget " + self.name)


def packer(state):
    """App._relayout with slates for widgets. Everything it runs is App's."""
    log = []
    app = types.SimpleNamespace(
        _shown=(),
        _pack={name: (Slate(name, log), {}) for name in C.STRIPES})
    app._state = lambda: state
    app._relayout = lambda: C.App._relayout(app)
    return app, log


def the_packer():
    print("\n-- the packer does what showing() asked for --------------")
    state = with_(advice="your move  Nf3", arrow=True, note="board unclear")
    app, log = packer(state)
    app._relayout()

    packed = tuple(line[5:] for line in log if line.startswith("pack "))
    check("it packs exactly the stripes that were asked for",
          packed, C.showing(state))
    check("  in the order the window stacks them",
          packed, tuple(n for n in C.STRIPES if n in packed))
    check("  after unpacking every stripe, so no stale one is left up",
          sorted(line[7:] for line in log if line.startswith("forget ")),
          sorted(C.STRIPES))
    check("  and the two halves of the move row in the order they must go",
          packed.index("clear") < packed.index("advice"), True)

    log[:] = []
    app._relayout()
    check("running again on the same state touches nothing", log, [])

    state["note"] = ""
    log[:] = []
    app._relayout()
    check("a note going blank takes its stripe down",
          "pack note" in log, False)
    check("  and leaves the rest up",
          tuple(line[5:] for line in log if line.startswith("pack ")),
          C.showing(state))


def every_stripe_is_built():
    """A stripe with no widget behind it is a KeyError on the first relayout.

    This reads _build rather than running it, which is the weaker kind of
    check, and it is here only because building it needs a window.
    """
    print("\n-- every stripe has a widget -----------------------------")
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(C.App._build).lstrip())
    registered = [node.slice.value for node in ast.walk(tree)
                  if isinstance(node, ast.Subscript)
                  and isinstance(node.value, ast.Name) and node.value.id == "pack"
                  and isinstance(node.slice, ast.Constant)]
    check("_build registers one widget per stripe and no others",
          sorted(registered), sorted(C.STRIPES))


# ---------------------------------------------------------------- the lines

class Blank:
    """A label that remembers what it was told instead of drawing it."""

    def __init__(self):
        self.text = ""

    def configure(self, **kw):
        self.text = kw.get("text", self.text)

    def cget(self, name):
        return self.text


class Page:
    """A Text widget that keeps what was written to it."""

    def __init__(self):
        self.written = []

    def configure(self, **kw):
        pass

    def delete(self, *where):
        self.written = []

    def insert(self, where, text, *tags):
        self.written.append(text)

    def see(self, *where):
        pass


def viewer():
    """Enough of App to render a frame against. Every method run on it is
    App's own; only the widgets and the arrow are stand-ins."""
    app = types.SimpleNamespace(
        my_colour=None, flipped=False, region=None, coach=None,
        coach_fen=None, arrow_cleared=None, note_until=0.0,
        lbl_status=Blank(), lbl_result=Blank(), lbl_board=Blank(),
        lbl_check=Blank(), lbl_coach=Blank(), lbl_detail=Blank(),
        lbl_file=Blank(), moves_box=Page(), board_box=Page(),
        show_board=types.SimpleNamespace(get=lambda: False),
        coach_on=types.SimpleNamespace(get=lambda: False))
    app._hide_arrow = lambda: None
    app._sync_arrow = lambda: None
    app._render = lambda f: C.App._render(app, f)
    return app


def frame(**changes):
    f = {"event": None, "region": None, "locked": True, "rows": [], "count": 0,
         "color": "white", "result": "*", "termination": "", "outcome": None,
         "path": None, "saved": 0, "joined": False, "board": "", "fen": None,
         "flipped": False, "check": "", "templates": "bundled", "sheet": None}
    f.update(changes)
    return f


def the_lines():
    print("\n-- what the lines say ------------------------------------")
    app = viewer()
    app._render(frame(locked=True))
    check("watching a game is one word", app.lbl_status.text, "recording")
    app._render(frame(locked=False))
    check("  and so is not having one yet", app.lbl_status.text, "waiting")

    # The row has about fifteen characters beside the two buttons at 400px and
    # 150% scaling, which is why this is a word and not a sentence.
    words = set()
    for locked in (True, False):
        app._render(frame(locked=locked))
        words.add(app.lbl_status.text)
    check("  neither of them is a phrase",
          [word for word in words if " " in word], [])

    app = viewer()
    app._render(frame(check="position confirmed"))
    check("the checker finding nothing to say puts nothing on the note line",
          app.lbl_check.text, "")

    app._render(frame(check="board unclear"))
    check("a complaint does go up", app.lbl_check.text, "board unclear")
    was = app.note_until
    app._render(frame(check="board unclear"))
    check("  and keeps its place while it is still true",
          app.note_until >= was, True)

    app = viewer()
    app.lbl_check.configure(text="rechecking every square")
    app.note_until = time.time() + C.NOTE_SECONDS
    app._render(frame(check="position confirmed"))
    check("a receipt for something you clicked is left alone",
          app.lbl_check.text, "rechecking every square")

    app = viewer()
    app._render(frame(region=(100, 200, 824, 824), templates="learned"))
    check("the drawer line says where the board is and what is reading it",
          app.lbl_board.text, "board 824x824 at 100,200   pieces learned")
    app._render(frame(region=None, templates=""))
    check("  and says so when there is no board",
          app.lbl_board.text, "no board on screen")


# ---------------------------------------------------------------- the widths

# Tk turns a positive point size into pixels through `tk scaling`, which on
# Windows is the display dpi over 72. The app calls SetProcessDpiAwareness(2),
# so the font grows with the display scaling while minsize does not, and 150%
# is where a row that fits on the machine it was written on stops fitting.
DPI = {"100%": 96, "150%": 144}

# Chrome drawn round the text of each kind of widget, low and high. Bounded
# rather than measured, because measuring it needs a window. Only the high end
# is load bearing, since that is the one that has to fit. Same bounds
# enrolltest.py uses, for the same reason.
CHROME = {"btn": (10, 24), "lbl": (0, 6), "check": (18, 30), "radio": (18, 30)}

FONTS = {False: "C:/Windows/Fonts/segoeui.ttf",
         True: "C:/Windows/Fonts/segoeuib.ttf"}

# minsize is 400 wide and every row is packed with padx=12 a side.
AVAILABLE = 400 - 24

# The rows as _build packs them: kind, text, point size, bold, and the pixels
# of padx asked for at 100%. This mirrors _build rather than reading it, so a
# widget added there has to be added here too.
ROWS = {
    "top": [("btn", "Stop", 10, True, 24),
            ("btn", "Setup", 9, False, 16),
            ("lbl", "recording", 9, False, 16)],
    "hero": [("lbl", "their move  Qa1xd4#", 14, True, 0),
             ("btn", "clear", 8, False, 14)],
    "drawer side": [("group", "Side", 8, False, 0),
                    ("radio", "auto", 8, False, 0),
                    ("radio", "white", 8, False, 0),
                    ("radio", "black", 8, False, 0)],
    "drawer show": [("group", "Show", 8, False, 0),
                    ("check", "Coach", 8, False, 0),
                    ("check", "Arrow", 8, False, 0),
                    ("check", "Position", 8, False, 0)],
    "drawer board": [("group", "Board", 8, False, 0),
                     ("btn", "Pick", 8, False, 6),
                     ("btn", "Pieces", 8, False, 6),
                     ("btn", "Recheck", 8, False, 6)],
}

# The drawer's group labels are given width=7, which Tk reads as seven times
# the width of "0" in the label's font.
GROUP_CHARS = 7


def text_px(text, points, dpi, bold):
    from PIL import ImageFont
    font = ImageFont.truetype(FONTS[bold], round(points * dpi / 72.0))
    box = font.getbbox(text)
    return box[2] - box[0]


def row_px(items, dpi):
    """How wide a row of widgets is, low bound and high bound."""
    low = high = 0
    for kind, text, points, bold, padx in items:
        scale = round(points * dpi / 72.0) / round(points * 96 / 72.0)
        width = text_px(text, points, dpi, bold)
        if kind == "group":
            width = max(width, GROUP_CHARS * text_px("0", points, dpi, bold))
            kind = "lbl"
        low += width + (CHROME[kind][0] + padx) * scale
        high += width + (CHROME[kind][1] + padx) * scale
    return round(low), round(high)


def widths():
    print("\n-- the rows fit the narrowest window ---------------------")
    if not all(os.path.exists(path) for path in FONTS.values()):
        print("SKIP  no Segoe UI on this machine, cannot measure the rows")
        return
    for label, dpi in DPI.items():
        for name, items in ROWS.items():
            low, high = row_px(items, dpi)
            print("      %-13s at %-4s %3d to %3d px of %d"
                  % (name, label, low, high, AVAILABLE))
    for label, dpi in DPI.items():
        for name, items in ROWS.items():
            check("%s fits at %s scaling" % (name, label),
                  row_px(items, dpi)[1] <= AVAILABLE, True)

    # The top row is the one with something stretchy on it, so what has to fit
    # is the two buttons. A status word too long for what is left clips its own
    # tail; a button that does not fit is not drawn at all.
    buttons = [item for item in ROWS["top"] if item[0] == "btn"]
    check("the two buttons alone leave room for a status word at 150%",
          AVAILABLE - row_px(buttons, DPI["150%"])[1]
          >= text_px("recording", 9, DPI["150%"], False), True)


def main():
    print("mode: headless, and checked. tkinter.Tk and tkinter.Toplevel are"
          " replaced\n      with functions that raise before chesswatch is"
          " imported.\n")
    nothing_opens()
    what_shows()
    the_packer()
    every_stripe_is_built()
    the_lines()
    widths()
    print("\n%d/%d passed" % (sum(bool(x) for x in R), len(R)))
    return 0 if all(R) else 1


if __name__ == "__main__":
    sys.exit(main())
