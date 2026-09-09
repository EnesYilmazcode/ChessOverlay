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
  the rows         read off _build's syntax tree, then measured against the
                   real Segoe UI at the pixel size Tk derives from the point
                   size, at 100% and at 150% display scaling

The rows are read rather than listed here on purpose. A table of widgets and
labels written into this file would measure this file: add a fourth button to
a drawer row and every check would stay green while the fourth one went
undrawn, which is the one thing issue #28 says must not happen. build_rows()
refuses a widget written in a shape it cannot read rather than skipping it.

What is NOT checked here: how any of it looks. Colours, spacing, whether the
move is large enough to read across a room. Those need a screen and a person.
"""

import ast
import inspect
import os
import queue
import sys
import textwrap
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

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"

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

QUIET = {"setup": False, "advice": "", "detail": "", "mistake": "",
         "note": "", "result": "", "arrow": False, "side": "auto",
         "position": False}


def with_(**changes):
    state = dict(QUIET)
    state.update(changes)
    return state


def unsettled_note():
    """The sentence the watcher returns while it cannot tell which way up the
    board is, read out of watcher.py rather than typed here.

    The prompt is keyed off the note text, so the two have to be checked
    against each other. Reworded upstream and copied here, the prompt would
    stop appearing and every check below would stay green.
    """
    tree = ast.parse(inspect.getsource(C.W))
    return [node.value.value for node in ast.walk(tree)
            if isinstance(node, ast.Return)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            and C.UNSETTLED in node.value.value]


def waiting_note():
    """That sentence on its own. Falls back to the marker so a run that has
    already failed the check above carries on rather than stopping on an
    index error."""
    written = unsettled_note()
    return written[0] if written else C.UNSETTLED


def state_keys():
    """Every key showing() reads out of the state it is handed."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(C.showing)))
    keys = set()
    for node in ast.walk(tree):
        owner = value = None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" and node.args:
            owner, value = node.func.value, node.args[0]
        elif isinstance(node, ast.Subscript):
            owner, value = node.value, node.slice
        if isinstance(owner, ast.Name) and owner.id == "state":
            keys.add(_const(value, {}))
    return keys - {None}


def what_shows():
    print("\n-- what the window puts up -------------------------------")
    check("a game in progress with nothing to say is three stripes",
          C.showing(QUIET), ("top", "foot", "moves"))

    check("advice brings up the move line",
          C.showing(with_(advice="your move  Nf3")),
          ("top", "hero", "advice", "foot", "moves"))
    check("  the small print under it only when there is any",
          C.showing(with_(advice="your move  Nf3", detail="knight: g1 to f3")),
          ("top", "hero", "advice", "detail", "foot", "moves"))
    check("  and the small print never on its own",
          "detail" in C.showing(with_(detail="knight: g1 to f3")), False)
    check("the move to avoid comes up under the detail line",
          C.showing(with_(advice="your move  Nf3", detail="knight: g1 to f3",
                          mistake="not Nh3, 1.0 worse")),
          ("top", "hero", "advice", "detail", "mistake", "foot", "moves"))
    check("  and does not need the detail line to be up",
          C.showing(with_(advice="your move  Nf3",
                          mistake="not Nh3, 1.0 worse")),
          ("top", "hero", "advice", "mistake", "foot", "moves"))
    check("  but is nothing on its own either, since it is only a warning"
          " beside the move it is a warning against",
          "mistake" in C.showing(with_(mistake="not Nh3, 1.0 worse")), False)
    check("  and it is only there for about half of positions, so the row"
          " comes down when there is nothing to say",
          "mistake" in C.showing(with_(advice="your move  Nf3")), False)

    check("the clear button waits for an arrow to clear",
          "clear" in C.showing(with_(advice="your move  Nf3")), False)
    check("  and turns up when one is drawn",
          C.showing(with_(advice="your move  Nf3", arrow=True)),
          ("top", "hero", "clear", "advice", "foot", "moves"))
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
          ("top", "setup", "hero", "advice", "foot", "moves"))

    check("the position copy is off unless it was switched on",
          C.showing(with_(position=True)),
          ("top", "foot", "position", "moves"))

    everything = with_(setup=True, advice="your move  Nf3", detail="knight",
                       mistake="not Nh3, 1.0 worse", note="board unclear",
                       result="You won", arrow=True, position=True)
    check("with the drawer open, nothing is missing and nothing is doubled",
          C.showing(everything),
          tuple(name for name in C.STRIPES if name != "side"))
    check("  and the side prompt is the one stripe the drawer stands in for",
          C.showing(dict(everything, setup=False, note=C.UNSETTLED)),
          tuple(name for name in C.STRIPES if name != "setup"))


def the_side_prompt():
    print("\n-- the side control while the board's orientation is open ")
    check("the watcher returns exactly one note the prompt answers",
          len(unsettled_note()), 1)
    waiting = waiting_note()
    print("      keyed off %r" % waiting)

    check("an ordinary note brings up no control",
          "side" in C.showing(with_(note="board unclear")), False)
    check("the one that says it is waiting to be told does",
          C.showing(with_(note=waiting)),
          ("top", "note", "side", "foot", "moves"))
    check("  under the note that asks for it",
          C.STRIPES[C.STRIPES.index("note") + 1], "side")
    check("  and not once a side has been picked",
          [side for side in ("white", "black")
           if "side" in C.showing(with_(note=waiting, side=side))], [])
    check("  nor while the drawer is showing the same control",
          "side" in C.showing(with_(note=waiting, setup=True)), False)
    check("  nor at rest, which is what keeps it from being a permanent row",
          "side" in C.showing(QUIET), False)

    app = types.SimpleNamespace(
        setup_open=False, arrow_drawn=False, lbl_coach=Blank(),
        lbl_detail=Blank(), lbl_mistake=Blank(), lbl_check=Blank(),
        lbl_result=Blank(),
        show_board=types.SimpleNamespace(get=lambda: False),
        colour_choice=types.SimpleNamespace(get=lambda: "auto"))
    app.lbl_check.configure(text=waiting)
    check("the window reads the side back off the control it is set with",
          C.App._state(app).get("side"), "auto")
    check("  and hands showing() every value it reads",
          sorted(state_keys() - set(C.App._state(app))), [])
    check("  as does the state the checks above are written against",
          sorted(state_keys() - set(QUIET)), [])
    check("so the real state a waiting window is in puts the prompt up",
          "side" in C.showing(C.App._state(app)), True)


# ---------------------------------------------------------------- the packer

class Slate:
    """A widget that writes down what was done to it instead of drawing."""

    def __init__(self, name, log):
        self.name = name
        self.log = log

    def pack(self, **how):
        self.log.append(("pack", self.name, how))

    def pack_forget(self):
        self.log.append(("forget", self.name, {}))


def packer(state):
    """App._relayout with slates for widgets. Everything it runs is App's.

    Each stripe is given its own distinguishable pack options, so passing the
    wrong stripe's options through would show up as well as dropping them.
    """
    log = []
    app = types.SimpleNamespace(
        _shown=(),
        _pack={name: (Slate(name, log), {"padx": i})
               for i, name in enumerate(C.STRIPES)})
    app._state = lambda: state
    app._relayout = lambda: C.App._relayout(app)
    return app, log


def the_packer():
    print("\n-- the packer does what showing() asked for --------------")
    state = with_(advice="your move  Nf3", arrow=True, note="board unclear")
    app, log = packer(state)
    app._relayout()

    packed = tuple(name for what, name, how in log if what == "pack")
    check("it packs exactly the stripes that were asked for",
          packed, C.showing(state))
    check("  in the order they are meant to be packed in",
          packed, tuple(n for n in C.STRIPES if n in packed))
    check("  after unpacking every stripe, so no stale one is left up",
          sorted(name for what, name, how in log if what == "forget"),
          sorted(C.STRIPES))
    check("  and the two halves of the move row in the order they must go",
          packed.index("clear") < packed.index("advice"), True)
    check("  each with its own pack options and not another's",
          [how for what, name, how in log if what == "pack"],
          [{"padx": C.STRIPES.index(name)} for name in packed])

    log[:] = []
    app._relayout()
    check("running again on the same state touches nothing", log, [])

    state["note"] = ""
    log[:] = []
    app._relayout()
    check("a note going blank takes its stripe down",
          [name for what, name, how in log if what == "pack" and name == "note"],
          [])
    check("  and leaves the rest up",
          tuple(name for what, name, how in log if what == "pack"),
          C.showing(state))

    app, log = packer(with_(note=waiting_note()))
    app._relayout()
    packed = tuple(name for what, name, how in log if what == "pack")
    check("the side prompt is packed straight under the note it answers",
          packed[packed.index("note") + 1], "side")


# ------------------------------------------------------- reading the widgets

# Point size to pixels the way Tk does it: `tk scaling` is the display dpi over
# 72. The app calls SetProcessDpiAwareness(2), so the font grows with the
# display scaling while minsize does not, and 150% is where a row that fits on
# the machine it was written on stops fitting.
DPI = {"100%": 96, "150%": 144}

# Chrome drawn round the text of each kind of widget, low and high. Bounded
# rather than measured, because measuring it needs a window. Only the high end
# is load bearing, since that is the one that has to fit.
CHROME = {"btn": (10, 24), "lbl": (0, 6), "check": (18, 30), "radio": (18, 30)}

KINDS = {"Button": "btn", "Label": "lbl", "Checkbutton": "check",
         "Radiobutton": "radio"}

FONTS = {False: "C:/Windows/Fonts/segoeui.ttf",
         True: "C:/Windows/Fonts/segoeuib.ttf"}

# How much width a row has, read out of the app rather than written down: the
# narrowest window it allows, less the padding its stripes are packed with.
def available():
    floor = window_floor()[0]
    return floor - 2 * build_rows()["*stripes*"]["top"]["padx"]

# Two labels are sized by what the app puts in them, not by what _build starts
# them at. The longest SAN there is has a piece, both source coordinates, a
# capture, a destination and a mark; the move line also has a wraplength, so a
# longer one folds rather than pushing the clear button off the row.
WORST = {"lbl_coach": "your move  Qa1xd4#"}


class Unreadable(Exception):
    """A widget written in a shape build_rows() cannot read. Raised rather
    than skipped: a widget silently left out of a row is exactly the bug the
    measuring is here to catch."""


def _const(node, env):
    """The value of an expression in _build, or None if it is not one this can
    work out. Between them these are the shapes _build writes: a literal, a
    loop variable or one bound from one, a value reached through an import
    alias, and a % format of any of those. Anything else comes back None and
    the caller refuses rather than guessing.
    """
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id in vars(C)):
        return getattr(vars(C)[node.value.id], node.attr, None)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        left, right = _const(node.left, env), _const(node.right, env)
        if isinstance(left, str) and right is not None:
            return left % right
    return None


def _for_values(node, env):
    """The elements a for loop in _build runs over, as nodes."""
    if isinstance(node, (ast.Tuple, ast.List)):
        return list(node.elts)
    values = _const(node, env)
    if isinstance(values, (tuple, list)):
        return [ast.Constant(value=value) for value in values]
    raise Unreadable("a loop over something this cannot read")


def _kw(call, name):
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _font(call):
    """(points, bold) off a font=("Segoe UI", 9, "bold") keyword."""
    node = _kw(call, "font")
    if not isinstance(node, ast.Tuple) or len(node.elts) < 2:
        raise Unreadable("no readable font on %s" % ast.dump(call)[:60])
    points = _const(node.elts[1], {})
    bold = any(_const(e, {}) == "bold" for e in node.elts[2:])
    return points, bold


def _padx(node):
    """Horizontal padding in pixels, from an int or a (left, right) pair."""
    if node is None:
        return 0
    if isinstance(node, ast.Constant):
        return 2 * node.value
    if isinstance(node, ast.Tuple):
        return sum(_const(e, {}) or 0 for e in node.elts)
    raise Unreadable("padx is neither a number nor a pair")


def _options(call):
    """The constant keywords of a pack() call, or of the dict() in a stripe's
    pack table entry."""
    return {k.arg: _const(k.value, {}) for k in call.keywords
            if k.arg is not None}


def _widget(call, env, name=None):
    kind = KINDS[call.func.attr]
    points, bold = _font(call)
    width = _const(_kw(call, "width"), env) if _kw(call, "width") else None
    text = _const(_kw(call, "text"), env)
    if text is None:
        raise Unreadable("no readable text on a %s" % kind)
    return {"kind": kind, "text": WORST.get(name, text), "points": points,
            "bold": bold, "chars": width, "wpadx": _padx(_kw(call, "padx")),
            "ppadx": 0, "pack": {}, "name": name}


def _helper(method, text, env):
    """A widget built by _drawer_row or _switch, read out of that helper."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in KINDS):
            widget = _widget(node, {"name": text, "text": text})
            widget["ppadx"] = 0
            return widget
    raise Unreadable("%s builds no widget" % method.__name__)


def _tk_calls(tree):
    """Every widget built on a named row, whatever its type. Deliberately not
    filtered by KINDS: a widget of a type this file does not know about is the
    case worth catching, not the case worth ignoring."""
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name) and n.func.value.id == "tk"
            and n.args and isinstance(n.args[0], ast.Name)]


def build_rows():
    """The rows of the window, read off App._build rather than copied here.

    Returns {row name: [widget, ...]} in the order they are packed, plus the
    stripe pack options under the key "*stripes*".
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(C.App._build)))
    rows, attrs, stripes, seen = {}, {}, {}, []

    def widget_of(call, env, name=None):
        parent = call.args[0].id if call.args and isinstance(call.args[0], ast.Name) else None
        if parent not in rows:
            return None
        seen.append(call)
        widget = _widget(call, env, name)
        rows[parent].append(widget)
        return widget

    def statement(node, env):
        if isinstance(node, ast.For):
            for element in _for_values(node.iter, env):
                names = ([node.target] if isinstance(node.target, ast.Name)
                         else node.target.elts)
                values = ([element] if not isinstance(element, ast.Tuple)
                          else element.elts)
                inner = dict(env)
                for target, value in zip(names, values):
                    got = _const(value, env)
                    if got is not None:
                        inner[target.id] = got
                for body in node.body:
                    statement(body, inner)
            return

        if isinstance(node, ast.Assign):
            value, target = node.value, node.targets[0]
            if (isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "pack"):
                # pack["name"] = (widget, dict(...))
                stripe = _const(target.slice, {})
                stripes[stripe] = _options(value.elts[1])
                held = value.elts[0]
                if (isinstance(held, ast.Attribute) and held.attr in attrs):
                    attrs[held.attr]["pack"] = stripes[stripe]
                    attrs[held.attr]["ppadx"] = _padx(_kw(value.elts[1], "padx"))
                return
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute):
                if value.func.attr == "Frame" and isinstance(target, ast.Name):
                    rows[target.id] = []
                    return
                if value.func.attr == "_drawer_row" and isinstance(target, ast.Name):
                    rows[target.id] = [_helper(C.App._drawer_row,
                                               _const(value.args[1], env), env)]
                    return
                if value.func.attr in KINDS:
                    name = target.attr if isinstance(target, ast.Attribute) else None
                    made = widget_of(value, env, name)
                    if made is not None and name:
                        attrs[name] = made
                    return
            # A plain local, so a label worked out once and used twice reads
            # the same as one written out at the widget.
            if isinstance(target, ast.Name):
                got = _const(value, env)
                if got is not None:
                    env[target.id] = got
            return

        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if not isinstance(call.func, ast.Attribute):
                return
            if call.func.attr == "_switch":
                row = call.args[0].id
                if row in rows:
                    rows[row].append(_helper(C.App._switch,
                                             _const(call.args[1], env), env))
                return
            if call.func.attr == "pack":
                inner = call.func.value
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) \
                        and inner.func.attr in KINDS:
                    made = widget_of(inner, env)          # tk.X(...).pack(...)
                    if made is None:
                        return
                elif isinstance(inner, ast.Attribute) and inner.attr in attrs:
                    made = attrs[inner.attr]              # self.x.pack(...)
                else:
                    return
                made["pack"] = _options(call)
                made["ppadx"] = _padx(_kw(call, "padx"))
            return

    for node in tree.body[0].body:
        statement(node, {})

    missed = [n for n in _tk_calls(tree)
              if n.args[0].id in rows and n not in seen]
    if missed:
        raise Unreadable("%d widget(s) in _build were not read" % len(missed))
    rows["*stripes*"] = stripes
    return rows


def window_floor():
    """The smallest window the app allows, read off App.__init__."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(C.App.__init__)))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "minsize"):
            return tuple(_const(arg, {}) for arg in node.args)
    raise Unreadable("App.__init__ sets no minsize")


def labelled_rows():
    """The rows built by _drawer_row, the drawer's four and the side prompt,
    counted straight off the calls rather than off build_rows' own
    bookkeeping, so a row build_rows stopped seeing would show up as a row
    that never got measured."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(C.App._build)))
    return [_const(node.args[1], {}) for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_drawer_row"]


def _cfg_gets(method):
    """Every self.cfg.get(...) call in a method, as nodes."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and node.args
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "cfg"]


def config_keys(method):
    """The config.json keys a method reads back, or the ones it writes if it
    is the one that writes the file. A key saved and never read again is a
    setting that quietly stops being remembered, which is what this is for."""
    if method is C.App._save_config:
        tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
        return {_const(key, {}) for node in ast.walk(tree)
                if isinstance(node, ast.Dict)
                for key in node.keys} - {None}
    return {_const(node.args[0], {}) for node in _cfg_gets(method)} - {None}


def switch_defaults():
    """What each setting falls back to on a machine with no config.json, read
    off the app rather than written down here."""
    out = {}
    for method in (C.App.__init__, C.App._build):
        for node in _cfg_gets(method):
            if len(node.args) == 2:
                out[_const(node.args[0], {})] = _const(node.args[1], {})
    return out


def status_words():
    """Every word the top row can be set to, read out of the app."""
    words = set()
    for member in vars(C.App).values():
        try:
            source = textwrap.dedent(inspect.getsource(member))
        except (TypeError, OSError):
            continue
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if (node.func.attr == "configure" and isinstance(owner, ast.Attribute)
                    and owner.attr == "lbl_status"):
                text = _const(_kw(node, "text"), {})
                if isinstance(text, str):
                    words.add(text)
    return words


# ---------------------------------------------------------------- the lines

class Blank:
    """A label that remembers what it was told instead of drawing it."""

    def __init__(self):
        self.text = ""
        self.colour = None

    def configure(self, **kw):
        self.text = kw.get("text", self.text)
        self.colour = kw.get("fg", self.colour)

    def cget(self, name):
        return self.text


class Switch:
    """A BooleanVar that remembers instead of talking to Tk."""

    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


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
        my_colour=None, flipped=False, region=None, coach=None, worker=None,
        coach_fen=None, arrow_cleared=None, note_until=0.0,
        arrow_bad_uci=None, arrow_bad_fen=None,
        lbl_status=Blank(), lbl_result=Blank(), lbl_board=Blank(),
        lbl_check=Blank(), lbl_coach=Blank(), lbl_detail=Blank(),
        lbl_mistake=Blank(), lbl_file=Blank(), btn=Blank(),
        moves_box=Page(), board_box=Page(),
        show_board=types.SimpleNamespace(get=lambda: False),
        coach_on=types.SimpleNamespace(get=lambda: False))
    app._hide_arrow = lambda: None
    app._sync_arrow = lambda: None
    app._render = lambda f: C.App._render(app, f)
    app._stop = lambda: C.App._stop(app)
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

    app._render(frame(locked=False))
    check("nothing before a game starts is a paragraph",
          [line for line in app.moves_box.written if "\n" in line], [])
    check("  and it is one line, not three",
          len(app.moves_box.written), 1)

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


def whose_turn():
    print("\n-- nothing is asked while it is not your move -------------")
    check("your own turn is a turn", C.my_turn(START_FEN, "white"), True)
    check("  and the same position is not, playing black",
          C.my_turn(START_FEN, "black"), False)
    check("  which way round follows the side you play",
          (C.my_turn(AFTER_E4, "black"), C.my_turn(AFTER_E4, "white")),
          (True, False))
    check("not knowing your colour is not a turn either",
          [C.my_turn(START_FEN, c) for c in (None, "", "auto")],
          [False, False, False])
    check("  nor is having no position at all",
          [C.my_turn(f, "white") for f in (None, "", "rubbish")],
          [False, False, False])

    def rendered(fen, colour, result="*"):
        """The real _render, with a coach that writes down what it was asked."""
        app = viewer()
        asked = []
        app.coach = types.SimpleNamespace(out=queue.Queue(),
                                          ask=lambda f: asked.append(f))
        app.coach_on = types.SimpleNamespace(get=lambda: True)
        app.my_colour = colour
        app._render(frame(fen=fen, color=colour, result=result,
                          region=(0, 0, 400, 400)))
        return asked, app.lbl_coach.text, app.coach_fen

    asked, line, held = rendered(START_FEN, "white")
    check("your own turn is asked about", (asked, held),
          ([START_FEN], START_FEN))
    check("  and the move line is left for the answer to fill in", line, "")

    asked, line, held = rendered(START_FEN, "black")
    check("their turn is not asked about at all", (asked, held), ([], None))
    check("  and the row stays up saying so, rather than the window changing"
          " height twice a move", line, "their turn")

    asked, line, held = rendered(START_FEN, None)
    check("nor is a board whose orientation has not settled",
          (asked, held), ([], None))
    asked, line, held = rendered(START_FEN, "white", result="1-0")
    check("nor a game that is over", (asked, line, held), ([], "", None))


def coached(turn="white", final=True, over=False):
    """App._drain_coach with one answer waiting on the queue. Returns the app
    and the arrow it asked for, if it asked for one."""
    app = viewer()
    app.coach = types.SimpleNamespace(out=queue.Queue())
    app.coach_on = types.SimpleNamespace(get=lambda: True)
    app.coach_fen = "the position"
    app.my_colour = "white"
    drawn = []
    app._show_arrow = lambda uci, fen: drawn.append((uci, fen))
    app._show_mistake = lambda uci, fen: drawn.append((uci, fen))
    app.coach.out.put(("advice", {
        "fen": "the position", "over": over, "final": final, "turn": turn,
        "san": "Ra8#", "uci": "a1a8", "text": "rook: a1 to a8, with check",
        "score": "mate in 1"}))
    C.App._drain_coach(app)
    return app, drawn


def the_answer():
    print("\n-- the answer is a headline and its small print ----------")
    app, drawn = coached()
    check("the move line is whose move it is and what to play, and no more",
          app.lbl_coach.text, "your move  Ra8#")
    check("  the naming of the squares and the score are under it",
          app.lbl_detail.text, "rook: a1 to a8, with check   mate in 1")
    check("  and the arrow is asked for",
          drawn, [("a1a8", "the position")])

    # Both arrows are about a move you are the one to make, so an answer for
    # the opponent's side is one the board has moved past. Drawing it was the
    # bug: the opponent's plan on your board, over your own turn.
    app, drawn = coached(turn="black")
    check("an answer for the opponent's side is dropped, arrow and all",
          (app.lbl_coach.text, drawn), ("", []))

    app, drawn = coached(final=False)
    check("an answer the engine has not finished with is marked",
          app.lbl_detail.text.endswith("   ..."), True)
    check("  on the small print, leaving the move line the width it was",
          app.lbl_coach.text, "your move  Ra8#")

    app, drawn = coached(over=True)
    check("a finished game says so and shows no small print",
          (app.lbl_coach.text, app.lbl_detail.text, drawn),
          ("the game is over", "", []))

    print("\n-- and the warning that lands after it --------------------")
    # The second answer is about the same position and turns up a second or so
    # later, so the row it fills has to come down again by itself. Nothing else
    # would take it down: for half of all positions there is no second answer
    # to replace it with.
    app, _ = coached()
    app.coach.out.put(("mistake", {
        "fen": "the position", "turn": "white", "san": "Nh3",
        "uci": "g1h3", "text": "knight: g1 to h3", "worse": "1.0 worse",
        "drop": 100}))
    C.App._drain_coach(app)
    check("the warning is one line, in the words of the side it is for",
          app.lbl_mistake.text, "not Nh3, 1.0 worse")
    check("  and the move to play is untouched above it",
          app.lbl_coach.text, "your move  Ra8#")

    app.coach_fen = "the next position"
    app.coach.out.put(("advice", {
        "fen": "the next position", "over": False, "final": True,
        "turn": "white", "san": "e4", "uci": "e2e4", "text": "pawn: e2 to e4",
        "score": "+0.3"}))
    C.App._drain_coach(app)
    check("advice about the next position takes the old warning down with it",
          (app.lbl_coach.text, app.lbl_mistake.text), ("your move  e4", ""))

    app, _ = coached()
    app.coach.out.put(("mistake", {
        "fen": "some other position", "turn": "white", "san": "Nh3",
        "uci": "g1h3", "text": "knight: g1 to h3", "worse": "1.0 worse",
        "drop": 100}))
    C.App._drain_coach(app)
    check("a warning about a position already played past is not shown at all",
          app.lbl_mistake.text, "")

    app, _ = coached(over=True)
    check("nor is one left up over a finished game", app.lbl_mistake.text, "")

    app = viewer()
    app.coach = types.SimpleNamespace(out=queue.Queue())
    app.coach_on = types.SimpleNamespace(get=lambda: True, set=lambda v: None)
    app.coach.out.put(("engine", "Stockfish would not start: no such file"))
    C.App._drain_coach(app)
    check("an engine that will not start is a note, not a headline",
          (app.lbl_coach.text, app.lbl_check.text.startswith("Stockfish")),
          ("", True))


class Engine:
    """A Coach that starts no process."""

    def __init__(self, path, think_seconds=None):
        self.path = path
        self.think_seconds = think_seconds
        self.started = False

    def start(self):
        self.started = True


def switching_on(coach=None, asked=True, engine="stockfish"):
    """App._toggle_coach with a stand-in engine behind it, or none at all.
    Returns the app it ran against, so what it wrote can be read back off the
    labels."""
    app = types.SimpleNamespace(
        coach=coach, note_until=0.0, coach_fen=None,
        coach_on=Switch(True), lbl_coach=Blank(), lbl_detail=Blank(),
        lbl_mistake=Blank(), lbl_check=Blank())
    app._save_config = lambda: None
    app._hide_arrow = lambda: None
    app._think_seconds = lambda: 1.0
    was = C.CO.find_engine, C.CO.Coach
    C.CO.find_engine, C.CO.Coach = (lambda: engine), Engine
    try:
        C.App._toggle_coach(app, asked=asked)
    finally:
        C.CO.find_engine, C.CO.Coach = was
    return app


def the_first_run():
    print("\n-- coaching is on by default, so a first run is one -------")
    saved = config_keys(C.App._save_config)
    read = config_keys(C.App.__init__) | config_keys(C.App._build)
    check("every setting written to config.json is read back out of it",
          (sorted(saved - read), sorted(read - saved)), ([], []))
    falls_back = switch_defaults()
    print("      with no config.json: %s"
          % "  ".join("%s %s" % pair for pair in sorted(falls_back.items())))
    check("coaching is what a machine with no config.json starts with",
          falls_back["coach"], True)
    check("  and the arrow and the board copy are not, since both draw",
          (falls_back["arrow"], falls_back["position"]), (False, False))

    app = switching_on(asked=False, engine=None)
    check("no Stockfish leaves the switch off and no engine behind it",
          (app.coach, app.coach_on.get()), (None, False))
    check("  the move line empty rather than holding the complaint",
          (app.lbl_coach.text, app.lbl_detail.text), ("", ""))
    check("  and the note saying why, muted, because nobody asked for it",
          ("Stockfish" in app.lbl_check.text, app.lbl_check.colour),
          (True, C.MUTED))
    check("  where clicking it and finding none is still a warning",
          switching_on(engine=None).lbl_check.colour, C.WARN)

    app = switching_on(asked=False)
    check("an engine that is there is started once, at the saved think time",
          (app.coach.started, app.coach.think_seconds), (True, 1.0))
    check("  with the move line left empty until it has an answer",
          app.lbl_coach.text, "")
    check("  where a tick is answered at once, since that is what it is for",
          switching_on(coach=Engine("already running")).lbl_coach.text,
          "thinking...")


def the_filename():
    print("\n-- the filename is the way into the games folder ---------")
    # The path is joined rather than written out: basename splits on the
    # separator of whichever machine the test runs on, and CI is Linux.
    name = "2026-09-05 1830 win.pgn"
    saved = os.path.join("somewhere", "games", name)
    app = viewer()
    app._render(frame(path=saved))
    check("a game being recorded shows its file",
          app.lbl_file.text, "games\\" + name)

    game = types.SimpleNamespace(moves=["e4"], save=lambda: None, path=saved)
    app.worker = types.SimpleNamespace(
        stop_flag=types.SimpleNamespace(set=lambda: None),
        tracker=types.SimpleNamespace(game=game))
    app._stop()
    check("  and stopping leaves it written the same way, not as a sentence",
          app.lbl_file.text, "games\\" + name)
    check("  with the button back to Start", app.btn.text, "Start")

    source = inspect.getsource(C.App._build)
    check("clicking the filename opens the folder",
          "self.lbl_file.bind(\"<Button-1>\"" in source
          and "_open_folder()" in source, True)


def every_stripe_is_built():
    """A stripe with no widget behind it is a KeyError on the first relayout."""
    print("\n-- every stripe has a widget -----------------------------")
    check("_build registers one widget per stripe and no others",
          sorted(build_rows()["*stripes*"]), sorted(C.STRIPES))


# ---------------------------------------------------------------- the widths

def px(points, dpi):
    return round(points * dpi / 72.0)


def text_px(text, points, dpi, bold):
    from PIL import ImageFont
    font = ImageFont.truetype(FONTS[bold], px(points, dpi))
    box = font.getbbox(text)
    return box[2] - box[0]


def row_px(widgets, dpi):
    """How wide a row of widgets is, low bound and high bound."""
    low = high = 0
    for w in widgets:
        scale = px(w["points"], dpi) / px(w["points"], 96)
        wide = text_px(w["text"], w["points"], dpi, w["bold"])
        if w["chars"]:
            # Tk reads a width in text units as that many times the width of
            # "0" in the widget's own font.
            wide = max(wide, w["chars"] * text_px("0", w["points"], dpi, w["bold"]))
        pad = (w["wpadx"] + w["ppadx"]) * scale
        low += wide + CHROME[w["kind"]][0] * scale + pad
        high += wide + CHROME[w["kind"]][1] * scale + pad
    return round(low), round(high)


def have_fonts():
    if all(os.path.exists(path) for path in FONTS.values()):
        return True
    print("SKIP  no Segoe UI on this machine, cannot measure the rows")
    return False


def widths():
    print("\n-- the rows fit the narrowest window ---------------------")
    if not have_fonts():
        return
    room = available()
    print("      the window floor is %dx%d, leaving %d px of row"
          % (window_floor() + (room,)))
    rows = {name: widgets for name, widgets in build_rows().items()
            if name != "*stripes*" and len(widgets) > 1}
    check("every row built with a label word is one of the rows measured",
          len([name for name in rows if name not in ("top", "hero")]),
          len(labelled_rows()))
    check("  along with the top row and the row the move sits on",
          sorted(set(rows) & {"top", "hero"}), ["hero", "top"])
    for label, dpi in DPI.items():
        for name in sorted(rows):
            low, high = row_px(rows[name], dpi)
            print("      %-6s at %-4s %3d to %3d px of %d   %s"
                  % (name, label, low, high, room,
                     " ".join(w["text"] or "-" for w in rows[name])))
    for label, dpi in DPI.items():
        for name in sorted(rows):
            check("the %s row fits at %s scaling" % (name, label),
                  row_px(rows[name], dpi)[1] <= room, True)


def the_status_line():
    print("\n-- every status the top row can show fits beside it ------")
    if not have_fonts():
        return
    top = build_rows()["top"]
    label = [w for w in top if w["name"] == "lbl_status"][0]
    room = available() - row_px([w for w in top if w is not label],
                                DPI["150%"])[1]
    words = status_words() | {label["text"]}
    for word in sorted(words):
        need = row_px([dict(label, text=word)], DPI["150%"])[1]
        print("      %-12s %3d px of %d" % (word, need, room))
    check("every one of them fits at 150%, where the row is tightest",
          [word for word in sorted(words)
           if row_px([dict(label, text=word)], DPI["150%"])[1] > room], [])


def the_pack_order():
    print("\n-- the order the fitting argument rests on ---------------")
    rows = build_rows()
    stripes = rows["*stripes*"]

    top = rows["top"]
    check("the top row is the button, the Setup button, then the status",
          [w["name"] for w in top], ["btn", "btn_setup", "lbl_status"])
    check("  the Setup button is packed right, before the status label",
          top[1]["pack"].get("side"), "right")
    check("  so the status is the stretchy one that loses its own tail",
          (top[2]["pack"].get("side"), top[2]["pack"].get("fill"),
           top[2]["pack"].get("expand")), ("left", "x", True))

    hero = rows["hero"]
    check("the clear button is packed before the move it sits beside",
          [w["name"] for w in hero], ["btn_clear", "lbl_coach"])
    check("  to the right, with the move line taking what is left",
          (hero[0]["pack"].get("side"), hero[1]["pack"].get("side"),
           hero[1]["pack"].get("expand")), ("right", "left", True))

    check("the footer is packed before the move list",
          C.STRIPES.index("foot") < C.STRIPES.index("moves"), True)
    check("  and from the bottom, so a short window shrinks the list instead",
          stripes["foot"].get("side"), "bottom")
    check("the position copy is the same",
          (C.STRIPES.index("position") < C.STRIPES.index("moves"),
           stripes["position"].get("side")), (True, "bottom"))
    check("the move list is packed last and is the one that gives",
          (C.STRIPES[-1], stripes["moves"].get("expand")), ("moves", True))


# ------------------------------------------------------ the region you pick

def paper_desktop(at=(300, 180), size=544, width=1200, height=900):
    """A desktop with one board on it, painted in the two colours find_board
    hunts for. Empty squares only: what snap_region has to get right is where
    the board is, and nothing about that is a piece."""
    from PIL import Image
    desk = Image.new("RGB", (width, height), (0x26, 0x24, 0x21))
    step = size // 8
    for row in range(8):
        for col in range(8):
            here = C.W.LIGHT_SQUARE if (row + col) % 2 == 0 else C.W.DARK_SQUARE
            desk.paste(Image.new("RGB", (step, step), here),
                       (at[0] + col * step, at[1] + row * step))
    return desk, (at[0], at[1], step * 8, step * 8)


def the_picked_region():
    """A rectangle dragged by hand is snapped onto the board inside it.

    Issue #52: a drag is never pixel exact, and the reporter's saved region was
    a 540 box on a 544 board, which cuts every square four pixels off centre
    and cost him six unreadable squares against one. The picker cannot do this
    itself, because its own dimmed window is still over the desktop while it
    runs, so the snap belongs to the two callers and both are checked here.
    """
    print("\n-- the region you picked is snapped onto the board -------")
    desk, board = paper_desktop()
    bounds = (0, 0, desk.size[0], desk.size[1])

    def grabber(region):
        left, top, wide, high = region
        return desk.crop((left, top, left + wide, top + high))

    def snap(region):
        return C.snap_region(region, grabber=grabber, bounds=bounds)

    left, top, size, _ = board
    check("a drag four pixels short of the board still finds it",
          snap((left, top, size - 4, size - 4)), board)
    check("  and one that started two pixels inside it",
          snap((left + 2, top + 2, size, size)), board)
    check("  and one loose around the whole thing",
          snap((left - 15, top - 15, size + 30, size + 30)), board)

    # The reason for the fallback, and the reason the slop is not wider: hand
    # picking exists for a board find_board cannot see, and a rectangle it
    # cannot answer for has to come back exactly as it was dragged.
    empty = (20, 20, 300, 300)
    check("a rectangle with no board in it comes back as it was dragged",
          snap(empty), empty)
    check("  and so does one the grab itself fails on",
          C.snap_region(empty, grabber=_forbid, bounds=bounds), empty)
    check("  and nothing is snapped when no region is held at all",
          snap(None), None)

    # snap_region is only worth anything if the two callers reach it, and
    # neither can be driven here: one needs a screen and the other a worker.
    # So they are read instead.
    calls = {name: [node.func.id
                    for node in ast.walk(ast.parse(textwrap.dedent(
                        inspect.getsource(getattr(C.App, name)))))
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)]
             for name in ("_pick", "_start")}
    check("picking a region snaps what was dragged",
          "snap_region" in calls["_pick"], True)
    check("  and starting snaps the one already saved, which is his",
          "snap_region" in calls["_start"], True)


def unreadable_widgets_are_refused():
    print("\n-- a widget it cannot read is refused, not skipped -------")
    was = KINDS.pop("Button")
    try:
        build_rows()
        raised = False
    except (Unreadable, KeyError):
        raised = True
    finally:
        KINDS["Button"] = was
    check("a widget build_rows cannot account for stops the run", raised, True)


def main():
    print("mode: headless, and checked. tkinter.Tk and tkinter.Toplevel are"
          " replaced\n      with functions that raise before chesswatch is"
          " imported.\n")
    nothing_opens()
    what_shows()
    the_side_prompt()
    the_packer()
    the_picked_region()
    try:
        every_stripe_is_built()
        the_pack_order()
        the_lines()
        whose_turn()
        the_answer()
        the_first_run()
        the_filename()
        widths()
        the_status_line()
        unreadable_widgets_are_refused()
    except Unreadable as exc:
        # Everything below the rows depends on being able to read them, so a
        # widget written in a shape this file cannot account for ends the run
        # rather than being quietly counted as a pass.
        check("every widget in _build can be read (%s)" % exc, False, True)
    print("\n%d/%d passed" % (sum(bool(x) for x in R), len(R)))
    return 0 if all(R) else 1


if __name__ == "__main__":
    sys.exit(main())
