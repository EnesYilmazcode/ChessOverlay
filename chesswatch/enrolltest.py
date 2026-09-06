"""Checks for teaching the pieces by hand, and for the arrow following the
position instead of the last thing the engine said.

Run:  python enrolltest.py

Nothing here opens a window. That is deliberate rather than a limitation: this
app puts an always on top, click through overlay on the real desktop, so a test
that draws one cannot be run on a machine somebody is using. Everything below
runs with Tk's two window classes replaced by a refusal, so that is enforced
rather than promised. Both features were
written with the decision separated from the widget so that this file can check
the decision.

  overlay.wanted()   the whole arrow staleness rule, no window in it
  enroll.Labels      which square each piece is taken from, no window in it
  enroll.opening_view whether a board really is the starting position, and
                     which way round, off the pixels
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
two are already what overlaytest.py measures. The window's own layout, and how
wide each of its rows comes out, are layouttest.py's.
"""

import ast
import os
import sys
import tkinter
import time
import queue
import inspect
import shutil
import types
import textwrap
import tempfile

import chess
from PIL import Image, ImageChops, ImageFilter

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


def no_windows():
    """Replace Tk's two window classes with a refusal.

    Called before any check runs. Without it the claim at the top of this file
    is only a claim, and the cost of it being wrong is a window drawn on the
    desktop of whoever ran the tests.
    """
    def refuse(*args, **kwargs):
        raise AssertionError("the tests must not open a window")

    tkinter.Tk = tkinter.Toplevel = refuse


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


def slots_from(truth, paired=True):
    """Where somebody clicking through the board ends up: the first square of
    each colour holding each of the twelve types.

    With paired False it is the first square of any colour, which is the person
    who clicked twelve times and stopped.
    """
    out = {}
    for r in range(8):
        for c in range(8):
            symbol = truth[r][c]
            if symbol == ".":
                continue
            here = out.setdefault(symbol, {})
            if paired or not here:
                here.setdefault(E.light_square(r, c), [(r, c)])
    return out


def renderer(name):
    """The set from a fixture, ready to draw any position in it."""
    img = Image.open(shot(name)).convert("RGB")
    return Renderer(shot(name), W.find_board(img))


def slot(sheet_img, symbol, light):
    """One slot cut back out of a written sheet."""
    at = (0 if light else P.PLAIN_SLOTS) + P.ORDER.index(symbol)
    return sheet_img.crop((at * P.TEMPLATE_PX, 0,
                           (at + 1) * P.TEMPLATE_PX, P.TEMPLATE_PX))


def h8_ranking(reader, board_img):
    """Every piece type scored against h8, best first. Row 0 column 7 on
    screen, with white at the bottom."""
    levels = P._levels(board_img)
    feat = P._board_features(board_img, levels)[7]
    return P.ranking(feat, reader.templates)


# ---------------------------------------------------------------- the sheet

def sheet():
    print("\n-- the sheet it writes -----------------------------------")
    b1, b6 = board_of("1"), board_of("6")
    tmp = tempfile.mkdtemp()
    try:
        path = E.write_sheet(b1, slots_from(TRUTH_1), os.path.join(tmp, "a.png"))
        check("the sheet holds each piece on each square colour",
              Image.open(path).size,
              (P.TEMPLATE_PX * P.PAIRED_SLOTS, P.TEMPLATE_PX))

        taught = P.PieceReader(path)
        check("and the ordinary reader loads it without being told anything",
              taught.ready, True)
        check("  as two templates a piece",
              sorted({len(v) for v in taught.templates.values()}), [2])
        check("  which know which colour they came off",
              sorted({t.light for v in taught.templates.values() for t in v}),
              [False, True])
        check("a board taught from itself then reads back with nothing wrong",
              tally(taught, b1, TRUTH_1), (64, 0, 0))

        # The case the whole thing is for. On this set the bundled sheet names
        # one piece in thirty two, which is issue #20. Teaching from the board
        # itself is the only route open to a game already in progress.
        bundled = P.PieceReader()
        base = tally(bundled, b6, TRUTH_6)
        path6 = E.write_sheet(b6, slots_from(TRUTH_6),
                              os.path.join(tmp, "b.png"))
        got = tally(P.PieceReader(path6), b6, TRUTH_6)
        print("      6.png, a set the bundled sheet has never seen:")
        print("        bundled  correct %d  wrong %d  unknown %d" % base)
        print("        taught   correct %d  wrong %d  unknown %d" % got)
        check("  teaching reads at least the 59 squares of 6.png it read here",
              got[0] >= 59, True)
        check("  and names nothing wrong doing it", got[1], 0)
        check("  which beats the 40 the bundled sheet managed",
              got[0] > base[0], True)

        # The square colour is what does that, and h8 is where it shows. A
        # rook cut only from light a8 used to lose that square to a pawn cut
        # from dark a7, on parity rather than on shape. The reader moves a
        # taught piece onto the other square colour itself now, so a one colour
        # sheet no longer names anything wrong; what it still costs is squares
        # it will not name at all, because a repainted ground is a guess at the
        # rendering where a second slot is the rendering.
        one = E.write_sheet(b6, slots_from(TRUTH_6, paired=False),
                            os.path.join(tmp, "d.png"))
        flat = tally(P.PieceReader(one), b6, TRUTH_6)
        print("        one colour only: correct %d  wrong %d  unknown %d" % flat)
        check("  one square a piece still names nothing wrong", flat[1], 0)
        for label, sheet_path in (("one colour", one), ("both colours", path6)):
            ranked = h8_ranking(P.PieceReader(sheet_path), b6)
            print("      h8, a black rook on a dark square, taught from %s:"
                  % label)
            for score, symbol in ranked[:3]:
                print("        %s  %.4f%s" % (symbol, score,
                                              "   <- correct" if symbol == "r"
                                              else ""))
        check("  and teaching both colours puts the rook back on top",
              h8_ranking(P.PieceReader(path6), b6)[0][1], "r")
        check("  clear of the pawn by more than the margin asks",
              h8_ranking(P.PieceReader(path6), b6)[0][0]
              - h8_ranking(P.PieceReader(path6), b6)[1][0] > P.MARGIN, True)

        # A person who clicks twelve times and stops has to be no worse off
        # than the one slot sheet left them, which means the same reading.
        check("twelve clicks still writes a whole sheet",
              Image.open(one).size,
              (P.TEMPLATE_PX * P.PAIRED_SLOTS, P.TEMPLATE_PX))
        check("  and reads the board no worse than one slot a piece did",
              flat[0] >= 58 and flat[1] <= 1, True)

        # A slot left black is not a sheet with a hole in it, it is a template
        # that matches every square. A piece nobody taught gets the slot it
        # already had rather than a black one, and rather than the refusal that
        # used to stop the whole save.
        short = dict(slots_from(TRUTH_1))
        short.pop("q")
        short.pop("K")
        part = E.write_sheet(b1, short, os.path.join(tmp, "c.png"))
        check("a sheet missing a piece is written, not refused",
              Image.open(part).size,
              (P.TEMPLATE_PX * P.PAIRED_SLOTS, P.TEMPLATE_PX))
        check("  and the ordinary reader loads it",
              P.PieceReader(part).ready, True)

        # Which slots came from where. The bundled sheet is twelve wide and
        # says nothing about square colour, so the piece it stands in for is
        # the same crop in both halves.
        part_img, bundled_img = Image.open(part), Image.open(P.TEMPLATE_SHEET)
        for symbol in ("K", "q"):
            check("  %s is left on the sheet it was already read from" % symbol,
                  [ImageChops.difference(
                      slot(part_img, symbol, light),
                      bundled_img.crop((P.ORDER.index(symbol) * P.TEMPLATE_PX, 0,
                                        (P.ORDER.index(symbol) + 1)
                                        * P.TEMPLATE_PX, P.TEMPLATE_PX))
                   ).getbbox() for light in (True, False)], [None, None])
        # The other half of that claim, and the one that fails if write_sheet
        # ever fills a slot it was taught: a piece that was taught has to be
        # this board, not the bundled sheet.
        check("  while a piece that was taught is cut from the board",
              ImageChops.difference(
                  slot(part_img, "Q", True),
                  bundled_img.crop((P.ORDER.index("Q") * P.TEMPLATE_PX, 0,
                                    (P.ORDER.index("Q") + 1) * P.TEMPLATE_PX,
                                    P.TEMPLATE_PX))).getbbox() is not None,
              True)

        # Teaching nothing is not a partial save, it is a copy of the sheet
        # already in use written out under a name that claims otherwise.
        failed = ""
        try:
            E.write_sheet(b1, {}, os.path.join(tmp, "none.png"))
        except ValueError as exc:
            failed = str(exc)
        check("teaching nothing at all is still refused", failed,
              "nothing taught yet")
        check("  and no file is left behind",
              os.path.exists(os.path.join(tmp, "none.png")), False)

        # And with nothing loadable to leave them on there is no sheet to
        # write, so the old refusal is what is left.
        gone = os.path.join(tmp, "no-such.png")
        real = P.TEMPLATE_SHEET
        P.TEMPLATE_SHEET = gone
        try:
            failed = ""
            try:
                E.write_sheet(b1, short, os.path.join(tmp, "f.png"), gone)
            except ValueError as exc:
                failed = str(exc)
        finally:
            P.TEMPLATE_SHEET = real
        check("with no sheet to leave them on it is refused as before",
              failed, "nothing taught for K q, and no sheet to leave them on")

        # The bundled sheet is still twelve slots and has to keep loading
        # exactly as it did, with no colour claimed for anything.
        plain = P.PieceReader()
        check("the sheet that ships is still read as one template a piece",
              (Image.open(P.TEMPLATE_SHEET).size[0] // P.TEMPLATE_PX,
               sorted({len(v) for v in plain.templates.values()})),
              (P.PLAIN_SLOTS, [1]))
        check("  and claims no square colour it cannot know",
              {t.light for v in plain.templates.values() for t in v}, {None})

        # Cut short. Under twelve is rubble. Twelve or more reads as a plain
        # sheet off the first twelve slots. Cut to exactly twelve those twelve
        # are every light square and nothing else, which the reader that
        # thresholded could not load at all because the sheet then had one
        # board colour where it needed two; this one normalises each slot by
        # its own spread and reads them, and holds a piece on one square colour
        # rather than nothing. All of them are all or nothing, which is the
        # guarantee that matters.
        wide = Image.open(path6)
        for slots, want in ((5, False), (P.PLAIN_SLOTS, True),
                            (P.PLAIN_SLOTS + 4, True), (P.PAIRED_SLOTS, True)):
            cut = os.path.join(tmp, "cut%d.png" % slots)
            wide.crop((0, 0, slots * P.TEMPLATE_PX, P.TEMPLATE_PX)).save(cut)
            reader = P.PieceReader(cut)
            check("  a %d slot cut of a paired sheet loads: %s"
                  % (slots, want), reader.ready, want)
            if want:
                check("    as %d template(s) a piece"
                      % (2 if slots >= P.PAIRED_SLOTS else 1),
                      sorted({len(v) for v in reader.templates.values()}),
                      [2] if slots >= P.PAIRED_SLOTS else [1])
            else:
                check("    and leaves whatever was loaded alone",
                      reader.source, "none")

        # Which is why a sheet has to be refused at the point it is written,
        # where the reason can still be said plainly.
        one_colour = {s: {True: rc[True]} for s, rc in slots_from(TRUTH_1).items()
                      if True in rc}
        one_colour.update({s: {True: [(0, 0)]} for s in P.ORDER
                           if s not in one_colour})
        failed = ""
        try:
            E.write_sheet(b1, one_colour, os.path.join(tmp, "e.png"))
        except ValueError as exc:
            failed = str(exc)
        check("teaching every piece off one square colour is refused",
              failed, "every square taught is the same colour")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# -------------------------------------- a game joined part way through

# Positions with pieces already off the board, drawn in the set of 6.png,
# which is the set the bundled sheet cannot read. The first is the case issue
# #50 reports: queens and bishops gone, so four of the twelve types cannot be
# pointed at however long you look. The other two are one sitting each, with a
# different pair of types missing, for teaching that accumulates.
MID = "r2nk2r/ppp2ppp/8/8/8/8/PPP2PPP/R2NK2R w - - 0 1"
NO_QUEENS = "r1b1k2r/pppp1ppp/2n2n2/4p3/2B1P3/2N2N2/PPPP1PPP/R1B1K2R w - - 0 1"
NO_KNIGHTS = "r1bqk2r/pppp1ppp/8/4p3/2B1P3/8/PPPP1PPP/R1BQK2R w - - 0 1"


def rendered(ren, fen):
    """A position drawn in some fixture's piece set, with the letters it ought
    to read as."""
    board = chess.Board(fen)
    return ren.render(board), ["".join(row) for row in W.grid_of(board, False)]


def same_slots(one, two, symbol):
    """Whether two written sheets hold the same picture of one piece, on each
    square colour. Both halves, because the dark one is the half a sheet
    copied out of another can land in the wrong place."""
    return [ImageChops.difference(slot(Image.open(one), symbol, light),
                                  slot(Image.open(two), symbol, light)
                                  ).getbbox() is None
            for light in (True, False)]


def partial():
    print("\n-- a game joined part way through ------------------------")
    ren = renderer("6")
    tmp = tempfile.mkdtemp()
    try:
        mid, truth = rendered(ren, MID)
        slots = slots_from(truth)
        gone = [s for s in P.ORDER if s not in slots]
        check("a board with pieces already traded cannot teach all twelve",
              gone, ["Q", "B", "q", "b"])

        # The measurement the issue is about. The honest baseline is teaching
        # nothing at all, because that is what refusing the save left behind.
        base = tally(P.PieceReader(), mid, truth)
        path = E.write_sheet(mid, slots, os.path.join(tmp, "mid.png"))
        got = tally(P.PieceReader(path), mid, truth)
        print("      a mid game board on the set of 6.png, 64 squares:")
        print("        taught nothing   correct %d  wrong %d  unknown %d" % base)
        print("        taught the 8 on it   correct %d  wrong %d  unknown %d"
              % got)
        check("teaching the eight types that are there reads the whole board",
              got, (64, 0, 0))
        check("  which is better than the %d squares teaching nothing read"
              % base[0], got[0] > base[0], True)
        check("  and names nothing wrong doing it", got[1], 0)

        # Same claim one step out: the board is not read at the size it was
        # taught at, which is where a template that was never really cut from
        # this set shows up.
        smaller = mid.resize((400, 400), Image.LANCZOS)
        print("        in a 400px window   taught nothing %d/%d/%d,"
              " taught %d/%d/%d"
              % (tally(P.PieceReader(), smaller, truth)
                 + tally(P.PieceReader(path), smaller, truth)))
        check("  and holds up at a size it was not taught at",
              tally(P.PieceReader(path), smaller, truth)[1], 0)

        # Teaching accumulates, because the sheet a piece is left on is the
        # one the reader is reading with, and after a save that is the taught
        # sheet. Two sittings, a different pair of types missing in each.
        first, truth1 = rendered(ren, NO_QUEENS)
        second, truth2 = rendered(ren, NO_KNIGHTS)
        one = E.write_sheet(first, slots_from(truth1),
                            os.path.join(tmp, "sitting1.png"))
        two = E.write_sheet(second, slots_from(truth2),
                            os.path.join(tmp, "sitting2.png"), one)
        check("a second sitting keeps the pieces the first taught, both colours",
              same_slots(one, two, "n"), [True, True])
        check("  and teaches the ones the first could not",
              same_slots(one, two, "q"), [False, False])
        # Without which the second sitting would be the bundled sheet's
        # knights, which is what leaving held at its default gives.
        alone = E.write_sheet(second, slots_from(truth2),
                              os.path.join(tmp, "alone.png"))
        check("  where starting over would have gone back to the bundled ones",
              same_slots(one, alone, "n"), [False, False])
        both = tally(P.PieceReader(two), mid, truth)
        print("        two sittings read the first board  %d/%d/%d" % both)
        check("  and the two sittings together read the board neither saw",
              both[1], 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------- more than two a piece

def averaging():
    print("\n-- every click kept, not just the first two ---------------")
    b6 = board_of("6")
    tmp = tempfile.mkdtemp()
    try:
        # The same board taught both ways. slots_from is the person who clicks
        # each piece on each square colour and stops, which is all the old
        # ceiling allowed; opening_slots is the new button taking all thirty
        # two squares of the position.
        hand = E.write_sheet(b6, slots_from(TRUTH_6),
                             os.path.join(tmp, "hand.png"))
        whole = E.write_sheet(
            b6, E.opening_slots(b6, E.beliefs(P.PieceReader(), b6)),
            os.path.join(tmp, "whole.png"))
        check("both ways write the same twenty four slot sheet",
              (Image.open(hand).size, Image.open(whole).size),
              ((P.TEMPLATE_PX * P.PAIRED_SLOTS, P.TEMPLATE_PX),) * 2)
        check("  and both load as two templates a piece",
              [sorted({len(v) for v in P.PieceReader(s).templates.values()})
               for s in (hand, whole)], [[2], [2]])

        size = b6.size[0]
        cases = [("as taught", b6),
                 ("in a 400px window", b6.resize((400, 400), Image.LANCZOS)),
                 ("in a 200px window", b6.resize((200, 200), Image.LANCZOS)),
                 ("cropped 3px out", b6.crop((3, 3, 3 + size, 3 + size))),
                 ("blurred by 1.0", b6.filter(ImageFilter.GaussianBlur(1.0))),
                 ("blurred by 1.5", b6.filter(ImageFilter.GaussianBlur(1.5)))]
        readers = [P.PieceReader(hand), P.PieceReader(whole)]
        worse = []
        print("      6.png read back, correct/wrong/unknown of 64 squares:")
        for name, img in cases:
            got = [tally(reader, img, TRUTH_6) for reader in readers]
            print("        %-18s 20 squares %2d/%d/%2d   32 squares %2d/%d/%2d"
                  % ((name,) + got[0] + got[1]))
            if got[1][0] < got[0][0] or got[1][1] > got[0][1]:
                worse.append(name)
        check("thirty two squares read no worse than twenty, anywhere",
              worse, [])

        # And the extra squares are really in the sheet rather than counted and
        # dropped. Four white pawns stand on light squares in the opening; the
        # white king stands on one square in the whole position.
        hand_img, whole_img = Image.open(hand), Image.open(whole)
        check("a slot with four samples is not just the first of them",
              ImageChops.difference(slot(whole_img, "P", True),
                                    slot(hand_img, "P", True)).getbbox()
              is not None, True)
        check("  while a slot with one sample is exactly that one square",
              ImageChops.difference(slot(whole_img, "K", True),
                                    slot(hand_img, "K", True)).getbbox(), None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------ the starting position

NOT_OPENING = "this board is not in the starting position"
NOT_STANDARD = ("the ends of the back rank do not match, so this is not the "
                "standard opening")
NO_SIDE = "which way round this board is drawn cannot be told from its two halves"


def refused(call, *args):
    """The reason a call gave for refusing, or None when it did not refuse.

    enroll refuses the way write_sheet has always refused, with the reason in
    the exception, so that the window has a sentence to show and this file has
    something to tell the three refusals apart by.
    """
    try:
        call(*args)
    except ValueError as exc:
        return str(exc)
    return None


def opening():
    print("\n-- taking a whole starting position -----------------------")
    b1, b6 = board_of("1"), board_of("6")
    bundled = P.PieceReader()
    start = chess.Board()

    check("6.png is read as the opening, white at the bottom",
          E.opening_view(b6), False)
    check("1.png, two moves in, is not the opening and is refused",
          refused(E.opening_view, b1), NOT_OPENING)

    # The same set drawn from black's side. fakeboard redraws the position
    # rather than turning the picture upside down, which is what chess.com does
    # when you play black.
    six = renderer("6")
    check("the same set seen from black's side is read as flipped",
          E.opening_view(six.render(start, flipped=True)), True)
    check("  and from white's side as not",
          E.opening_view(six.render(start)), False)
    check("  at sizes it was not captured at either",
          [E.opening_view(six.render(start, flipped=f, size=s))
           for s in (560, 400, 280, 200) for f in (False, True)],
          [False, True] * 4)
    check("  as is the board of 6.png itself, at every size and blurred",
          [E.opening_view(img) for img in
           [b6.resize((s, s), Image.LANCZOS) for s in (560, 400, 280, 200)]
           + [b6.filter(ImageFilter.GaussianBlur(r)) for r in (1.0, 1.5)]],
          [False] * 6)

    slots = E.opening_slots(b6, E.beliefs(bundled, b6))
    check("it takes all twelve pieces", sorted(slots), sorted(P.ORDER))
    check("  from all thirty two squares",
          sum(len(sq) for here in slots.values() for sq in here.values()), 32)
    check("  and every square it takes really holds that piece",
          [rc for s, here in slots.items() for sq in here.values() for rc in sq
           if TRUTH_6[rc[0]][rc[1]] != s], [])
    # Eight of the twelve stand on both square colours in the opening. Each
    # king and each queen has one square in the whole position, so four of the
    # twelve can only ever be single sample from it.
    check("  eight on both colours, four with one square to stand on",
          sorted(len(here) for here in slots.values()), [1] * 4 + [2] * 8)
    check("  and four pawns of each colour on each square colour",
          [sorted(len(sq) for sq in slots[s].values()) for s in "Pp"],
          [[4, 4], [4, 4]])

    # A set the reader can already read is taken as well, its own answer
    # agreeing with the opening on every square it names.
    one = renderer("1").render(start)
    named = sum(1 for row in bundled.classify(one)[0] for s in row
                if s not in (".", "?"))
    print("      on chess.com's own set the reader names %d of the 32 itself"
          % named)
    check("the set it can read is taken too",
          sorted(E.opening_slots(one, E.beliefs(bundled, one))), sorted(P.ORDER))


# --------------------------------------------- what a shuffled board does

def shuffled():
    print("\n-- boards that look like the opening and are not -----------")
    b6 = board_of("6")
    bundled = P.PieceReader()
    six, one = renderer("6"), renderer("1")
    tmp = tempfile.mkdtemp()
    try:
        # Chess960 keeps all thirty two pieces on the outer two ranks, so its
        # occupancy grid and its two ink halves are the opening's exactly. What
        # it moves is the back rank, and mapping a standard board onto it cuts
        # most of the sheet from the wrong piece. chess.com offers 960, and the
        # person pressing the button is telling the truth about the position.
        board = chess.Board.from_chess960_pos(300)
        shuffle = six.render(board)
        check("a chess960 board holds the same squares as the opening",
              [[cell != "." for cell in row] for row in W.read_occupancy(shuffle)],
              E.OPENING_OCCUPANCY)
        check("  so occupancy alone would have taken it",
              board.board_fen().split("/")[0], "qbnrkrbn")
        check("  and the ends of its back rank are what refuse it",
              refused(E.opening_view, shuffle), NOT_STANDARD)

        # What it would have cost. Twenty of the twenty four slots come off
        # the back rank on a 960 board, most of them the wrong piece.
        wrong = {s: {light: [rc for rc in sq] for light, sq in here.items()}
                 for s, here in E.opening_slots(b6, E.beliefs(bundled, b6)).items()}
        grid = W.grid_of(board, False)
        truth = ["".join(row) for row in grid]
        off = sum(1 for s, here in wrong.items() for sq in here.values()
                  for r, c in sq if truth[r][c] != s)
        print("      mapping the opening onto #300 would take %d of its 32 "
              "squares from the wrong piece" % off)

        taken = []
        for n in (0, 100, 300, 700, 959):
            for label, rend in (("6", six), ("1", one)):
                for size in (None, 400):
                    img = rend.render(chess.Board.from_chess960_pos(n), size=size)
                    if refused(E.opening_view, img) is None:
                        taken.append((n, label, size))
        check("none of ten chess960 positions is taken, in either set, at "
              "either size", taken, [])

        # And the standard position among them is still taken, drawn by the
        # same renderer, so what refuses the other ten is the back rank and not
        # something about rendering.
        check("  while #518, which is the standard opening, still is",
              [E.opening_view(rend.render(chess.Board.from_chess960_pos(518)))
               for rend in (six, one)], [False, False])

        # The gap, checked rather than claimed. A back rank that mirrors the
        # opening's outside the king and queen is invisible to a test that only
        # compares one end against the other, and so is a legal standard game
        # that swapped the knights for each other's colour. Both are taken.
        # This is here so that the day one of them is closed, this says so.
        mirrored = [n for n in range(960)
                    if (lambda r: r[0] == r[7] and r[1] == r[6] and r[2] == r[5])(
                        chess.Board.from_chess960_pos(n).board_fen().split("/")[0])]
        check("twelve of the 960 mirror the opening outside the king and queen",
              len(mirrored), 12)
        check("  and one of those, taken, is a real hole",
              E.opening_view(six.render(chess.Board.from_chess960_pos(326))),
              False)
        knights = chess.Board("rNbqkbNr/pppppppp/8/8/8/8/PPPPPPPP/RnBQKBnR w - - 0 1")
        check("  as is a standard game that swapped the knights over",
              E.opening_view(six.render(knights)), False)

        # A panel drawn over the corner of the board. Its squares still carry
        # ink, so occupancy passes, but a8 stops looking like h8.
        panel = b6.copy()
        step = b6.size[0] // 8
        panel.paste(Image.new("RGB", (2 * step, step), (32, 32, 40)), (0, 0))
        for x in range(6, 2 * step - 6, 12):
            panel.paste(Image.new("RGB", (5, step // 3), (210, 210, 214)),
                        (x, step // 3))
        check("a panel over the corner of the board is refused",
              refused(E.opening_view, panel), NOT_STANDARD)
        check("  and it is not refused for having emptied those squares",
              [[cell != "." for cell in row] for row in W.read_occupancy(panel)],
              E.OPENING_OCCUPANCY)

        # Both halves inked the same way, which is what a set whose two
        # colours do not separate looks like. Nothing here can say which way
        # round that board is, and half a chance is not enough to teach twelve
        # templates on. Drawn as white pieces at both ends rather than by
        # copying pixels about, so that the back rank it refuses on is a real
        # one and the refusal is the ink.
        alike = chess.Board(None)
        for square in chess.SQUARES:
            rank, file = chess.square_rank(square), chess.square_file(square)
            if rank in (0, 1):
                piece = chess.Board().piece_at(square)
            elif rank in (6, 7):
                piece = chess.Board().piece_at(chess.square(file, 7 - rank))
            else:
                piece = None
            if piece:
                alike.set_piece_at(square, piece)
        drawn = six.render(alike)
        check("a board whose two halves are inked alike is refused",
              refused(E.opening_view, drawn), NO_SIDE)
        check("  and not because anything looks like it moved",
              [[cell != "." for cell in row] for row in W.read_occupancy(drawn)],
              E.OPENING_OCCUPANCY)

        # The reader is asked as a bonus rather than as a guard, and it is
        # worth saying which. On 6.png it names four of the sixteen back rank
        # squares and refuses the rest, so it covers a quarter of where a
        # shuffled board differs and the check above covers all of it. What it
        # must never do is name one of them WRONG, which is the line below.
        rows = bundled.classify(b6)[0]
        back = [(rows[r][c], TRUTH_6[r][c]) for r in (0, 7) for c in range(8)]
        print("      on 6.png the reader names %d of the 64 squares and reads "
              "%s on the back rank"
              % (sum(1 for row in rows for s in row if s not in (".", "?")),
                 " ".join(sorted({g for g, _ in back}))))
        check("what the reader does say about 6.png's back rank is right",
              [g for g, w in back if g not in ("?", w)], [])
        lying = [[(".", None)] * 8 for _ in range(8)]
        lying[0][0] = ("R", 0.9)          # a white rook where a8 holds a black one
        check("  but where it does disagree it stops the press, and says so",
              refused(E.opening_slots, b6, lying),
              "the reader reads R where the opening puts r")
        unsure = [[("?", None)] * 8 for _ in range(8)]
        check("  and a reader that will not name anything cannot veto",
              refused(E.opening_slots, b6, unsure), None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------- the button, driven

def button():
    print("\n-- the button itself, without a window --------------------")
    b1, b6 = board_of("1"), board_of("6")
    bundled = P.PieceReader()

    # _opening touches the board, the bookkeeping and the redraw and nothing
    # else, so stand-ins are enough to run the real method without a window.
    scored = E.beliefs(bundled, b6)
    lab = E.Labels(scored)
    lab.slots.clear()
    drawn = []
    win = types.SimpleNamespace(board=b6, scored=scored, labels=lab,
                                _redraw=lambda: drawn.append(True))
    E.Enroller._opening(win)
    check("the button fills all twelve in and redraws",
          (sorted(lab.slots), lab.samples(), drawn),
          (sorted(P.ORDER), 32, [True]))
    check("  and says what it did and did not check",
          lab.hint(), "took all 32 squares. What was checked is that nothing "
                      "has moved, not which piece is which.")

    # Work done by hand is not thrown away silently. The seed is not work, it
    # is the reader's own proposal, so it is not counted.
    lab = win.labels = E.Labels(scored)
    seeded = lab.samples()
    lab.square(0, 0)
    lab.symbol("r")
    lab.square(7, 4)
    lab.symbol("K")
    # One of the two clicks lands on a square the reader now proposes on this
    # set, so it replaces a seeded slot rather than adding one. What the row is
    # for is that the two counts stay apart, which they do.
    check("clicking is counted apart from what the reader proposed",
          (len(lab.by_hand), seeded), (1, 5))
    E.Enroller._opening(win)
    check("  and the opening says how much of it it replaced",
          lab.hint(), "took all 32 squares, over the 1 you had clicked. "
                      "Which piece is which was not checked.")
    check("  after which there is nothing clicked left to replace",
          (lab.by_hand, lab.samples()), (set(), 32))

    win.board, win.scored = b1, E.beliefs(bundled, b1)
    win.labels = lab = E.Labels(win.scored)
    held = {s: {light: list(sq) for light, sq in here.items()}
            for s, here in lab.slots.items()}
    E.Enroller._opening(win)
    check("on a board that is not the opening it takes nothing",
          (lab.slots, lab.hint()), (held, NOT_OPENING))
    check("  and the next click clears the message",
          (lab.square(1, 1), lab.hint().startswith("that square reads"))[1],
          True)


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
        picked = [(s, rc) for s, here in seeded.items()
                  for squares in here.values() for rc in squares]
        check("  and no square classify() refused is seeded",
              [rc for _, rc in picked if rc not in named], [])
        right = sum(1 for s, (r, c) in picked if truths[name][r][c] == s)
        pairs = sum(1 for here in seeded.values() if len(here) == 2)
        print("      %s.png: proposes %d of 12 pieces, %d on both colours, "
              "%d squares, %d of them right"
              % (name, len(seeded), pairs, len(picked), right))
        check("  and every square it does propose holds that piece",
              right, len(picked))

    seeded1 = E.seed_slots(E.beliefs(bundled, boards["1"]))
    check("on a set it can read there is nothing left to click",
          len(seeded1), 12)
    # Eight of the twelve types stand on both square colours in that position.
    # The two kings and the two queens have one square each, so four cannot be
    # paired from any board, and are the ones written into both halves.
    check("  and it proposes both colours wherever both are there",
          sum(1 for here in seeded1.values() if len(here) == 2), 8)
    # It used to propose almost nothing here, which is what issue #20 was.
    # The correlation reader names ten of that set's thirty two pieces off the
    # bundled sheet, so the seed is worth having and the person still has the
    # rest to click.
    seeded6 = E.seed_slots(E.beliefs(bundled, boards["6"]))
    check("on the set from issue #20 it proposes some of it and not all",
          (4 <= len(seeded6) <= 10, len(seeded6) < 12), (True, True))

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

    lab.slots.clear()

    # a8 is light, h8 is dark, both hold a black rook.
    lab.square(0, 0)
    check("a square clicked on its own just waits", (lab.sel, lab.pending),
          ((0, 0), None))
    lab.symbol("r")
    check("  and the piece named next is taken from it",
          lab.slots["r"], {True: [(0, 0)]})
    check("  with nothing left waiting", (lab.sel, lab.pending), (None, None))

    lab.symbol("r")
    lab.square(0, 7)
    check("the other colour is kept alongside, not instead",
          lab.slots["r"], {True: [(0, 0)], False: [(0, 7)]})
    check("  which is what makes it a pair", lab.paired(), ["r"])

    # e8 is light, like a8. This is the click issue #31 is about: it used to
    # replace a8 and leave two squares taught however many were clicked.
    lab.symbol("r")
    lab.square(0, 4)
    check("a second square of a colour it already has is kept as well",
          lab.slots["r"], {True: [(0, 0), (0, 4)], False: [(0, 7)]})
    check("  so every click shows up in the count", lab.samples(), 3)
    lab.symbol("r")
    lab.square(0, 4)
    check("  and clicking a taught square again takes it back",
          lab.slots["r"], {True: [(0, 0)], False: [(0, 7)]})

    # A square holds one piece. Teaching it to a second one has to take it off
    # the first, or one crop is written into two slots and one of them is a
    # wrong template.
    lab.symbol("n")
    lab.square(0, 7)
    check("a square taught to another piece is taken off the first",
          (lab.slots["r"], lab.slots["n"]),
          ({True: [(0, 0)]}, {False: [(0, 7)]}))
    check("  so nothing is ever cut for two pieces at once",
          len(lab.chosen()), lab.samples())
    del lab.slots["n"]
    lab.slots["r"] = {True: [(0, 0)], False: [(0, 7)]}

    lab.symbol("k")
    check("a piece clicked first waits for a square", lab.pending, "k")
    lab.symbol("k")
    check("  clicking it again puts it back down", lab.pending, None)
    lab.symbol("k")
    lab.square(0, 4)
    check("  and the next square clicked answers it",
          lab.slots["k"], {True: [(0, 4)]})
    lab.symbol("k")
    check("the hint asks for the colour that would help, and calls it optional",
          lab.hint(), "now a dark square holding the black king, if there is one")
    lab.pending = None

    check("nothing taught for a piece is what missing means",
          "".join(lab.missing()), "KQRBNPqbnp")
    lab.slots.clear()
    check("  so an empty sheet is all twelve",
          "".join(lab.missing()), P.ORDER)
    check("  and the count says so",
          lab.status().startswith("0 of 12 taught"), True)

    lab.slots.update(slots_from(TRUTH_6))
    check("all twelve taught leaves nothing missing", lab.missing(), [])
    check("  and says it is ready", lab.status().endswith("ready to save"), True)
    check("  and says how many are on both colours",
          lab.status().startswith("12 of 12 taught from 20 squares, "
                                  "%d on both colours" % len(lab.paired())),
          True)
    check("  off the squares it is really cutting from", lab.samples(), 20)
    check("the starting position pairs everything but the kings and queens",
          "".join(s for s in P.ORDER if s not in lab.paired()), "KQkq")
    check("every square it will cut from is a different one",
          len(lab.chosen()),
          sum(len(here) for here in lab.slots.values()))

    # The white king starts on e1, a dark square, and has no light one to pair
    # with. The white pawns cover both colours already.
    lab.pending = "K"
    check("the hint names the colour still worth a click",
          lab.hint(), "now a light square holding the white king, "
                      "if there is one")
    lab.pending = "P"
    check("  and stops asking once a piece has both",
          lab.hint(), "now click the square holding the white pawn")
    lab.slots.pop("P")
    check("  as it does before a piece has any",
          lab.hint(), "now click the square holding the white pawn")
    lab.slots.update(slots_from(TRUTH_6))
    lab.pending = None
    lab.sel = (0, 0)
    check("  and a picked square says what it currently reads",
          lab.hint().startswith("that square reads %s" % scored[0][0][0]), True)
    lab.scored = [[(s, None) for s, _ in row] for row in lab.scored]
    check("  with no invented number when there is none to give",
          lab.hint(), "that square reads %s. Say what it really is."
          % lab.scored[0][0][0])

    # The whole opening at once, and the refusal that goes with it. Which
    # sentence each refusal carries is pinned in shuffled(), against the check
    # that produces it.
    lab = E.Labels(scored)
    lab.slots = {"r": {True: [(0, 0)]}}
    check("a refusal changes nothing and puts its reason in the hint",
          (lab.refuse("no it is not"), lab.slots, lab.hint()),
          (False, {"r": {True: [(0, 0)]}}, "no it is not"))
    lab.square(1, 1)
    check("  until the next click, which clears it",
          lab.hint().startswith("that square reads"), True)
    check("taking the opening takes every square of it",
          (lab.opening(E.opening_slots(b6, scored)), lab.samples()), (True, 32))
    check("  which is twelve taught and nothing waiting",
          (lab.missing(), lab.sel, lab.pending), ([], None, None))


# ---------------------------------------------------------------- the arrow

def arrow():
    print("\n-- the arrow follows the position, not the last reply ----")
    # wanted(on, region, position, advice_for, advice_uci, cleared, flipped,
    #        mine)
    check("nothing is drawn before the engine has answered",
          OV.wanted(True, R1, FEN_A, None, None, None), None)
    check("advice for the position on screen is drawn",
          OV.wanted(True, R1, FEN_A, FEN_A, "e2e4", None),
          (R1, "e2e4", False, True))

    # Fault one. The move lands, the engine has not answered yet, and the old
    # arrow used to stay drawn on the new position until it did.
    check("the move that makes the advice stale takes the arrow down",
          OV.wanted(True, R1, FEN_B, FEN_A, "e2e4", None), None)
    check("  and a late reply for the position before is still refused",
          OV.wanted(True, R1, FEN_C, FEN_B, "e7e5", None), None)
    check("  advice for the position now on screen is what comes back",
          OV.wanted(True, R1, FEN_B, FEN_B, "g1f3", None),
          (R1, "g1f3", False, True))

    # Fault two. The window moved or was resized. The advice is still true, so
    # the arrow moves with the board rather than hiding or staying put.
    check("moving the board moves the arrow with it",
          OV.wanted(True, R2, FEN_B, FEN_B, "g1f3", None),
          (R2, "g1f3", False, True))
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
          (R1, "b1c3", False, True))

    # The opponent's move is drawn too, in the other colour. Which side the
    # advice was for travels with it, so the answer says what to paint and the
    # caller does not work it out again a frame later.
    print("\n-- whose move it is, which is the colour ------------------")
    check("your move comes back as yours",
          OV.wanted(True, R1, FEN_B, FEN_B, "g1f3", None, False, True)[3], True)
    check("and theirs as theirs",
          OV.wanted(True, R1, FEN_B, FEN_B, "g1f3", None, False, False)[3],
          False)
    check("the same move for the other side is a different answer, so a"
          " caller cannot miss it",
          OV.wanted(True, R1, FEN_B, FEN_B, "g1f3", None, False, True)
          == OV.wanted(True, R1, FEN_B, FEN_B, "g1f3", None, False, False),
          False)
    check("and a stale position is still nothing at all, whoever it was for",
          OV.wanted(True, R1, FEN_C, FEN_B, "g1f3", None, False, False), None)
    # None means "no position", and it turns up on both sides: the sentinel
    # for nothing cleared, and the position when there is none on screen. Two
    # Nones matching each other must not read as a match.
    check("advice about no position at all is not drawn",
          OV.wanted(True, R1, None, None, "e2e4", None), None)
    check("  nor is advice whose own position is unknown",
          OV.wanted(True, R1, FEN_A, None, "e2e4", None), None)


def coach_side():
    """The real _drain_coach, handed one reply, asked which side it drew for.

    The colour is decided in two steps, and wanted() above only covers the
    second. This is the first: whose move the engine answered for. Getting it
    backwards draws your plan in the opponent's colour and labels it theirs,
    which no check that only looks at the arrow rule would notice.

    No window. _sync_arrow stops at a board with no arrow built on it, so the
    real method runs and only the state it wrote is read back.
    """
    print("\n-- the side the coach's reply is drawn for ----------------")
    import chesswatch as CW

    def drained(turn, yours):
        app = types.SimpleNamespace(
            coach=types.SimpleNamespace(out=queue.Queue()),
            coach_on=types.SimpleNamespace(get=lambda: True, set=lambda v: None),
            coach_fen=FEN_A, my_colour=yours, arrow=None, arrow_uci=None,
            arrow_fen=None, arrow_mine=None, arrow_cleared=None, region=R1,
            flipped=False, lbl_coach=Blank(), lbl_detail=Blank(),
            arrow_on=types.SimpleNamespace(get=lambda: True))
        app._show_arrow = lambda *a: CW.App._show_arrow(app, *a)
        app._sync_arrow = lambda: CW.App._sync_arrow(app)
        app.coach.out.put(("advice", {
            "fen": FEN_A, "over": False, "final": True, "depth": 12,
            "turn": turn, "san": "Nf3", "uci": "g1f3",
            "text": "knight: g1 to f3", "score": "+0.4"}))
        CW.App._drain_coach(app)
        return app.arrow_mine, app.lbl_coach.text.split("  ")[0]

    check("white to move with white at the bottom is your move",
          drained("white", "white"), (True, "your move"))
    check("  and black to move on the same board is theirs",
          drained("black", "white"), (False, "their move"))
    check("the other way round when you are the one playing black",
          drained("black", "black"), (True, "your move"))
    check("before the orientation settles nothing is yours yet",
          drained("white", None), (False, "their move"))


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
          (R1, "e2e4", False, True))
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
        #
        # Sleeping POLL_SECONDS is what makes this the real cadence. With no
        # board anywhere the hunt now backs off by the clock, so a loop that
        # spends no wall time at all would step through the whole loop inside
        # one gap and prove nothing about how long you actually wait.
        #
        # Thirty ticks rather than twenty, so the bound holds in the worst case
        # and not just this one. The longest gap in the table is 2.0s and a
        # tick is POLL_SECONDS, so twenty ticks is 2.4s of margin over a 2.0s
        # wait, and a run that started the wait a moment earlier would fail on
        # timing alone.
        desk.there = True
        misses = worker._misses
        back, began = None, time.time()
        for i in range(30):
            worker._tick()
            if worker.region is not None:
                back = i + 1
                break
            time.sleep(CW.POLL_SECONDS)
        print("      back on tick %s of 30, %.2fs after it returned, %d"
              " misses into the backoff" % (back, time.time() - began, misses))
        check("the board coming back is picked up again",
              back is not None and back <= 30, True)
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
        show_board=types.SimpleNamespace(get=lambda: False),
        think_choice=types.SimpleNamespace(get=lambda: "1s"),
        taught_sheet=None,
        lbl_check=Blank(),
        worker=types.SimpleNamespace(reader=reader))
    app._think_seconds = lambda: CW.App._think_seconds(app)
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


# ------------------------------------------------ the teach panel's width

# Tk turns a positive point size into pixels through `tk scaling`, which on
# Windows is the display dpi over 72. The app calls SetProcessDpiAwareness(2),
# so the font grows with the display scaling while the window does not.
PX = {"100%": round(8 * 96 / 72), "150%": round(8 * 144 / 72)}

# Chrome drawn round the text, low and high. This is bounded rather than
# measured: it cannot be measured without a window. Only the high end is
# load bearing, since that is the one that has to fit.
CHROME = {"btn": (10, 24), "lbl": (0, 6), "check": (18, 30), "radio": (18, 30)}

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


def teach_panel():
    """Only the teach window is measured here. The main window's own rows
    used to be, off a table of labels written into this file; they are
    layouttest.py's now, read off App._build rather than copied."""
    print("\n-- the teach panel's new button fits ---------------------")
    if not os.path.exists(FONT_PATH):
        print("SKIP  no Segoe UI on this machine, cannot measure the row")
        return
    # The teach window is its own window, and the width of its panel is set by
    # the two columns of piece buttons. Tk sizes a button given width=7 to
    # seven of the font's average characters, which is what "0" measures, so
    # the new button has to fit inside two of those to leave the panel alone.
    button = [("btn", "it is the opening")]
    columns = [("btn", "0" * 7), ("btn", "0" * 7)]
    for name, px in PX.items():
        print("      the opening button at %s: %d to %d px, in the %d to %d "
              "the piece buttons already ask for"
              % ((name,) + row_px(button, px) + row_px(columns, px)))
    # Both are buttons, so whatever chrome Tk draws round this one it draws
    # round each of the two above it, and the comparison comes down to the text.
    check("the opening button fits inside the two columns it spans",
          [row_px(button, px)[1] <= row_px(columns, px)[1]
           for px in PX.values()], [True, True])
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
                        "_show_arrow", "_sync_arrow", "_use_taught", "_build")}

    check("_render finishes every frame by syncing the arrow",
          src["_render"].rstrip().endswith("self._sync_arrow()"), True)
    check("_sync_arrow asks overlay.wanted and nothing else",
          len(calls_of(src["_sync_arrow"], "wanted")), 1)
    check("_render drops a clear that has outlived its position",
          "self.arrow_cleared = None" in src["_render"], True)
    check("_drain hides the arrow when the worker says it is searching",
          "self.region = None" in src["_drain"]
          and "self._sync_arrow()" in src["_drain"], True)
    check("the coach hands the arrow the position its move was for,"
          " and whose move it is",
          max(calls_of(src["_drain_coach"], "_show_arrow")), 3)
    sig = inspect.signature(CW.App._show_arrow)
    check("  and _show_arrow takes the position and the side",
          list(sig.parameters), ["self", "uci", "fen", "mine"])
    # Names alone pass whatever the defaults are, and a default on fen is the
    # bug: None is also the "nothing cleared" sentinel, so a call that forgot
    # the position would suppress the arrow for the rest of the session rather
    # than fail.
    check("  with nothing to forget the position with",
          sig.parameters["fen"].default, inspect.Parameter.empty)
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
    check("the think time out of the config file goes through the clamp",
          "CO.nearest_think(" in src["_build"], True)

    esrc = inspect.getsource(E)
    check("enroll builds a root only for its own standalone run",
          esrc.count("tk.Tk()"), 1)
    check("your arrow colour is untouched", OV.YOURS, "#00E8FF")
    check("and one place decides which side gets which",
          (OV.colour_for(True), OV.colour_for(False)), (OV.YOURS, OV.THEIRS))
    for spec, who in ((OV.YOURS, "yours "), (OV.THEIRS, "theirs")):
        grey = Image.new("RGB", (1, 1), spec).convert("L").getpixel((0, 0))
        check("  %s greys into the band the reader ignores" % who,
              W.DARK < grey < W.BRIGHT, True)


# The board of #54 and #55, as it stands in testdata/7.png. A middlegame in a
# set the bundled sheet leaves seven squares of unread, holding eight of the
# twelve piece types, which is the joined-game case #50 is for.
TRUTH_7 = ["..b..r..", "..p.....", "..p.p...", "..NpP.k.",
           "PP.P....", ".....PP.", "........", "R.....K."]


def taught_7(tmp):
    """A sheet taught off 7.png, the four types not standing on it carried over
    from the bundled sheet the way write_sheet carries them."""
    return E.write_sheet(board_of("7"), slots_from(TRUTH_7),
                         os.path.join(tmp, "7.png"))


def one_picture():
    print("\n-- refusing a sheet that holds one piece twice -------------")
    b7 = board_of("7")
    tmp = tempfile.mkdtemp()
    try:
        good = taught_7(tmp)
        check("a sheet taught off a real board holds twelve pieces",
              E.confusable(good), None)
        check("  and the bundled sheet does too",
              E.confusable(P.TEMPLATE_SHEET), None)

        # #54 as it was reported: the black king's square clicked while the
        # knight button was selected, which the window accepts in silence.
        wrong = slots_from(TRUTH_7)
        wrong["n"] = {False: [(3, 6)]}
        why = refused(E.write_sheet, b7, wrong, os.path.join(tmp, "bad.png"))
        check("a king taught into the knight slot is refused",
              (why or "").startswith("the black king and the black knight"),
              True)
        check("  and the refusal names both pieces and the way back",
              ("--reset" in (why or ""), os.path.exists(
                  os.path.join(tmp, "bad.png"))), (True, False))

        # Carrying a piece over from the sheet in use is what #50 added and it
        # has to keep working: eight taught here, four carried.
        short = {s: v for s, v in slots_from(TRUTH_1).items() if s in "KQRBNP"}
        check("carrying the other colour over is still allowed",
              refused(E.write_sheet, board_of("1"), short,
                      os.path.join(tmp, "half.png"), None), None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def put_back():
    print("\n-- putting one piece back ---------------------------------")
    tmp = tempfile.mkdtemp()
    try:
        path = taught_7(tmp)
        before = Image.open(path).convert("RGB")
        # A sheet already carrying the bad slot, which is what the reporter had
        # on disk and what teaching could not reach: his board has no black
        # knight to point at.
        sick = Image.open(path).convert("RGB")
        for half in (0, P.PLAIN_SLOTS):
            sick.paste(slot(before, "k", half == 0),
                       ((half + P.ORDER.index("n")) * P.TEMPLATE_PX, 0))
        sick.save(path)
        check("the sheet on disk cannot tell the two apart",
              E.confusable(path), ("k", "n"))

        E.reset_slot("n", path)
        after = Image.open(path).convert("RGB")
        check("the knight goes back to the bundled template",
              E.confusable(path), None)

        def moved(one, two):
            return sorted({s for s in P.ORDER for light in (True, False)
                           if ImageChops.difference(
                               slot(one, s, light),
                               slot(two, s, light)).getbbox()})

        check("  and it is the only slot that moved", moved(sick, after), ["n"])
        # The eight of the twelve that were taught off the board are in here,
        # which is the half of #54 that deleting taught.png cannot do.
        check("  the other eleven are where teaching left them",
              moved(before, after), [])
        check("  the sheet still loads", P.PieceReader(path).ready, True)
        check("  and still reads the board it was taught from",
              tally(P.PieceReader(path), board_of("7"), TRUTH_7), (64, 0, 0))

        check("resetting a piece that is not one is refused",
              refused(E.reset_slot, "x", path) is not None, True)
        gone = os.path.join(tmp, "nothing.png")
        check("resetting a sheet that is not there is refused",
              refused(E.reset_slot, "n", gone) is not None, True)
        check("the command line route runs without a window",
              (E.reset_main(["n"], path), E.reset_main([], path)), (0, 1))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def which_way_up():
    print("\n-- which way up, off the pieces ---------------------------")
    tmp = tempfile.mkdtemp()
    try:
        reader = P.PieceReader(taught_7(tmp))
        b7 = board_of("7")
        check("the taught sheet reads all 64 squares of 7.png",
              tally(reader, b7, TRUTH_7), (64, 0, 0))

        # The acceptance test of #55. One frame, no move of any kind.
        rows, _ = reader.classify(b7)
        check("three black at the top and two white at the bottom says white "
              "is at the bottom", W.facing(rows), False)
        cold = W.BoardTracker(directory=tmp, reader=reader)
        note = cold.check(b7)
        check("  so the first frame no longer asks which way up",
              "which way up" in note, False)
        check("  and it is not guessing whose turn it is either",
              cold.locked_on, False)

        # The one move it still wants is any move, where before this it was a
        # pawn move specifically. This one is a rook.
        board = W.board_from_grid([list(row) for row in TRUTH_7],
                                  False, chess.BLACK)
        ren = Renderer(shot("7"), W.find_board(
            Image.open(shot("7")).convert("RGB")), board=board)
        board.push_san("Rf7")
        cold.check(ren.render(board))
        check("a rook move settles it", cold.locked_on, True)
        check("  the right way up", cold.flipped, False)
        check("  at the right position",
              cold.board.board_fen(), board.board_fen())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    no_windows()
    sheet()
    one_picture()
    put_back()
    which_way_up()
    partial()
    averaging()
    opening()
    shuffled()
    button()
    knows()
    labels()
    arrow()
    coach_side()
    clear_across_games()
    board_goes_away()
    persistence()
    teach_panel()
    wiring()
    print("\n%d/%d passed" % (sum(bool(x) for x in R), len(R)))
    return 0 if all(R) else 1


if __name__ == "__main__":
    sys.exit(main())
