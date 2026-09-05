"""Checks for teaching the pieces by hand, and for the arrow following the
position instead of the last thing the engine said.

Run:  python enrolltest.py

Nothing here opens a window. That is deliberate rather than a limitation: this
app puts an always on top, click through overlay on the real desktop, so a test
that draws one cannot be run on a machine somebody is using. Both features were
written with the decision separated from the widget so that this file can check
the decision.

  overlay.wanted()   the whole arrow staleness rule, no window in it
  enroll.Labels      which square each piece is taken from, no window in it
  enroll.write_sheet the sheet itself, which is only pixels
  Worker._tick       driven for real, with a rendered desktop standing in for
                     the screen, because "the board went away" is worker
                     behaviour and a pure function cannot prove the worker
                     ever reports it

The last section reads the source of chesswatch.py to check a few call sites.
That is a weaker kind of check and it is labelled as one. An earlier version of
this file leaned on it for the board-going-away case and passed while the
behaviour was absent, because the string it matched was on a branch nothing
could reach. Anything that can be driven is driven.

What is NOT checked here: every widget in enroll.py, the arrow really appearing
on screen, and Windows click-through. The first needs a desktop, and the last
two are already what overlaytest.py measures.
"""

import ast
import os
import sys
import queue
import inspect
import shutil
import types
import textwrap
import tempfile

import chess
from PIL import Image

import watcher as W
import pieces as P
import overlay as OV
import enroll as E
from fakeboard import Renderer
from shots import shot

# 1.png is chess.com's own set after 1.e4 c5 2.d4 e6. 6.png is the starting
# position in a different set, the board issue #20 is about.
TRUTH_1 = ["rnbqkbnr", "pp.p.ppp", "....p...", "..p.....",
           "...PP...", "........", "PPP..PPP", "RNBQKBNR"]
TRUTH_6 = ["".join(row) for row in W.grid_of(chess.Board(), False)]

_b = chess.Board()
FEN_A = _b.fen()
_b.push_san("e4")
FEN_B = _b.fen()
_b.push_san("e5")
FEN_C = _b.fen()

R1 = (100, 100, 400, 400)
R2 = (640, 220, 512, 512)

R = []


def check(name, got, want):
    ok = got == want
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        print("        got  ", got)
        print("        want ", want)
    R.append(ok)
    return ok


def board_of(name):
    return E.board_of(Image.open(shot(name)).convert("RGB"))


def tally(reader, board_img, truth):
    """Correct, wrong and unknown squares. Unknown is counted apart from wrong
    because a "?" only costs a pass and a wrong piece goes in the record."""
    rows, _ = reader.classify(board_img)
    correct = wrong = unknown = 0
    for r in range(8):
        for c in range(8):
            got, want = rows[r][c], truth[r][c]
            if got == "?":
                unknown += 1
            elif got == want:
                correct += 1
            else:
                wrong += 1
    return correct, wrong, unknown


def slots_from(truth):
    """Where somebody clicking through the board ends up: the first square
    holding each of the twelve types."""
    out = {}
    for r in range(8):
        for c in range(8):
            symbol = truth[r][c]
            if symbol != "." and symbol not in out:
                out[symbol] = (r, c)
    return out


# ---------------------------------------------------------------- the sheet

def sheet():
    print("\n-- the sheet it writes -----------------------------------")
    b1, b6 = board_of("1"), board_of("6")
    tmp = tempfile.mkdtemp()
    try:
        path = E.write_sheet(b1, slots_from(TRUTH_1), os.path.join(tmp, "a.png"))
        check("the sheet is the size make_templates.py writes",
              Image.open(path).size,
              (P.TEMPLATE_PX * len(P.ORDER), P.TEMPLATE_PX))

        taught = P.PieceReader(path)
        check("and the ordinary reader loads it without being told anything",
              taught.ready, True)
        check("a board taught from itself then reads back with nothing wrong",
              tally(taught, b1, TRUTH_1), (64, 0, 0))

        # The case the whole thing is for. On this set the bundled sheet names
        # one piece in thirty-two, which is issue #20. Teaching from the board
        # itself is the only route open to a game already in progress.
        bundled = P.PieceReader()
        base = tally(bundled, b6, TRUTH_6)
        path6 = E.write_sheet(b6, slots_from(TRUTH_6),
                              os.path.join(tmp, "b.png"))
        got = tally(P.PieceReader(path6), b6, TRUTH_6)
        print("      6.png, a set the bundled sheet has never seen:")
        print("        bundled  correct %d  wrong %d  unknown %d" % base)
        print("        taught   correct %d  wrong %d  unknown %d" % got)
        check("  teaching reads at least the 51 squares of 6.png it read here",
              got[0] >= 51, True)
        check("  and names nothing wrong doing it", got[1], 0)
        check("  which beats the 33 the bundled sheet managed",
              got[0] > base[0], True)

        # A slot left black is not a sheet with a hole in it, it is a template
        # that matches every square, so it has to be refused before it is
        # written rather than found later.
        short = dict(slots_from(TRUTH_1))
        short.pop("q")
        short.pop("K")
        failed = ""
        try:
            E.write_sheet(b1, short, os.path.join(tmp, "c.png"))
        except ValueError as exc:
            failed = str(exc)
        check("a sheet missing a piece is refused, not written",
              failed, "nothing taught for K q")
        check("  and no file is left behind",
              os.path.exists(os.path.join(tmp, "c.png")), False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------- what it already knows

def knows():
    print("\n-- what it shows you it already knows --------------------")
    boards = {"1": board_of("1"), "6": board_of("6")}
    truths = {"1": TRUTH_1, "6": TRUTH_6}
    bundled = P.PieceReader()

    # Both boards, not just the readable one. classify() is where the trust
    # gate lives and the gate only bites on a foreign set, so a check that runs
    # on 1.png alone would stay green while the two answers diverged on exactly
    # the boards this tool exists for.
    for name, board in boards.items():
        scored = E.beliefs(bundled, board)
        rows, _ = bundled.classify(board)
        check("on %s.png every letter shown is the one classify() gives" % name,
              [[s for s, _ in row] for row in scored],
              [list(row) for row in rows])
        named = {(r, c) for r in range(8) for c in range(8)
                 if rows[r][c] not in (".", "?")}
        seeded = E.seed_slots(scored)
        check("  and no square classify() refused is seeded",
              [rc for rc in seeded.values() if rc not in named], [])
        right = sum(1 for s, (r, c) in seeded.items() if truths[name][r][c] == s)
        print("      %s.png: proposes %d of 12 squares, %d of them right"
              % (name, len(seeded), right))
        check("  and every square it does propose holds that piece",
              right, len(seeded))

    check("on a set it can read there is nothing left to click",
          len(E.seed_slots(E.beliefs(bundled, boards["1"]))), 12)
    check("on the set from issue #20 it proposes almost nothing",
          len(E.seed_slots(E.beliefs(bundled, boards["6"]))) < 4, True)

    # A build of pieces.py with no per square score to give must show no
    # number, not a made up one. A hard coded 1.00 rendered where a
    # measurement belongs is the worst of the available failures.
    real = E._scores
    E._scores = lambda reader, img: None
    try:
        scored = E.beliefs(bundled, boards["1"])
        check("with no scorer available, no square shows a number",
              {v for row in scored for _, v in row}, {None})
        check("  and the letters are still classify()'s",
              [[s for s, _ in row] for row in scored],
              [list(row) for row in bundled.classify(boards["1"])[0]])
        check("  and all twelve are still seeded, first square winning",
              len(E.seed_slots(scored)), 12)
    finally:
        E._scores = real

    check("a scorer that raises is reported as no scorer, not as a crash",
          E._scores(object(), boards["1"]), None)

    # A reader holding no templates has no beliefs to show, and has to say so
    # rather than fill the board in with guesses.
    empty = P.PieceReader(os.path.join(tempfile.gettempdir(), "no-such.png"))
    empty.templates = {}
    check("a reader that knows nothing seeds nothing",
          E.seed_slots(E.beliefs(empty, boards["1"])), {})


# ---------------------------------------------------------------- the labels

def labels():
    print("\n-- clicking squares and pieces ---------------------------")
    b6 = board_of("6")
    scored = E.beliefs(P.PieceReader(), b6)
    lab = E.Labels(scored)
    seeded = dict(lab.slots)

    lab.square(0, 0)
    check("a square clicked on its own just waits", (lab.sel, lab.pending),
          ((0, 0), None))
    lab.symbol("r")
    check("  and the piece named next is taken from it", lab.slots["r"], (0, 0))
    check("  with nothing left waiting", (lab.sel, lab.pending), (None, None))

    lab.symbol("k")
    check("a piece clicked first waits for a square", lab.pending, "k")
    lab.symbol("k")
    check("  clicking it again puts it back down", lab.pending, None)
    lab.symbol("k")
    lab.square(0, 4)
    check("  and the next square clicked answers it", lab.slots["k"], (0, 4))

    check("the ones it started with are still there where untouched",
          [s for s in seeded if lab.slots.get(s) == seeded[s]],
          [s for s in seeded if s not in ("r", "k")])

    lab.slots.clear()
    check("nothing taught means all twelve are missing",
          "".join(lab.missing()), P.ORDER)
    check("  and the count says so",
          lab.status().startswith("0 of 12 taught"), True)
    for symbol, rc in slots_from(TRUTH_6).items():
        lab.slots[symbol] = rc
    check("all twelve taught leaves nothing missing", lab.missing(), [])
    check("  and says it is ready", lab.status().endswith("ready to save"), True)
    check("two pieces cannot be taken from one square without saying so",
          len(set(lab.chosen())), 12)

    lab.pending = "P"
    check("the hint says what the next click will do",
          lab.hint(), "now click the square holding the white pawn")
    lab.pending = None
    lab.sel = (0, 0)
    check("  and a picked square says what it currently reads",
          lab.hint().startswith("that square reads %s" % scored[0][0][0]), True)
    lab.scored = [[(s, None) for s, _ in row] for row in lab.scored]
    check("  with no invented number when there is none to give",
          lab.hint(), "that square reads %s. Say what it really is."
          % lab.scored[0][0][0])


# ---------------------------------------------------------------- the arrow

def arrow():
    print("\n-- the arrow follows the position, not the last reply ----")
    # wanted(on, region, position, advice_for, advice_uci, cleared, flipped)
    check("nothing is drawn before the engine has answered",
          OV.wanted(True, R1, FEN_A, None, None, None), None)
    check("advice for the position on screen is drawn",
          OV.wanted(True, R1, FEN_A, FEN_A, "e2e4", None),
          (R1, "e2e4", False))

    # Fault one. The move lands, the engine has not answered yet, and the old
    # arrow used to stay drawn on the new position until it did.
    check("the move that makes the advice stale takes the arrow down",
          OV.wanted(True, R1, FEN_B, FEN_A, "e2e4", None), None)
    check("  and a late reply for the position before is still refused",
          OV.wanted(True, R1, FEN_C, FEN_B, "e7e5", None), None)
    check("  advice for the position now on screen is what comes back",
          OV.wanted(True, R1, FEN_B, FEN_B, "g1f3", None),
          (R1, "g1f3", False))

    # Fault two. The window moved or was resized. The advice is still true, so
    # the arrow moves with the board rather than hiding or staying put.
    check("moving the board moves the arrow with it",
          OV.wanted(True, R2, FEN_B, FEN_B, "g1f3", None),
          (R2, "g1f3", False))
    check("  and turning the board round is carried through",
          OV.wanted(True, R1, FEN_B, FEN_B, "g1f3", None, True)[2], True)
    check("  a region change is a different answer, so a caller cannot miss it",
          OV.wanted(True, R1, FEN_B, FEN_B, "g1f3", None)
          == OV.wanted(True, R2, FEN_B, FEN_B, "g1f3", None), False)

    # Fault three, the half of it that is a rule. That the app ever reaches a
    # region of None is what board_goes_away() below has to show.
    check("no board means no arrow",
          OV.wanted(True, None, FEN_B, FEN_B, "g1f3", None), None)
    check("switching the arrow off takes it down",
          OV.wanted(False, R1, FEN_B, FEN_B, "g1f3", None), None)

    print("\n-- clearing beats the engine -----------------------------")
    check("a cleared position stays clear when its own reply lands",
          OV.wanted(True, R1, FEN_B, FEN_B, "g1f3", FEN_B), None)
    check("  and when the engine thinks again about it",
          OV.wanted(True, R1, FEN_B, FEN_B, "d2d4", FEN_B), None)
    check("  and when the board is redrawn somewhere else",
          OV.wanted(True, R2, FEN_B, FEN_B, "g1f3", FEN_B), None)
    check("the next move brings arrows back without another click",
          OV.wanted(True, R1, FEN_C, FEN_C, "b1c3", FEN_B),
          (R1, "b1c3", False))
    # None means "no position", and it turns up on both sides: the sentinel
    # for nothing cleared, and the position when there is none on screen. Two
    # Nones matching each other must not read as a match.
    check("advice about no position at all is not drawn",
          OV.wanted(True, R1, None, None, "e2e4", None), None)
    check("  nor is advice whose own position is unknown",
          OV.wanted(True, R1, FEN_A, None, "e2e4", None), None)


def clear_across_games():
    print("\n-- a clear does not outlive its position -----------------")
    # Two games in one sitting reach byte-identical opening positions, so a
    # suppression kept past the position it was made for silently swallows the
    # next game's arrow.
    first, second = chess.Board(), chess.Board()
    check("two games open on the same position, character for character",
          first.fen(), second.fen())

    # What the app does at the end of _render, spelled out so it can be run.
    def after_frame(cleared, position, event=None):
        if event == "newgame" or cleared != position:
            return None
        return cleared

    cleared = FEN_A                       # cleared in game one, at the start
    check("it holds while that position is still on the board",
          after_frame(cleared, FEN_A), FEN_A)
    cleared = after_frame(cleared, FEN_B)
    check("  and is dropped the moment the position moves on", cleared, None)
    check("  so the same position in game two is not suppressed",
          OV.wanted(True, R1, FEN_A, FEN_A, "e2e4", cleared),
          (R1, "e2e4", False))
    check("a new game drops it even at the position it was made at",
          after_frame(FEN_A, FEN_A, event="newgame"), None)


# ------------------------------------------------------- the board going away

class FakeDesktop:
    """A desktop the real Worker can be pointed at: a rendered chess.com board
    on a plain background, or the background on its own."""

    DESK = (0, 0, 640, 560)
    AT = (100, 60)
    SIZE = 400

    def __init__(self):
        ref = Image.open(shot("1")).convert("RGB")
        self.render = Renderer(shot("1"), W.find_board(ref))
        self.board = chess.Board()
        self.there = True

    def image(self):
        desk = Image.new("RGB", self.DESK[2:], (32, 30, 28))
        if self.there:
            desk.paste(self.render.render(self.board).resize(
                (self.SIZE, self.SIZE), Image.LANCZOS), self.AT)
        return desk

    def grab(self, region):
        x, y, w, h = region
        return self.image().crop((x, y, x + w, y + h))


def board_goes_away():
    print("\n-- the worker gives the region up when the board goes ----")
    import chesswatch as CW
    desk = FakeDesktop()
    was = (CW.grab, CW.virtual_screen)
    CW.grab, CW.virtual_screen = desk.grab, lambda: desk.DESK
    out = tempfile.mkdtemp()
    try:
        q = queue.Queue()
        worker = CW.Worker(None, q, directory=out)
        for _ in range(12):
            worker._tick()
            if worker.tracker.locked_on:
                break
        check("the worker finds the board and locks on",
              (worker.tracker.locked_on, worker.region),
              (True, (desk.AT[0], desk.AT[1], desk.SIZE, desk.SIZE)))
        desk.board.push_san("e4")
        for _ in range(6):
            worker._tick()
        check("  and records a move played on it",
              worker.tracker.game.moves, ["e4"])

        # Close the tab. The region still exists and still grabs pixels, they
        # are just not a board any more. This is where the arrow used to be
        # stranded: no searching was ever sent, the region never changed, and
        # the frame kept reporting the same position for ever.
        desk.there = False
        while not q.empty():
            q.get_nowait()
        kinds, noticed = [], None
        for i in range(30):
            worker._tick()
            while not q.empty():
                kind = q.get_nowait()[0]
                kinds.append(kind)
                if kind == "searching" and noticed is None:
                    noticed = i + 1
        print("      noticed on tick %s of 30" % noticed)
        check("the board going away is noticed and said out loud",
              noticed is not None and noticed <= 20, True)
        check("  the region is given up, which is what hides the arrow",
              worker.region, None)
        check("  and no frame claims a board after that",
              kinds[-1], "searching")

        # And it is not a wedge: the board coming back is picked up again and
        # the game carries on.
        desk.there = True
        back = None
        for i in range(20):
            worker._tick()
            if worker.region is not None:
                back = i + 1
                break
        check("the board coming back is picked up again",
              back is not None and back <= 20, True)
        desk.board.push_san("e5")
        for _ in range(8):
            worker._tick()
        check("  and the same game keeps recording",
              worker.tracker.game.moves, ["e4", "e5"])
    finally:
        CW.grab, CW.virtual_screen = was
        shutil.rmtree(out, ignore_errors=True)


# ------------------------------------------- keeping what you taught

class Blank:
    """A label that remembers what it was told instead of drawing it."""

    def __init__(self):
        self.text = ""

    def configure(self, **kw):
        self.text = kw.get("text", self.text)


def stub_app(reader):
    """Enough of App to stand in as self. Every method run against it is App's
    own; only the widgets and the worker are stand-ins."""
    import chesswatch as CW
    app = types.SimpleNamespace(
        board_region=None,
        colour_choice=types.SimpleNamespace(get=lambda: "auto"),
        coach_on=types.SimpleNamespace(get=lambda: False),
        arrow_on=types.SimpleNamespace(get=lambda: True),
        taught_sheet=None,
        lbl_check=Blank(),
        worker=types.SimpleNamespace(reader=reader))
    app._save_config = lambda: CW.App._save_config(app)
    app._load_config = lambda: CW.App._load_config(app)
    return app


def persistence():
    print("\n-- what you taught outlives the session ------------------")
    import chesswatch as CW
    tmp = tempfile.mkdtemp()
    was = CW.CONFIG_PATH
    CW.CONFIG_PATH = os.path.join(tmp, "config.json")
    try:
        b6 = board_of("6")
        taught = E.write_sheet(b6, slots_from(TRUTH_6),
                               os.path.join(tmp, "taught.png"))
        reader = P.PieceReader()
        check("a fresh reader starts on the bundled sheet",
              (reader.source, reader.sheet), ("bundled", P.TEMPLATE_SHEET))

        app = stub_app(reader)
        CW.App._use_taught(app, taught)
        check("handing it a taught sheet loads that sheet",
              (reader.ready, reader.sheet), (True, taught))
        check("  and it reads the board it was taught from",
              tally(reader, b6, TRUTH_6)[0] >= 51, True)
        check("  and says so", app.lbl_check.text,
              "reading with the pieces you taught")

        check("the path is written to the config",
              CW.App._load_config(app).get("sheet"), taught)
        # The next launch: a new App reads the config, a new Worker builds a
        # new reader on the bundled sheet, and _start hands the sheet back.
        again = stub_app(P.PieceReader())
        again.taught_sheet = CW.App._load_config(again).get("sheet")
        CW.App._use_taught(again, again.taught_sheet)
        check("  so the next launch comes up reading with it",
              (again.worker.reader.sheet, again.worker.reader.ready),
              (taught, True))

        # And a sheet that has gone is forgotten rather than complained about
        # at every launch from then on.
        os.remove(taught)
        third = stub_app(P.PieceReader())
        third.taught_sheet = taught
        CW.App._use_taught(third, taught)
        check("a taught sheet that has been deleted is dropped",
              (third.taught_sheet, CW.App._load_config(third).get("sheet")),
              (None, None))
        check("  the reader is left on a sheet that does load",
              third.worker.reader.sheet, P.TEMPLATE_SHEET)
        check("  and it says what happened", third.lbl_check.text,
              "that sheet would not load")
    finally:
        CW.CONFIG_PATH = was
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- the layout

# Tk turns a positive point size into pixels through `tk scaling`, which on
# Windows is the display dpi over 72. The app calls SetProcessDpiAwareness(2),
# so the font grows with the display scaling while minsize does not.
PX = {"100%": round(8 * 96 / 72), "150%": round(8 * 144 / 72)}

# Chrome drawn round the text, low and high. This is bounded rather than
# measured: it cannot be measured without a window. Only the high end is
# load bearing, since that is the one that has to fit.
CHROME = {"btn": (10, 24), "lbl": (0, 6), "check": (18, 30), "radio": (18, 30)}

ROWS = {
    "tools": [("btn", "check the pieces now"), ("lbl", "I play:"),
              ("radio", "auto"), ("radio", "white"), ("radio", "black")],
    "switches": [("lbl", "coaching:"), ("check", "best move"),
                 ("check", "arrow on board")],
    "extras": [("btn", "clear arrows"), ("btn", "teach the pieces")],
}

# minsize is 400 and the rows are packed with padx=12 a side.
AVAILABLE = 400 - 24
FONT_PATH = "C:/Windows/Fonts/segoeui.ttf"


def row_px(items, px):
    from PIL import ImageFont
    font = ImageFont.truetype(FONT_PATH, px)
    scale = px / PX["100%"]
    low = high = 0
    for kind, text in items:
        box = font.getbbox(text)
        width = box[2] - box[0]
        low += width + CHROME[kind][0] * scale
        high += width + CHROME[kind][1] * scale
    return round(low), round(high)


def layout():
    print("\n-- the new buttons fit the window ------------------------")
    if not os.path.exists(FONT_PATH):
        print("SKIP  no Segoe UI on this machine, cannot measure the rows")
        return
    for label, px in PX.items():
        for name, items in ROWS.items():
            low, high = row_px(items, px)
            print("      %-9s at %s: %d to %d px of %d"
                  % (name, label, low, high, AVAILABLE))
    check("the row the two new buttons sit on fits at 100% scaling",
          row_px(ROWS["extras"], PX["100%"])[1] <= AVAILABLE, True)
    check("  and at 150%, where the font grows and minsize does not",
          row_px(ROWS["extras"], PX["150%"])[1] <= AVAILABLE, True)
    check("the coaching switches still fit at both",
          [row_px(ROWS["switches"], px)[1] <= AVAILABLE for px in PX.values()],
          [True, True])
    # The tools row was already full before any of this. Nothing was added to
    # it, and this holds it at what it was: fitting at 100% and not at 150%.
    check("nothing was added to the row that was already full",
          row_px(ROWS["tools"], PX["100%"])[1] <= AVAILABLE, True)


# ---------------------------------------------------------------- the wiring

def calls_of(src, name):
    """How many arguments each call of a method is given, read off the code."""
    tree = ast.parse(textwrap.dedent(src))
    return [len(n.args) for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == name]


def wiring():
    print("\n-- the fix is wired in -----------------------------------")
    # These read chesswatch.py rather than run it, because running it opens a
    # window on a real desktop. They are the join between the parts above that
    # were driven, and they are worth no more than that.
    import chesswatch as CW
    src = {name: inspect.getsource(getattr(CW.App, name))
           for name in ("_render", "_drain", "_drain_coach", "_stop", "_start",
                        "_toggle_arrow", "_toggle_coach", "_clear_arrows",
                        "_show_arrow", "_sync_arrow", "_use_taught")}

    check("_render finishes every frame by syncing the arrow",
          src["_render"].rstrip().endswith("self._sync_arrow()"), True)
    check("_sync_arrow asks overlay.wanted and nothing else",
          len(calls_of(src["_sync_arrow"], "wanted")), 1)
    check("_render drops a clear that has outlived its position",
          "self.arrow_cleared = None" in src["_render"], True)
    check("_drain hides the arrow when the worker says it is searching",
          "self.region = None" in src["_drain"]
          and "self._sync_arrow()" in src["_drain"], True)
    check("the coach hands the arrow the position its move was for",
          max(calls_of(src["_drain_coach"], "_show_arrow")), 2)
    check("  and _show_arrow has no default to forget it with",
          list(inspect.signature(CW.App._show_arrow).parameters),
          ["self", "uci", "fen"])
    check("clearing records the position before it takes the arrow down",
          src["_clear_arrows"].index("self.arrow_cleared")
          < src["_clear_arrows"].index("self._hide_arrow()"), True)
    for name in ("_stop", "_toggle_coach"):
        check("%s takes the arrow down too" % name,
              "_hide_arrow()" in src[name], True)
    check("_toggle_arrow syncs rather than hiding by hand",
          "_sync_arrow" in src["_toggle_arrow"]
          and ".hide()" not in src["_toggle_arrow"], True)
    check("nothing draws the arrow except _sync_arrow",
          [n for n in src if ".arrow.show(" in src[n]], ["_sync_arrow"])
    check("a taught sheet is handed to every worker that is started",
          "_use_taught" in src["_start"], True)

    esrc = inspect.getsource(E)
    check("enroll builds a root only for its own standalone run",
          esrc.count("tk.Tk()"), 1)
    check("the arrow colour is untouched", OV.COLOUR, "#00E8FF")
    grey = Image.new("RGB", (1, 1), OV.COLOUR).convert("L").getpixel((0, 0))
    check("  so it still greys into the band the reader ignores",
          W.DARK < grey < W.BRIGHT, True)


def main():
    sheet()
    knows()
    labels()
    arrow()
    clear_across_games()
    board_goes_away()
    persistence()
    layout()
    wiring()
    print("\n%d/%d passed" % (sum(bool(x) for x in R), len(R)))
    return 0 if all(R) else 1


if __name__ == "__main__":
    sys.exit(main())
