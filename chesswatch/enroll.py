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

Saving writes the same twelve slot PNG make_templates.py writes, because that
is the only format the reader loads. Nothing here changes pieces.png.
"""

import os
import sys
import tkinter as tk

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
    return {symbol: {light: rc for light, (_, rc) in here.items()}
            for symbol, here in best.items()}


class Labels:
    """Which squares each of the twelve pieces will be cut from, and the two
    ways a person arrives at that.

    Up to one square of each colour per piece, because that is what the sheet
    can carry and what the reader wants. Twelve is still the whole job: a piece
    taught on one colour is written into both halves and is no worse off than
    it was when the sheet held one slot each. A thirteenth click, on the same
    piece standing on the other colour, is what buys the better read.

    Kept apart from the window on purpose. This is all the bookkeeping there
    is, so it can be checked without opening anything, and the window is left
    with nothing to do but draw whatever this says.
    """

    def __init__(self, scored):
        self.scored = scored
        self.slots = seed_slots(scored)
        self.sel = None          # a square waiting to be told what it holds
        self.pending = None      # a piece waiting to be shown where it is

    def _teach(self, symbol, row, col):
        """A second square of the same colour replaces the first, since it is a
        correction. A second of the other colour is kept alongside, since it is
        the extra the pair is made of."""
        self.slots.setdefault(symbol, {})[light_square(row, col)] = (row, col)

    def square(self, row, col):
        """A square either answers the piece that is waiting for one, or
        becomes the square waiting for a piece. Both orders work because there
        is no telling which way round someone will click."""
        if self.pending:
            self._teach(self.pending, row, col)
            self.pending = self.sel = None
        else:
            self.sel = (row, col)

    def symbol(self, symbol):
        """A piece either lands on the square already picked, or waits for one.
        Clicking the waiting piece again puts it back down."""
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

    def chosen(self):
        """The square each piece is coming from, keyed the way the board is
        drawn rather than the way the sheet is written."""
        return {rc: s for s, here in self.slots.items() for rc in here.values()}

    def hint(self):
        """One line saying what a click will do next."""
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
        # piece on both colours reads better than the same piece on one.
        return "12 of 12 taught, %d on both colours   ready to save" % len(
            self.paired())


def write_sheet(board_img, slots, path=TAUGHT_SHEET):
    """Write the paired sheet: the twelve pieces as they look on a light
    square, then the same twelve on a dark one.

    slots maps a piece symbol to {light: (row, col)}, squares on this board
    holding that piece, row 0 being the top of the screen. All twelve symbols
    are required, one colour each at the least.

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
            r, c = here.get(light) or next(iter(here.values()))
            crop = board_img.crop((int(c * step), int(r * step),
                                   int((c + 1) * step), int((r + 1) * step)))
            sheet.paste(crop.resize((TEMPLATE_PX, TEMPLATE_PX), Image.LANCZOS),
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
