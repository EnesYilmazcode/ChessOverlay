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

The last section reads the source of chesswatch.py to check the call sites the
fix depends on are still there. That is a weaker kind of check and it is
labelled as one: it proves the wiring exists, not that it draws.

What is NOT checked here: every widget in enroll.py, the arrow really appearing
on screen, and Windows click-through. The first needs a desktop, and the last
two are already what overlaytest.py measures.
"""

import ast
import os
import sys
import inspect
import shutil
import textwrap
import tempfile

import chess
from PIL import Image

import watcher as W
import pieces as P
import overlay as OV
import enroll as E
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
    b1, b6 = board_of("1"), board_of("6")
    bundled = P.PieceReader()

    scored = E.beliefs(bundled, b1)
    check("every square comes back with a symbol and a number",
          sorted({(type(s).__name__, type(v).__name__)
                  for row in scored for s, v in row}),
          [("str", "float")])
    check("  and the symbols are the ones classify() gives",
          [[s for s, _ in row] for row in scored],
          [list(row) for row in bundled.classify(b1)[0]])

    seeded = E.seed_slots(scored)
    right = sum(1 for s, (r, c) in seeded.items() if TRUTH_1[r][c] == s)
    print("      on chess.com's own set it proposes %d of 12 squares, "
          "%d of them right" % (len(seeded), right))
    check("on a set it can read, the window opens with nothing left to do",
          (len(seeded), right), (12, 12))

    seeded6 = E.seed_slots(E.beliefs(bundled, b6))
    right6 = sum(1 for s, (r, c) in seeded6.items() if TRUTH_6[r][c] == s)
    print("      on the set from issue #20 it proposes %d of 12, %d right"
          % (len(seeded6), right6))
    check("  on a set it cannot read it proposes almost nothing, "
          "and invents nothing", right6, len(seeded6))

    # A reader holding no templates has no beliefs to show, and has to say so
    # rather than fill the board in with guesses.
    empty = P.PieceReader(os.path.join(tempfile.gettempdir(), "no-such.png"))
    empty.templates = {}
    check("a reader that knows nothing seeds nothing",
          E.seed_slots(E.beliefs(empty, b1)), {})


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

    check("the twelve it started with are still there where untouched",
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
          lab.hint().startswith("that square reads %s at" % scored[0][0][0]),
          True)


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

    # Fault three. The board is gone off screen entirely, so nothing asks the
    # engine anything and nothing used to take the arrow down.
    check("losing the board takes the arrow off it",
          OV.wanted(True, None, FEN_B, FEN_B, "g1f3", None), None)
    check("switching the arrow off takes it down",
          OV.wanted(False, R1, FEN_B, FEN_B, "g1f3", None), None)
    check("no position on screen draws nothing",
          OV.wanted(True, R1, None, None, "g1f3", None), None)

    print("\n-- clearing beats the engine -----------------------------")
    # The button records the position it cleared. The reply the engine is
    # already working on carries that same position, so it loses.
    check("a cleared position stays clear when its own reply lands",
          OV.wanted(True, R1, FEN_B, FEN_B, "g1f3", FEN_B), None)
    check("  and when the engine thinks again about it",
          OV.wanted(True, R1, FEN_B, FEN_B, "d2d4", FEN_B), None)
    check("  and when the board is redrawn in the same place",
          OV.wanted(True, R2, FEN_B, FEN_B, "g1f3", FEN_B), None)
    check("the next move brings arrows back without another click",
          OV.wanted(True, R1, FEN_C, FEN_C, "b1c3", FEN_B),
          (R1, "b1c3", False))
    check("clearing with no position on screen suppresses nothing later",
          OV.wanted(True, R1, FEN_A, FEN_A, "e2e4", None),
          (R1, "e2e4", False))


# ------------------------------------------------- how many windows there are

class Win:
    """Stands in for a Toplevel. The sweep only ever looks at the tag."""

    def __init__(self, is_arrow):
        setattr(self, OV.ARROW_TAG, is_arrow)


def orphans():
    print("\n-- how many arrow windows there can be -------------------")
    # Reading chesswatch.py: _toggle_arrow builds an Arrow only when
    # self.arrow is None and nothing ever sets it back to None, Arrow._draw
    # clears the canvas before every line, and a Toplevel cannot outlive its
    # process. So one App can only ever show one arrow. The one way a second
    # window exists is an Arrow whose __init__ fails after the Toplevel is
    # made: the caller gets no reference back, self.arrow stays None, and the
    # next toggle builds another while Tk still holds the first.
    live, orphan, other = Win(True), Win(True), Win(False)
    check("the window in use is not swept",
          OV._orphans([live, orphan, other], live), [orphan])
    check("  and neither is anything that is not an arrow",
          other in OV._orphans([live, orphan, other], None), False)
    check("with nothing to keep, every arrow window goes",
          OV._orphans([live, orphan], None), [live, orphan])
    check("nothing to sweep is not an error", OV._orphans([other], None), [])

    # The other half of that fix: a half built Arrow has to take its own
    # window with it, since nothing else can.
    src = inspect.getsource(OV.Arrow.__init__)
    check("a failed Arrow destroys the window it had already made",
          "self.win.destroy()" in src and "raise" in src, True)


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
    # window on a real desktop. A missing call is how this regresses, so the
    # call being there is worth holding on to even though it is not proof that
    # it draws.
    import chesswatch as CW          # importing opens nothing; main() does
    src = {name: inspect.getsource(getattr(CW.App, name))
           for name in ("_render", "_drain", "_drain_coach", "_stop",
                        "_toggle_arrow", "_toggle_coach", "_clear_arrows",
                        "_show_arrow", "_sync_arrow")}

    check("_render finishes every frame by syncing the arrow",
          src["_render"].rstrip().endswith("self._sync_arrow()"), True)
    check("_sync_arrow asks overlay.wanted and nothing else",
          len(calls_of(src["_sync_arrow"], "wanted")), 1)
    check("losing the board forgets the region, which is what hides it",
          "self.region = None" in src["_drain"]
          and "self._sync_arrow()" in src["_drain"], True)
    check("the coach hands the arrow the position its move was for",
          max(calls_of(src["_drain_coach"], "_show_arrow")), 2)
    check("  and _show_arrow takes one",
          list(inspect.signature(CW.App._show_arrow).parameters),
          ["self", "uci", "fen"])
    check("clearing records the position before it takes the arrow down",
          src["_clear_arrows"].index("self.arrow_cleared")
          < src["_clear_arrows"].index("self._show_arrow(None)"), True)
    check("  and sweeps for a window left behind",
          len(calls_of(src["_clear_arrows"], "close_orphans")), 1)
    for name in ("_stop", "_toggle_coach"):
        check("%s takes the arrow down too" % name,
              "_show_arrow(None)" in src[name], True)
    check("_toggle_arrow syncs rather than hiding by hand",
          "_sync_arrow" in src["_toggle_arrow"]
          and ".hide()" not in src["_toggle_arrow"], True)
    check("nothing draws the arrow except _sync_arrow",
          [n for n in src if ".arrow.show(" in src[n]], ["_sync_arrow"])

    # enroll.py must not open anything on import or from the app's button
    # except the window the button is for.
    esrc = inspect.getsource(E)
    check("enroll builds a root only for its own standalone run",
          esrc.count("tk.Tk()"), 1)
    check("  and the arrow colour is untouched", OV.COLOUR, "#00E8FF")
    grey = Image.new("RGB", (1, 1), OV.COLOUR).convert("L").getpixel((0, 0))
    check("  so it still greys into the band the reader ignores",
          W.DARK < grey < W.BRIGHT, True)


def main():
    sheet()
    knows()
    labels()
    arrow()
    orphans()
    wiring()
    print("\n%d/%d passed" % (sum(bool(x) for x in R), len(R)))
    return 0 if all(R) else 1


if __name__ == "__main__":
    sys.exit(main())
