"""Teach the reader your piece set by hand.

Run:  python enroll.py [screenshot.png]

The reader normally learns the twelve piece shapes off a starting position, for
free and exactly at your window size. That needs all twelve types on the board
at once, so a game joined part way through never gets one, and on a piece set
the bundled sheet has never seen such a game reads almost nothing. This is the
way out of that corner: point at one piece of each type and say what it is.

It opens with the reader's own answer already filled in, a letter per square and
a score where there is one to give, so only the squares it got wrong need
touching. On a set it already reads that is a few clicks; on a set it cannot
read at all it is twelve. The letters come from classify(), the same call the
recorder trusts, so a square shown with a piece on it is one the recorder would
also name, and a square it refuses is never offered as a template.

A board sitting in the opening needs no clicks at all: "it is the opening"
takes all thirty two pieces at once. Three things about the picture are checked
before it is acted on, because a board that is not the opening would teach
twelve wrong templates and the reader names squares confidently off whatever it
was taught. What none of them check is which piece is which, which is the whole
reason this tool exists, so the guarantee is narrower than the button: nothing
has moved, and the board is laid out the way the opening lays one out.

Every square clicked is kept and averaged into its slot rather than replacing
what was there, so a fourth pawn is a fourth sample and not a lost click.

Saving writes the same twelve slot PNG make_templates.py writes, because that
is the only format the reader loads. Nothing here changes pieces.png.
"""

import os
import sys
import tkinter as tk

import chess
from PIL import Image, ImageTk

import watcher as W
import pieces
from pieces import ORDER, TEMPLATE_PX, PAIRED_SLOTS

# chesswatch is imported for the screen grab and the colours, and it is safe
# from here because chesswatch only imports this file inside the button that
# opens the window.
import chesswatch as C

APP_DIR = os.path.dirname(os.path.abspath(__file__))
TAUGHT_SHEET = os.path.join(APP_DIR, "taught.png")

NAMES = {"K": "king", "Q": "queen", "R": "rook",
         "B": "bishop", "N": "knight", "P": "pawn"}

# How much brighter the ink on one half of a board has to be than the ink on
# the other before "starting position" will say which way round it is drawn.
# Measured on the two sets there are fixtures for, at every window size and
# either way up: chess.com's own separates by 0.77 and the flat set of 6.png,
# which is the harder one, by 0.50. A set whose two colours do not separate by
# this much is refused rather than guessed at.
OPENING_INK_GAP = 0.25

# Which squares the opening covers, as True and False rather than letters. The
# same both ways up, which is why one of the two is enough: turning the board
# round changes which pieces are at the top, not which squares are covered.
OPENING_OCCUPANCY = [[cell != "." for cell in row]
                     for row in W.occupancy_of(chess.Board())]


# ------------------------------------------------------------------ pure part
# Everything the window does to the pixels lives here, so enrolltest.py can
# check it without a display.

def board_of(img):
    """Crop a screenshot down to the board. A picture that is already a board
    comes back untouched, which is what a caller who cropped it has."""
    rect = W.find_board(img)
    if not rect:
        return img
    x, y, size = rect
    return img.crop((x, y, x + size, y + size))


def light_square(row, col):
    """True when a screen square is a light one. a8 and h1 are both light, so
    screen parity says which colour a square is whichever way round the board
    is being looked at."""
    return (row + col) % 2 == 0


def _scores(reader, board_img):
    """A number per square for how close the reader's call was, or None if this
    build of pieces.py does not hand one out.

    Only ever a decoration. It comes from the module's private per square
    decision, which does not carry the board-measured levels or the trust gate
    that classify() applies on top, so it can be generous where classify() is
    not. That is why nothing downstream is allowed to read the symbol off it.
    """
    try:
        out = [[None] * 8 for _ in range(8)]
        for r, c, sq in pieces.squares(board_img):
            out[r][c] = float(pieces._decide(sq, reader.templates)[1])
        return out
    except Exception:
        return None


def beliefs(reader, board_img):
    """What the reader thinks is on each square, as an 8x8 grid of
    (symbol, score). Symbol is a piece letter, "." for empty, or "?" when the
    reader will not name it. Score is None when there is no number to give.

    The symbol always comes from classify(), never from the scorer. classify()
    is where the trust gate lives, and the gate only bites on a foreign piece
    set, which is the one case this whole tool exists for. Showing a confident
    letter the recorder itself would refuse, and then seeding a template off
    it, is how a wrong crop would get into the permanent record.
    """
    rows, _ = reader.classify(board_img)
    scores = _scores(reader, board_img)
    return [[(rows[r][c], scores[r][c] if scores else None)
             for c in range(8)] for r in range(8)]


def seed_slots(scored):
    """The reader's own pick of which squares to cut each piece from: for every
    symbol it named, the square it scored highest on of each colour.

    This is the half that turns twelve labels into a correction. A reader that
    names nothing seeds nothing and the window opens empty, which is the honest
    picture of what it knows.

    A pair of opposite colours is what is worth having, so both are seeded
    whenever the reader named the piece on both. See write_sheet.
    """
    best = {}
    for r in range(8):
        for c in range(8):
            symbol, score = scored[r][c]
            if symbol in (".", "?"):
                continue
            # No score at all still seeds, first square wins. A square without
            # a number was still named by classify(), which is the part that
            # decides whether a square may be seeded at all.
            rank = -1.0 if score is None else score
            here = best.setdefault(symbol, {})
            light = light_square(r, c)
            if light not in here or rank > here[light][0]:
                here[light] = (rank, (r, c))
    # One square per colour, in the list of squares per colour the window
    # keeps, because a seed is a proposal and every click after it is another
    # sample of the same piece.
    return {symbol: {light: [rc] for light, (_, rc) in here.items()}
            for symbol, here in best.items()}


def opening_view(board_img):
    """Which way round this board is drawn, if it is the starting position at
    all. False means white is at the bottom of the screen and True means black
    is, which is what grid_of calls flipped. A board that will not be taken as
    the opening raises ValueError saying which of the three checks stopped it,
    the way write_sheet refuses a sheet it will not write.

    All three are answered off the pixels rather than off the reader, because
    the set this is for is one the reader cannot read.

    Which squares are covered says nothing has moved. Every square of the outer
    two ranks holds something and the middle four hold nothing. On a board
    carrying all thirty two pieces that proves no pawn has moved, since a pawn
    leaves its rank only onto an empty middle one or by capturing and a capture
    leaves thirty one pieces. It proves nothing about the pieces behind the
    pawns, which can be shuffled or swapped and still stand on those squares.

    Whether the ends of each back rank match is what rules out a shuffled one.
    The opening puts the same piece at each end: rooks on a and h, knights on b
    and g, bishops on c and f. So of the three squares at one end, each has to
    be a better match for the square it mirrors than for either of the others,
    and the same the other way round, which is a comparison of one square on
    this board against another and so needs no template and no threshold.
    Chess960 is why this is here. It keeps all thirty two pieces on the outer
    ranks, so its occupancy and its ink are the opening's exactly, and mapping
    a standard board onto position #300 takes fourteen of its thirty two
    squares from the wrong piece.

    Which half is inked brighter says which way up it is. That is the same
    bright against dark test _judge already applies to a square, taken sixteen
    squares at a time because one square does not settle it: on the flat set of
    6.png four of the white pieces on the back rank read darker than they are
    bright, the a1 rook at 281 bright against 323 dark, while its half of the
    board reads 0.57 bright against the other half's 0.07.

    What none of this checks is which piece is which. Drawn in both fixture
    sets, at capture size and at 400 pixels, 948 of the 959 non-standard
    chess960 positions are refused in three of those four and 942 in the
    fourth. What is taken in all four is the 11 whose back rank mirrors the
    opening's outside the king and queen, which no comparison of one end
    against the other can tell from the opening. So are a king and queen
    swapped, and the knights swapped for each other's colour, which is
    reachable in a legal standard game. A set that draws its black pieces
    brighter than its white ones is taken upside down. None of those is caught
    here, and the only thing standing in their way is the person seeing where
    the twelve letters landed before pressing save.
    """
    feats = pieces._board_features(board_img, pieces._levels(board_img))
    holds = [[f is not None and f.coverage >= W.MIN_COVERAGE
              for f in feats[row * 8:row * 8 + 8]] for row in range(8)]
    if holds != OPENING_OCCUPANCY:
        raise ValueError("this board is not in the starting position")
    for row in (0, 7):
        left = [feats[row * 8 + col] for col in (0, 1, 2)]
        right = [feats[row * 8 + 7 - col] for col in (0, 1, 2)]
        table = [[pieces._score(a, b) for b in right] for a in left]
        # Strictly better, not merely as good. A pairing that is only joint
        # best is a pairing the pixels did not settle, and it is no evidence
        # that the board is laid out this way.
        for i in range(3):
            if any(table[i][j] >= table[i][i] or table[j][i] >= table[i][i]
                   for j in range(3) if j != i):
                raise ValueError("the ends of the back rank do not match, so "
                                 "this is not the standard opening")
    shares = []
    for rows in ((0, 1), (6, 7)):
        here = [feats[r * 8 + c] for r in rows for c in range(8)]
        # Every one of these squares passed MIN_COVERAGE, which counts bright
        # and dark together, so there is always ink here to take a share of.
        bright = sum(f.bright for f in here)
        shares.append(bright / (bright + sum(f.dark for f in here)))
    top, bottom = shares
    if abs(top - bottom) < OPENING_INK_GAP:
        raise ValueError("which way round this board is drawn cannot be told "
                         "from its two halves")
    return top > bottom


def opening_slots(board_img, scored):
    """Every piece of a starting position at once, keyed the way Labels keeps
    them. A board that will not be taken raises ValueError saying why.

    This is the common case the twelve clicks were being spent on: a board
    sitting in the opening on a set the reader cannot read. The position is
    known, so nothing new is read off the picture and this is a mapping.

    scored is the reader's own answer and it is used only to refuse. Where the
    reader named a piece and the opening disagrees, one of the two is wrong and
    neither is worth a template. It is a bonus rather than a guard: on a set it
    cannot read it names nothing and so says nothing, and that is the case this
    control exists for. On 6.png it names 8 of the 64 squares, all of them
    pawns, and has no opinion at all about the back rank where a shuffled
    position differs. opening_view is what covers that.
    """
    flipped = opening_view(board_img)
    grid = W.grid_of(chess.Board(), flipped)
    out = {}
    for row in range(8):
        for col in range(8):
            symbol = grid[row][col]
            seen = scored[row][col][0]
            if seen not in (".", "?") and seen != symbol:
                raise ValueError("the reader reads %s where the opening puts %s"
                                 % (seen, symbol))
            if symbol != ".":
                out.setdefault(symbol, {}).setdefault(
                    light_square(row, col), []).append((row, col))
    return out


class Labels:
    """Which squares each of the twelve pieces will be cut from, and the three
    ways a person arrives at that.

    Every square clicked is kept. Several squares of one piece on one square
    colour are averaged into that piece's slot, which is what the reader does
    with the frames the recorder folds in anyway, so a third and a fourth pawn
    are a third and a fourth sample. One square of each colour used to be the
    ceiling and a third click silently replaced one of the first two.

    Clicking a taught square again takes it back. That is the only way to undo
    a mis-click now that a second click no longer overwrites the first, and a
    mis-click is a wrong sample averaged into a template, which is the failure
    this whole tool is built to avoid.

    Kept apart from the window on purpose. This is all the bookkeeping there
    is, so it can be checked without opening anything, and the window is left
    with nothing to do but draw whatever this says.
    """

    def __init__(self, scored):
        self.scored = scored
        self.slots = seed_slots(scored)
        self.sel = None          # a square waiting to be told what it holds
        self.pending = None      # a piece waiting to be shown where it is
        self.note = None         # what the last press did, when it needs saying
        # Squares clicked rather than seeded, which is the work the opening
        # replaces. The seed is the reader's own proposal and is no loss.
        self.by_hand = set()

    def _forget(self, row, col):
        """Take a square away from whatever piece was being cut from it. A
        square holds one piece, and the same crop written into two slots is a
        wrong template in one of them."""
        for symbol, here in list(self.slots.items()):
            for light, squares in list(here.items()):
                if (row, col) in squares:
                    squares.remove((row, col))
                    if not squares:
                        del here[light]
            if not here:
                del self.slots[symbol]

    def _teach(self, symbol, row, col):
        """One more square this piece is cut from, or one fewer when it is
        already one of them."""
        held = (row, col) in self.slots.get(symbol, {}).get(
            light_square(row, col), ())
        self._forget(row, col)
        if held:
            self.by_hand.discard((row, col))
            return
        self.slots.setdefault(symbol, {}).setdefault(
            light_square(row, col), []).append((row, col))
        self.by_hand.add((row, col))

    def opening(self, slots):
        """Take a whole starting position at once, replacing everything.

        Replacing rather than adding, because the opening is an answer for all
        thirty two squares and a click that disagrees with it is a mis-click.
        Squares clicked by hand are said out loud on the way out, since a press
        that silently threw away someone's work would be the same fault as the
        click that used to be silently discarded.

        What is said either way is what was checked. Nothing here knows which
        piece is which, and a button that reads like it does would be a promise
        the code does not keep.
        """
        dropped = len(self.by_hand)
        self.slots = slots
        self.sel = self.pending = None
        self.by_hand = set()
        if dropped:
            self.note = ("took all 32 squares, over the %d you had clicked. "
                         "Which piece is which was not checked." % dropped)
        else:
            self.note = ("took all 32 squares. What was checked is that "
                         "nothing has moved, not which piece is which.")
        return True

    def refuse(self, why):
        """Say why a press did nothing. Nothing is changed, which leaves
        anything already taught exactly where it was."""
        self.note = why
        return False

    def square(self, row, col):
        """A square either answers the piece that is waiting for one, or
        becomes the square waiting for a piece. Both orders work because there
        is no telling which way round someone will click."""
        self.note = None
        if self.pending:
            self._teach(self.pending, row, col)
            self.pending = self.sel = None
        else:
            self.sel = (row, col)

    def symbol(self, symbol):
        """A piece either lands on the square already picked, or waits for one.
        Clicking the waiting piece again puts it back down."""
        self.note = None
        if self.sel:
            self._teach(symbol, *self.sel)
            self.sel = self.pending = None
        else:
            self.pending = None if self.pending == symbol else symbol

    def missing(self):
        return [s for s in ORDER if not self.slots.get(s)]

    def paired(self):
        """The pieces taught on both square colours."""
        return [s for s in ORDER if len(self.slots.get(s, ())) == 2]

    def samples(self):
        """How many squares are being cut from, over all twelve pieces."""
        return sum(len(squares) for here in self.slots.values()
                   for squares in here.values())

    def chosen(self):
        """The square each piece is coming from, keyed the way the board is
        drawn rather than the way the sheet is written."""
        return {rc: s for s, here in self.slots.items()
                for squares in here.values() for rc in squares}

    def hint(self):
        """One line saying what a click will do next, or why the last press
        did nothing."""
        if self.note:
            return self.note
        if self.pending:
            name = ("white " if self.pending.isupper() else "black ") \
                   + NAMES[self.pending.upper()]
            here = self.slots.get(self.pending)
            if here and len(here) == 1:
                return "now a %s square holding the %s, if there is one" % (
                    "dark" if next(iter(here)) else "light", name)
            return "now click the square holding the " + name
        if self.sel:
            symbol, score = self.scored[self.sel[0]][self.sel[1]]
            how = "" if score is None else " at %.02f" % score
            return "that square reads %s%s. Say what it really is." % (symbol,
                                                                       how)
        return ("Click a square, then say what is on it. Green squares are the "
                "ones the sheet will be cut from.")

    def status(self):
        missing = self.missing()
        if missing:
            return "%d of 12 taught   still missing: %s" % (
                12 - len(missing), " ".join(missing))
        # The pair count is shown rather than demanded. Twelve saves, and a
        # piece on both colours reads better than the same piece on one. The
        # square count is there so that a click that landed on a piece already
        # taught is visibly a click that landed.
        return ("12 of 12 taught from %d squares, %d on both colours"
                "   ready to save" % (self.samples(), len(self.paired())))


def _average(crops):
    """Several crops of one piece as the one picture a slot holds.

    A running mean, which is the average _Template keeps over the frames the
    recorder folds in. What it keeps is the part of the piece that was there in
    every sample and what it fades is the part that was not, which on a set
    taught by hand is antialiasing phase and the odd pixel of a neighbour.

    The average has to happen here rather than in the reader because the sheet
    is a picture: twenty four slots, one image each, which is the only format
    pieces.py loads.
    """
    out = crops[0]
    for n, crop in enumerate(crops[1:], start=2):
        out = Image.blend(out, crop, 1.0 / n)
    return out


def write_sheet(board_img, slots, path=TAUGHT_SHEET):
    """Write the paired sheet: the twelve pieces as they look on a light
    square, then the same twelve on a dark one.

    slots maps a piece symbol to {light: [(row, col), ...]}, squares on this
    board holding that piece, row 0 being the top of the screen. All twelve
    symbols are required, one colour each at the least. Several squares of one
    colour are averaged into the one slot the sheet has for them.

    The square colour is why this is 24 slots rather than make_templates.py's
    12. One crop per piece meant a rook taught from a light square was the only
    rook a dark square rook could be compared with, and it lost to a pawn
    taught from a dark square: on 6.png the h8 rook scored 0.518 as a pawn and
    0.413 as a rook, which no threshold reaches because both are honest.

    A piece known on only one colour is written into both halves. That is what
    the picture holds, a king only ever stands on one square, and it leaves
    such a piece exactly where the one slot sheet left it.

    A sheet with a slot left black would not fail to load, it would load as a
    template matching every square, so a missing piece is refused here rather
    than written and discovered later.
    """
    # A colour taught no squares at all is that colour untaught, and saying so
    # here is what keeps the two checks below reading what is really there.
    slots = {symbol: {light: squares for light, squares in here.items()
                      if squares}
             for symbol, here in slots.items()}
    missing = [s for s in ORDER if not slots.get(s)]
    if missing:
        raise ValueError("nothing taught for " + " ".join(missing))
    # The reader measures the two board colours off the sheet itself, so a
    # sheet cut entirely from one colour holds only one and every slot reduces
    # to nothing. Unreachable from a real position, where the two kings alone
    # stand on opposite colours, but the error it would otherwise give is a
    # blank slot a long way from the cause.
    if len({light for here in slots.values() for light in here}) < 2:
        raise ValueError("every square taught is the same colour")
    step = board_img.size[0] / 8.0
    sheet = Image.new("RGB", (TEMPLATE_PX * PAIRED_SLOTS, TEMPLATE_PX))
    for half, light in enumerate((True, False)):
        for i, symbol in enumerate(ORDER):
            here = slots[symbol]
            crops = []
            for r, c in here.get(light) or next(iter(here.values())):
                crop = board_img.crop((int(c * step), int(r * step),
                                       int((c + 1) * step),
                                       int((r + 1) * step)))
                # Resized before the average rather than after, because a board
                # whose size does not divide by eight gives crops a pixel apart
                # in size and those cannot be blended at all.
                crops.append(crop.resize((TEMPLATE_PX, TEMPLATE_PX),
                                         Image.LANCZOS))
            sheet.paste(_average(crops),
                        ((half * len(ORDER) + i) * TEMPLATE_PX, 0))
    # Written beside then moved into place, so an interrupted save leaves the
    # sheet that was already working rather than half of a new one.
    tmp = path + ".tmp"
    sheet.save(tmp, "PNG")
    os.replace(tmp, path)
    return path


def capture():
    """The board that is on screen right now, or None if there is not one."""
    region = C.find_board_on_screen()
    return C.grab(region) if region else None


# ------------------------------------------------------------------ window

class Enroller:
    """The teach window.

    Small on purpose. The board at 44 pixels a square plus a two column strip
    of piece buttons asks for about 530x400, which sits beside a real
    chess.com window instead of covering it.
    """

    SQUARE = 44

    def __init__(self, parent, board_img, reader, on_saved=None,
                 path=TAUGHT_SHEET):
        self.board = board_img
        self.on_saved = on_saved
        self.path = path
        self.scored = beliefs(reader, board_img)
        self.labels = Labels(self.scored)

        # A root of its own only when nobody handed one over, which is the
        # standalone run. Called from the app this is one more child window.
        self.win = tk.Tk() if parent is None else tk.Toplevel(parent)
        self.win.title("Teach the pieces")
        self.win.configure(bg=C.BG)
        self.win.resizable(False, False)
        C.dark_titlebar(self.win)

        side = self.SQUARE * 8
        body = tk.Frame(self.win, bg=C.BG)
        body.pack(fill="both", expand=True, padx=10, pady=10)

        self.canvas = tk.Canvas(body, width=side, height=side, bg=C.BG,
                                highlightthickness=0, cursor="hand2")
        self.canvas.pack(side="left")
        self.canvas.bind("<Button-1>", self._click)
        # Bound to this window rather than to whatever Tk happens to be the
        # default one, so an app that already has a root keeps working.
        self.photo = ImageTk.PhotoImage(
            board_img.resize((side, side), Image.LANCZOS), master=self.win)

        panel = tk.Frame(body, bg=C.BG)
        panel.pack(side="left", fill="y", padx=(10, 0))
        self.buttons = {}
        for col, (colour, case) in enumerate((("white", str.upper),
                                              ("black", str.lower))):
            tk.Label(panel, text=colour, bg=C.BG, fg=C.MUTED,
                     font=("Segoe UI", 8)).grid(row=0, column=col, pady=(0, 2))
            for row, letter in enumerate("KQRBNP"):
                symbol = case(letter)
                btn = tk.Button(panel, text=NAMES[letter], relief="flat",
                                bg=C.PANEL, fg=C.MUTED, cursor="hand2",
                                width=7, font=("Segoe UI", 8),
                                command=lambda s=symbol: self._pick_symbol(s))
                btn.grid(row=row + 1, column=col, padx=1, pady=1)
                self.buttons[symbol] = btn

        # Across both columns rather than beside them, and measured rather
        # than eyeballed: at Segoe UI 8 the label spans 89 to 103 pixels
        # against the two columns' 104 to 132, and at 150% scaling 126 to 146
        # against 155 to 196, so the panel is still as wide as the buttons
        # above make it. See layout() in enrolltest.py.
        #
        # It says what the person is claiming rather than what the program
        # verified, because opening_view cannot check which piece is which.
        self.btn_opening = tk.Button(panel, text="it is the opening",
                                     command=self._opening, relief="flat",
                                     bg=C.PANEL, fg=C.MUTED, cursor="hand2",
                                     font=("Segoe UI", 8))
        self.btn_opening.grid(row=7, column=0, columnspan=2, sticky="we",
                              pady=(6, 0))

        self.lbl_hint = tk.Label(panel, text="", bg=C.BG, fg=C.MUTED,
                                 font=("Segoe UI", 8), wraplength=150,
                                 justify="left", anchor="w")
        self.lbl_hint.grid(row=8, column=0, columnspan=2, sticky="we",
                           pady=(8, 0))
        self.btn_save = tk.Button(panel, text="save", command=self._save,
                                  relief="flat", bg="#3d3a37", fg=C.FG,
                                  cursor="hand2", font=("Segoe UI", 9, "bold"))
        self.btn_save.grid(row=9, column=0, columnspan=2, sticky="we",
                           pady=(8, 0))

        self.lbl_status = tk.Label(self.win, text="", bg=C.BG, fg=C.MUTED,
                                   font=("Segoe UI", 8), anchor="w")
        self.lbl_status.pack(fill="x", padx=10, pady=(0, 8))

        self._redraw()

    # -- drawing -----------------------------------------------------
    def _redraw(self):
        step = self.SQUARE
        cv = self.canvas
        cv.delete("all")
        cv.create_image(0, 0, image=self.photo, anchor="nw")
        chosen = self.labels.chosen()
        for r in range(8):
            for c in range(8):
                x, y = c * step, r * step
                symbol, score = self.scored[r][c]
                if symbol != ".":
                    self._tag(x + 3, y + 2, symbol,
                              C.WARN if symbol == "?" else C.FG, "bold")
                if score is not None and symbol != ".":
                    # Nothing invented. A build with no score to give shows the
                    # letter and no number, rather than a made up 1.00 sitting
                    # where a measurement should be.
                    self._tag(x + step - 3, y + step - 2, "%.02f" % score,
                              C.MUTED, "", anchor="se")
                taught = chosen.get((r, c))
                if taught:
                    cv.create_rectangle(x + 1, y + 1, x + step - 1,
                                        y + step - 1, outline=C.ACCENT,
                                        width=2)
                    self._tag(x + step / 2, y + step / 2, taught, C.ACCENT,
                              "bold", anchor="center", size=13)
        if self.labels.sel:
            r, c = self.labels.sel
            cv.create_rectangle(c * step + 1, r * step + 1,
                                (c + 1) * step - 1, (r + 1) * step - 1,
                                outline="#ffffff", width=2, dash=(3, 2))
        self._status()

    def _tag(self, x, y, text, fill, weight, anchor="nw", size=8):
        """Text on top of a screenshot, with a black copy behind it. The board
        underneath is any colour at all, so one pass in one colour is legible
        on some squares and gone on others."""
        font = ("Segoe UI", size, weight) if weight else ("Segoe UI", size)
        self.canvas.create_text(x + 1, y + 1, text=text, fill="#000000",
                                font=font, anchor=anchor)
        self.canvas.create_text(x, y, text=text, fill=fill, font=font,
                                anchor=anchor)

    def _status(self):
        labels = self.labels
        for symbol, btn in self.buttons.items():
            btn.configure(fg=C.ACCENT if symbol in labels.slots else C.MUTED,
                          bg="#4a4744" if symbol == labels.pending else C.PANEL)
        self.lbl_status.configure(text=labels.status(),
                                  fg=C.MUTED if labels.missing() else C.ACCENT)
        self.lbl_hint.configure(text=labels.hint())

    # -- clicks ------------------------------------------------------
    def _click(self, event):
        col = min(7, max(0, int(event.x // self.SQUARE)))
        row = min(7, max(0, int(event.y // self.SQUARE)))
        self._pick_square(row, col)

    def _pick_square(self, row, col):
        self.labels.square(row, col)
        self._redraw()

    def _pick_symbol(self, symbol):
        self.labels.symbol(symbol)
        self._redraw()

    def _opening(self):
        """Take the board as the starting position, if it is one.

        The refusal is the point of the button as much as the thirty two
        squares are. Nothing is taken on a maybe, and which check refused lands
        in the hint line where the next click will clear it, the same way
        write_sheet's reason for refusing a sheet lands in the status line.
        """
        try:
            slots = opening_slots(self.board, self.scored)
        except ValueError as exc:
            self.labels.refuse(str(exc))
        else:
            self.labels.opening(slots)
        self._redraw()

    def _save(self):
        try:
            write_sheet(self.board, self.labels.slots, self.path)
        except ValueError as exc:
            self.lbl_status.configure(text=str(exc), fg=C.WARN)
            return None
        self.lbl_status.configure(text="wrote " + os.path.basename(self.path),
                                  fg=C.ACCENT)
        if self.on_saved:
            self.on_saved(self.path)
        self.win.after(600, self.win.destroy)
        return self.path

    def run(self):
        self.win.mainloop()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        img = board_of(Image.open(args[0]).convert("RGB"))
    else:
        img = capture()
    if img is None:
        print("No chess board on screen. Pass a screenshot instead:")
        print("  python enroll.py board.png")
        return 1
    Enroller(None, img, pieces.PieceReader()).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
