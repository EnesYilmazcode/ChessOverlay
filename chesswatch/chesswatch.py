"""ChessWatch - watches the chess.com board on your screen and saves each game
locally as PGN + JSON. Desktop app, no browser extension, nothing leaves the
machine.

Run:  python chesswatch.py
"""

import os
import sys
import json
import time
import queue
import threading
import ctypes

# Must run before Tk exists, so Tk's coordinates match real screen pixels.
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import tkinter as tk
from tkinter import messagebox

import chess
import mss
from PIL import Image

import watcher as W
import pieces
import coach as CO
import overlay as OV

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
POLL_SECONDS = 0.12

# Re-hunt for the board every this many frames while nothing is locked on. Once
# a game is running, re-check this often instead, but only actually re-hunt if
# the region has stopped holding a board, which means the window moved.
REFIND_IDLE = 8
REFIND_CHECK = 6
STILL_A_BOARD = 0.7

# How long to wait between hunts while no board is on screen at all, widening
# with each miss. A hunt grabs the whole desktop and searches it, 120 ms on a
# 3840x1080 screen, and the tick below only ever sleeps its 50 ms floor after
# one. Back to back that is 71% of a core for as long as chess.com is closed.
# Nobody needs a board found inside a tenth of a second of it appearing, so the
# last step is the one that matters and a second is plenty.
IDLE_BACKOFF = (0.0, 0.2, 0.5, 1.0, 1.0, 2.0)

# How often the slower piece-by-piece check runs, in frames. While the fast
# reader is stuck it runs far sooner, because that is exactly when a gap is
# still small enough to be bridged.
CHECK_EVERY = 35
STUCK_CHECK = 6

# Settling. When the board looks different from the last thing we accepted, the
# new reading has to HOLD for this long before it is believed.
#
# This has to be a duration, not a count of agreeing reads. chess.com slides a
# piece to its destination over about 200ms, and for much of that slide it sits
# centred on an intermediate square, which for e2-e4 reads as a clean, stable
# "pawn on e3" for around 86ms. Any number of fast reads inside that window all
# agree with each other, so counting them proves nothing. Outlasting the
# animation is the only thing that separates a real move from a piece in
# flight. A recorder can afford the delay; being wrong is what it cannot
# afford.
SETTLE_HOLD = 0.25
SETTLE_GAP = 0.03
SETTLE_LIMIT = 4.0

# A reading that could be a piece in flight has to hold for much longer.
# Only moves sitting on the path of a longer legal move can be faked that
# way, so this delay is paid on a minority of moves, and never on the
# knight moves and captures that make up most of a game.
SETTLE_HOLD_RISKY = 1.10

# How long the note line keeps something it has nothing else to say. A
# complaint from the piece checker pushes this forward for as long as it is
# still true; a receipt for something you clicked gets this much and no more.
NOTE_SECONDS = 6.0

BG = "#262421"
PANEL = "#302e2b"
FG = "#e8e6e3"
ACCENT = "#7fa650"
MUTED = "#8b8987"
WARN = "#d08a70"

_MSS = getattr(mss, "MSS", None) or mss.mss
_local = threading.local()


# ------------------------------------------------------------------ capture

def _sct():
    """One screen grabber per thread, kept alive between shots.

    Per thread rather than one shared, because mss is not thread safe and both
    the capture worker and the Tk thread take screenshots. Kept alive because
    building one allocates a device context and a bitmap, and the settle loop
    takes several shots per move.
    """
    sct = getattr(_local, "sct", None)
    if sct is None:
        sct = _local.sct = _MSS()
    return sct


def close_sct():
    """Drop this thread's grabber. A worker that has stopped should not keep a
    device context open for a screen nobody is reading."""
    sct = getattr(_local, "sct", None)
    if sct is not None:
        _local.sct = None
        try:
            sct.close()
        except Exception:
            pass


def grab(region):
    """Screenshot one region. region is (left, top, width, height)."""
    left, top, width, height = region
    try:
        shot = _sct().grab({"left": left, "top": top,
                            "width": width, "height": height})
    except Exception:
        # A grabber that has gone bad stays bad. It holds a device context, and
        # unplugging a monitor or changing the resolution invalidates that, so
        # every later shot through the same instance fails the same way.
        # Building one per shot used to heal that for free; dropping it here is
        # what holding on to one costs. The next call builds a fresh one.
        close_sct()
        raise
    return Image.frombytes("RGB", (shot.width, shot.height), shot.bgra,
                           "raw", "BGRX")


def virtual_screen():
    try:
        m = _sct().monitors[0]
    except Exception:
        close_sct()                # same reason as in grab
        raise
    return m["left"], m["top"], m["width"], m["height"]


def find_board_on_screen():
    """Hunt the whole desktop for a chess.com board. Returns a screen region."""
    left, top, width, height = virtual_screen()
    shot = grab((left, top, width, height))
    rect = W.find_board(shot)
    if not rect:
        return None
    x, y, size = rect
    return left + x, top + y, size, size


# How far outside a hand-picked rectangle to look for the board, in pixels.
# Wide enough for a drag that undershot a corner and narrow enough that it
# cannot reach the next thing on the page: it is under half a square on any
# board find_board will look at, which starts at 120 wide.
SNAP_SLOP = 24


def snap_region(region, grabber=None, bounds=None):
    """The board actually inside a hand-picked rectangle, as a screen region.

    A drag is never pixel exact and must not be trusted as one. Every square is
    cut as a fraction of the region, so a region a few pixels off cuts every
    square off centre and drags a strip of its neighbour in with it. Measured
    on the board of issue #52, with the sheet its owner taught: the board read
    exactly leaves one square unreadable, the 540 box he had saved for it
    leaves six, and check() refuses the frame on one.

    The rectangle is handed back untouched when no board is found in it.
    Picking by hand is what exists for a board find_board cannot see, and that
    has to keep working.

    grabber and bounds are for the tests, which have no screen.
    """
    if not region:
        return region
    grabber = grabber or grab
    try:
        bounds = bounds or virtual_screen()
    except Exception:
        return region
    left, top, width, height = region
    sl, st, sw, sh = bounds
    x0, y0 = max(sl, left - SNAP_SLOP), max(st, top - SNAP_SLOP)
    x1 = min(sl + sw, left + width + SNAP_SLOP)
    y1 = min(st + sh, top + height + SNAP_SLOP)
    if x1 - x0 < 120 or y1 - y0 < 120:
        return region
    try:
        shot = grabber((x0, y0, x1 - x0, y1 - y0))
    except Exception:
        return region
    found = W.find_board(shot)
    if not found:
        return region
    x, y, size = found
    return x0 + x, y0 + y, size, size


def dark_titlebar(win):
    if sys.platform != "win32":
        return
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int))
    except Exception:
        pass


class RegionPicker:
    """Dim the desktop and let the user drag a rectangle over it."""

    def __init__(self, parent, prompt):
        self.result = None
        left, top, width, height = virtual_screen()

        self.win = tk.Toplevel(parent)
        self.win.overrideredirect(True)
        self.win.geometry("%dx%d+%d+%d" % (width, height, left, top))
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.35)
        self.win.configure(bg="black")
        self.win.config(cursor="crosshair")

        self.offset = (left, top)
        self.canvas = tk.Canvas(self.win, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(width // 2, 60, fill="white",
                                font=("Segoe UI", 26, "bold"), text=prompt)
        self.canvas.create_text(width // 2, 105, fill="white",
                                font=("Segoe UI", 15),
                                text="Drag a box corner to corner.   Esc to cancel.")

        self.start = None
        self.rect = None
        self.canvas.bind("<Button-1>", self._down)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._up)
        self.win.bind("<Escape>", lambda e: self.win.destroy())
        self.win.focus_force()
        self.win.grab_set()
        parent.wait_window(self.win)

    def _down(self, e):
        self.start = (e.x, e.y)
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(e.x, e.y, e.x, e.y,
                                                 outline=ACCENT, width=3)

    def _drag(self, e):
        if self.start:
            self.canvas.coords(self.rect, self.start[0], self.start[1], e.x, e.y)

    def _up(self, e):
        if not self.start:
            return
        x0, y0 = self.start
        left = min(x0, e.x) + self.offset[0]
        top = min(y0, e.y) + self.offset[1]
        size = min(abs(e.x - x0), abs(e.y - y0))
        if size > 60:
            self.result = (left, top, size, size)
        self.win.destroy()


# ------------------------------------------------------------------ worker

class Worker(threading.Thread):
    """Grabs the board, reads it, and folds it into the tracker."""

    daemon = True

    def __init__(self, region, out, directory=W.GAMES_DIR):
        super().__init__()
        self.region = region
        self.out = out
        self.stop_flag = threading.Event()
        self.reader = pieces.PieceReader()
        self.tracker = W.BoardTracker(directory=directory, reader=self.reader)
        self.manual = region is not None
        self.lost = False          # the region stopped holding a board
        self.check_now = threading.Event()
        self._quiet = 0
        self._frames = 0
        self._since_check = 0
        self._accepted = None      # the last reading we acted on
        self._misses = 0           # hunts in a row that found no board
        self._last_hunt = 0.0
        self._searching = False    # whether the app has been told there is none
        self._board_px = None      # board width the templates were last fitted to
        self.settle_stats = [0, 0]  # readings taken, readings acted on

    def run(self):
        try:
            while not self.stop_flag.is_set():
                started = time.time()
                try:
                    self._tick()
                except Exception as exc:
                    self.out.put(("error", "%s: %s" % (type(exc).__name__, exc)))
                time.sleep(max(0.05, POLL_SECONDS - (time.time() - started)))
        finally:
            close_sct()

    def _tick(self):
        self._frames += 1
        if self._should_refind():
            self._last_hunt = time.time()
            found = find_board_on_screen()
            if found:
                self.region = found
                self._quiet = 0
                self._misses = 0
            elif self.region is None:
                # Only a hunt with no region to fall back on counts towards the
                # backoff. The routine re-hunt runs with one held and comes back
                # empty whenever the board has not moved, and counting those
                # left the first hunt after the region was finally given up
                # waiting the longest gap in the table rather than firing.
                self._misses += 1
            elif self.lost:
                # The rectangle we were watching has stopped holding a board
                # and the hunt found no other one, so let it go. Keeping it
                # went on reading a position off pixels that are no longer a
                # board, and reporting that position every frame for as long as
                # the app ran, which is what left the coaching arrow drawn over
                # whatever took the board's place.
                self.region = None
        if self.region is None:
            # Said once on the way in, not eight times a second for as long as
            # nothing is on screen. The app answers this by reconfiguring two
            # labels and re-syncing the arrow, and none of that changes while
            # the answer stays "still looking".
            if not self._searching:
                self._searching = True
                self.out.put(("searching", None))
            return
        self._searching = False

        shot, occ, settled = self._read_settled()
        if not settled:
            # Still moving. Leave it alone and look again next time round.
            self._quiet += 1
            return
        self._accepted = occ

        event = self.tracker.feed(occ, shot)
        if event:
            self._quiet = 0
            if event == "newgame":
                # Relearn the pieces from your own screen while we know exactly
                # what every square holds.
                self.tracker.learn_pieces(shot)
            self.tracker.game.save()
        else:
            self._quiet += 1

        # A resize is the one moment mid game worth spending a relearn on, and
        # a width already fitted is not worth a second one. New widths arrive
        # only as fast as _should_refind re-hunts the board, so dragging the
        # window edge with a different findable width on screen every single
        # frame measured 7 relearns over 172 frames, at 4.7 ms against a frame
        # of 120. A newgame has just refitted them off a starting position.
        #
        # The width is recorded even when relearn_pieces refuses the frame, so
        # a resize seen while the reader is behind costs one skipped relearn
        # rather than a retry every frame. Skipping is the cheap side: a set
        # learned at another size reads this board at no measured cost.
        resized = self._board_px is not None and shot.size[0] != self._board_px
        self._board_px = shot.size[0]
        if resized and event != "newgame":
            self.tracker.relearn_pieces(shot)

        if self._run_check(shot, event):
            if self.tracker.game and self.tracker.game.moves:
                self.tracker.game.save()

        game = self.tracker.game
        self.out.put(("frame", {
            "event": event,
            "region": self.region,
            "locked": self.tracker.locked_on,
            "rows": game.rows() if game else [],
            "count": len(game.moves) if game else 0,
            "color": game.my_color if game else None,
            "result": game.result if game else "*",
            "termination": game.termination if game else "",
            "outcome": game.won if game else None,
            "path": game.path if game else None,
            "saved": len(self.tracker.finished),
            "joined": bool(game and game.joined_late),
            "board": self.tracker.ascii_board(),
            "fen": self.tracker.board.fen() if self.tracker.board else None,
            "flipped": self.tracker.flipped,
            "check": self.tracker.last_check,
            "templates": self.reader.source,
            "sheet": self.reader.sheet,
        }))

    def _run_check(self, shot, event):
        """The piece-level pass. Runs on a timer, whenever the fast reader has
        been stuck for a while, and whenever you press the button.

        The button also refreshes the templates, always rather than only when
        they look wrong for this board. Nothing on screen says the bundled
        sheet is a poor likeness of your pieces, so a condition would refuse
        the one game that most needs it: joined part way through, still on the
        sheet, with no learned size to be judged stale against.
        """
        asked = self.check_now.is_set()
        self._since_check += 1
        due = (asked
               or self._since_check >= CHECK_EVERY
               or (self.tracker.stuck and self._since_check >= STUCK_CHECK))
        if not due or event:
            return False
        self._since_check = 0
        self.check_now.clear()
        if asked:
            self.tracker.relearn_pieces(shot)
        before = self.tracker.game.moves if self.tracker.game else None
        result = self.tracker.check(shot)
        return result not in ("position confirmed", "board unclear") or \
            (before is not None and self.tracker.game
             and self.tracker.game.moves != before)

    def _read_settled(self):
        """Read the board, and if it has changed, wait for the new picture to
        hold still before believing it. Returns (image, occupancy, settled).

        Nothing is resampled while the board looks the way we left it, so this
        costs one read per tick for almost the whole game and only works hard
        during the moment a piece is actually moving.
        """
        shot = grab(self.region)
        occ = W.read_occupancy(shot)
        self.settle_stats[0] += 1
        if occ == self._accepted:
            return shot, occ, True

        started = time.time()
        since = started
        risky = self.tracker.could_be_mid_move(occ)
        while time.time() - started < SETTLE_LIMIT:
            if time.time() - since >= (SETTLE_HOLD_RISKY if risky else SETTLE_HOLD):
                self.settle_stats[1] += 1
                return shot, occ, True
            time.sleep(SETTLE_GAP)
            fresh = grab(self.region)
            reading = W.read_occupancy(fresh)
            self.settle_stats[0] += 1
            if reading != occ:
                occ, shot, since = reading, fresh, time.time()
                risky = self.tracker.could_be_mid_move(occ)
        return shot, occ, False

    def _should_refind(self):
        """Re-hunt the board while idle, or once a quiet game turns out to be
        pointed at a region that is no longer a board, which is what happens
        when the browser window is moved or resized.

        A region you picked by hand is left alone while it still holds a board,
        but not for ever: a stale pick that no longer points at one would
        otherwise wedge the app permanently.

        Sets self.lost on the way past. A routine re-hunt while idle and a
        region that has stopped holding a board both ask for the same search,
        but only the second is a reason to give the region up when the search
        comes back empty, and _tick needs to tell them apart.

        While no region is held at all the gap between hunts widens with each
        miss. This used to be the caller's job and the caller got it wrong: it
        asked `self.region is None or self._should_refind()`, and the left half
        is true for as long as no board is found, so the backoff on the right
        never got a say and the hunt ran every tick.
        """
        self.lost = False
        if self.region is None:
            # Nothing to lose, so self.lost stays false and the wait is the
            # only question.
            waited = time.time() - self._last_hunt
            return waited >= IDLE_BACKOFF[min(self._misses,
                                              len(IDLE_BACKOFF) - 1)]
        if not self.tracker.locked_on and not self.manual:
            return self._frames % REFIND_IDLE == 0
        if not self._quiet or self._quiet % REFIND_CHECK:
            return False
        try:
            here = grab(self.region)
        except Exception:
            self.lost = True
            return True
        self.lost = W.grid_score(here, 0, 0, self.region[2]) < STILL_A_BOARD
        return self.lost


# ------------------------------------------------------------------ layout

# The window is a stack of stripes, and this is the order they are PACKED in,
# which is not quite the order they appear in. Which of them are up is
# showing()'s decision, taken on plain values so it can be checked without
# opening a window. An empty label still costs a line of height, so a stripe
# with nothing to say is not packed at all rather than packed blank.
#
# Order matters because pack leaves undrawn whatever it reached last and had
# no room for. "foot" and "position" are packed from the bottom before the
# move list is packed at all, so a window too short for everything takes it
# out of the move list, which shrinks visibly, rather than out of the footer,
# which would just be missing. The footer is the only way into the games
# folder now, so it is not allowed to be the one that goes.
#
# "clear" and "advice" are the two halves of the hero row rather than stripes
# of their own, and they are here for the same reason: the order that row is
# packed in decides which of the two survives a 400px window.
STRIPES = ("top", "setup", "hero", "clear", "advice", "detail", "note",
           "side", "result", "foot", "position", "moves")

# The note the watcher writes while it has a game on screen and cannot tell
# which way up the board is. It is the only note with a control that answers
# it, which is why the note is what the side prompt is keyed off. Keyed off
# whether a game is locked on instead, the prompt would also be up through the
# ordinary idle state, and a row that is up at rest is a permanent row.
UNSETTLED = "which way up"


def showing(state):
    """The parts of the window that are up, top to bottom.

    state is plain values, never widgets: what the coach said, what the note
    line holds, whether the drawer is open. Everything optional in here is
    optional because it is empty for most of a game.
    """
    up = {"top", "moves", "foot"}
    if state.get("setup"):
        up.add("setup")
    if state.get("advice"):
        up |= {"hero", "advice"}
        # Nothing to clear until an arrow is actually on the board.
        if state.get("arrow"):
            up.add("clear")
        if state.get("detail"):
            up.add("detail")
    if state.get("note"):
        up.add("note")
        # The wait for a pawn move is the one thing on the note line with a
        # control that ends it, and that control is two clicks away. Offer it
        # here, and only while all three are true: the note still says so, no
        # side has been picked yet, and the drawer is not already showing the
        # same control further up.
        if (UNSETTLED in state["note"] and state.get("side") == "auto"
                and not state.get("setup")):
            up.add("side")
    if state.get("result"):
        up.add("result")
    if state.get("position"):
        up.add("position")
    return tuple(name for name in STRIPES if name in up)


# ------------------------------------------------------------------ app

class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.worker = None
        self.cfg = self._load_config()
        self.board_region = self.cfg.get("board_region")
        self.colour_choice = tk.StringVar(value=self.cfg.get("colour", "auto"))
        self.coach = None            # started the first time it is switched on
        self.coach_fen = None        # the position the advice on screen is for
        self.my_colour = None
        self.arrow = None            # the on-screen arrow, built on demand
        self.arrow_uci = None        # the move the engine last named
        self.arrow_fen = None        # the position it named it for
        self.arrow_mine = True       # and whose move it was, which is the colour
        self.arrow_cleared = None    # a position whose arrow was cleared by hand
        self.arrow_drawn = False     # whether one is on the board right now
        self.teacher = None          # the teach-the-pieces window, if it is open
        self.taught_sheet = self.cfg.get("sheet")   # pieces taught by hand
        self.region = None
        self.flipped = False
        self.setup_open = False      # the drawer, shut until you ask for it
        self.note_until = 0.0        # when the note line goes blank again
        self._shown = ()             # the stripes packed right now

        root.title("ChessWatch")
        root.configure(bg=BG)
        root.geometry("420x620")
        # Shorter than it was. With the drawer shut there are two rows of
        # chrome above the moves rather than five.
        root.minsize(400, 420)

        self._build()
        dark_titlebar(root)
        root.after(120, self._drain)
        # The remembered switches are honoured once the window really exists.
        # Without this they come back ticked at the next launch and do nothing,
        # and the arrow would be built before there is a window to sit over.
        root.after(400, self._apply_saved_switches)
        root.protocol("WM_DELETE_WINDOW", self._quit)
        self._start()

    def _build(self):
        """Build every stripe once. _relayout decides which of them are up."""
        pack = {}          # stripe -> the pack() it is put back with

        top = tk.Frame(self.root, bg=BG)
        pack["top"] = (top, dict(fill="x", padx=12, pady=(12, 8)))
        self.btn = tk.Button(top, text="Stop", command=self._toggle,
                             bg="#b33a3a", fg="white", relief="flat",
                             font=("Segoe UI", 10, "bold"), padx=12, pady=6,
                             activebackground="#8f2f2f", cursor="hand2")
        self.btn.pack(side="left")
        # Packed before the status label although it sits to the right of it.
        # Tk does not clip a widget a row has no room for, it leaves it
        # undrawn, and what pack reaches last is what goes. Putting the button
        # first means a long status loses its own tail instead.
        self.btn_setup = tk.Button(top, text="Setup", command=self._toggle_setup,
                                   relief="flat", bg="#3d3a37", fg=FG,
                                   cursor="hand2", font=("Segoe UI", 9),
                                   padx=8, pady=4)
        self.btn_setup.pack(side="right")
        self.lbl_status = tk.Label(top, text="starting", bg=BG, fg=MUTED,
                                   font=("Segoe UI", 9), anchor="w")
        self.lbl_status.pack(side="left", padx=8, fill="x", expand=True)

        # The drawer. Everything in it is set once and then never again, which
        # is why it is not on screen while a game is being played.
        setup = tk.Frame(self.root, bg=BG)
        pack["setup"] = (setup, dict(fill="x", padx=12, pady=(0, 6)))

        side = self._drawer_row(setup, "Side")
        for word in ("auto", "white", "black"):
            tk.Radiobutton(side, text=word, value=word,
                           variable=self.colour_choice, command=self._set_colour,
                           bg=BG, fg=MUTED, selectcolor=BG, activebackground=BG,
                           activeforeground=FG, font=("Segoe UI", 8),
                           cursor="hand2").pack(side="left")

        show = self._drawer_row(setup, "Show")
        # On by default. The move to play is the reason the program exists and
        # the window is built around it, so off by default meant a first run
        # showed an empty hero line and nothing to say the setting existed.
        # What it costs is a Stockfish process on a machine that wants only
        # the recorder, and one muted line on a machine that has no Stockfish
        # at all. Anyone who does not want it unticks it once and it is
        # remembered, the same as the other two.
        self.coach_on = tk.BooleanVar(value=bool(self.cfg.get("coach", True)))
        self._switch(show, "Coach", self.coach_on, self._toggle_coach)
        self.arrow_on = tk.BooleanVar(value=bool(self.cfg.get("arrow", False)))
        self._switch(show, "Arrow", self.arrow_on, self._toggle_arrow)
        # Off by default now. The board this is a copy of is already on screen
        # beside the window, and eight lines of text repeating it was the
        # biggest thing here that nobody had asked for. Remembered like the
        # other two, or anyone who does want it re-ticks it every launch.
        self.show_board = tk.BooleanVar(value=bool(self.cfg.get("position",
                                                                False)))
        self._switch(show, "Position", self.show_board, self._toggle_position)

        # How long the engine gets. Three values on one line, the same shape
        # as Side above, rather than a drop-down: a menu is two clicks and a
        # block of styling to buy nothing back at three choices.
        think = self._drawer_row(setup, "Think")
        self.think_choice = tk.StringVar(
            value="%gs" % CO.nearest_think(self.cfg.get("think_seconds")))
        for seconds in CO.THINK_CHOICES:
            word = "%gs" % seconds
            tk.Radiobutton(think, text=word, value=word,
                           variable=self.think_choice, command=self._set_think,
                           bg=BG, fg=MUTED, selectcolor=BG, activebackground=BG,
                           activeforeground=FG, font=("Segoe UI", 8),
                           cursor="hand2").pack(side="left")

        board = self._drawer_row(setup, "Board")
        for word, command in (("Pick", self._pick),
                              ("Pieces", self._teach_pieces),
                              ("Recheck", self._check_now)):
            tk.Button(board, text=word, command=command, relief="flat",
                      bg="#3d3a37", fg=FG, cursor="hand2",
                      font=("Segoe UI", 8)).pack(side="left", padx=(0, 6))

        # Where the board was found and which pieces are reading it. Both are
        # diagnostics: they are worth something when the drawer is open
        # because something looks wrong, and nothing the rest of the time.
        self.lbl_board = tk.Label(setup, text="no board on screen", bg=BG,
                                  fg=MUTED, font=("Segoe UI", 8), anchor="w")
        self.lbl_board.pack(fill="x", pady=(5, 0))

        # The move to play. It is the reason the program exists, so it is the
        # one thing here allowed to be large.
        hero = tk.Frame(self.root, bg=BG)
        pack["hero"] = (hero, dict(fill="x", padx=12, pady=(4, 0)))
        self.btn_clear = tk.Button(hero, text="clear", command=self._clear_arrows,
                                   relief="flat", bg="#3d3a37", fg=MUTED,
                                   cursor="hand2", font=("Segoe UI", 8), padx=4)
        pack["clear"] = (self.btn_clear, dict(side="right", padx=(6, 0)))
        self.lbl_coach = tk.Label(hero, text="", bg=BG, fg=ACCENT, anchor="w",
                                  justify="left", font=("Segoe UI", 14, "bold"))
        pack["advice"] = (self.lbl_coach, dict(side="left", fill="x",
                                               expand=True))
        self.lbl_coach.bind("<Configure>", self._wrap)

        self.lbl_detail = tk.Label(self.root, text="", bg=BG, fg=MUTED,
                                   font=("Segoe UI", 9), anchor="w")
        pack["detail"] = (self.lbl_detail, dict(fill="x", padx=12))

        self.lbl_check = tk.Label(self.root, text="", bg=BG, fg=MUTED,
                                  font=("Segoe UI", 9), anchor="w")
        pack["note"] = (self.lbl_check, dict(fill="x", padx=12, pady=(4, 0)))

        # The side control again, under the note that asks for it. Same
        # variable and same command as the drawer's row, and lined up with it,
        # so it is the same setting rather than a second one. No "auto": the
        # prompt is only up while that is what is selected. showing() takes
        # the row down as soon as either of those stops being true.
        prompt = self._drawer_row(self.root, "Side")
        pack["side"] = (prompt, dict(fill="x", padx=12, pady=(2, 0)))
        for word in ("white", "black"):
            tk.Radiobutton(prompt, text=word, value=word,
                           variable=self.colour_choice, command=self._set_colour,
                           bg=BG, fg=MUTED, selectcolor=BG, activebackground=BG,
                           activeforeground=FG, font=("Segoe UI", 8),
                           cursor="hand2").pack(side="left")

        self.lbl_result = tk.Label(self.root, text="", bg=BG, fg=ACCENT,
                                   font=("Segoe UI", 11, "bold"), anchor="w")
        pack["result"] = (self.lbl_result, dict(fill="x", padx=12, pady=(4, 0)))

        self.moves_box = tk.Text(self.root, bg=PANEL, fg=FG, relief="flat",
                                 font=("Consolas", 12), state="disabled",
                                 padx=12, pady=10, height=14, wrap="word")
        pack["moves"] = (self.moves_box, dict(fill="both", expand=True,
                                              padx=12, pady=6))
        self.moves_box.tag_configure("num", foreground=MUTED)
        self.moves_box.tag_configure("mine", foreground=ACCENT,
                                     font=("Consolas", 12, "bold"))
        self.moves_box.tag_configure("theirs", foreground=FG)
        self.moves_box.tag_configure("hint", foreground=MUTED,
                                     font=("Consolas", 10))
        self.moves_box.tag_configure("warn", foreground=WARN,
                                     font=("Consolas", 10))

        self.board_box = tk.Text(self.root, bg="#1e1c1a", fg=MUTED, relief="flat",
                                 font=("Consolas", 10), height=8, padx=10, pady=6,
                                 state="disabled")
        pack["position"] = (self.board_box, dict(side="bottom", fill="x",
                                                 padx=12, pady=(0, 6)))

        foot = tk.Frame(self.root, bg=BG)
        pack["foot"] = (foot, dict(side="bottom", fill="x", padx=12,
                                   pady=(0, 10)))
        # The filename is the way into the folder, so there is no button for
        # one. Underlined, on a hand cursor, is what says it can be clicked.
        self.lbl_file = tk.Label(foot, text="games", bg=BG, fg=MUTED, anchor="w",
                                 font=("Segoe UI", 8, "underline"),
                                 cursor="hand2")
        self.lbl_file.pack(side="left", fill="x", expand=True)
        self.lbl_file.bind("<Button-1>", lambda e: self._open_folder())

        self._pack = pack
        self._relayout()

    def _drawer_row(self, parent, name):
        """A word, then the controls it covers. Four of these make the drawer
        and one is the side prompt, which is why it lines up with the drawer's
        own Side row. The width is in characters, so the words still line up
        when the display scaling grows the font and the window does not
        follow."""
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=1)
        tk.Label(row, text=name, bg=BG, fg=MUTED, width=7, anchor="w",
                 font=("Segoe UI", 8)).pack(side="left")
        return row

    def _switch(self, parent, text, variable, command):
        tk.Checkbutton(parent, text=text, variable=variable, command=command,
                       bg=BG, fg=MUTED, selectcolor=BG, activebackground=BG,
                       activeforeground=FG, font=("Segoe UI", 8),
                       cursor="hand2").pack(side="left")

    # -- layout ------------------------------------------------------
    def _state(self):
        """What the window has to say, read back off the labels holding it."""
        return {"setup": self.setup_open,
                "advice": self.lbl_coach.cget("text"),
                "detail": self.lbl_detail.cget("text"),
                "note": self.lbl_check.cget("text"),
                "result": self.lbl_result.cget("text"),
                "arrow": self.arrow_drawn,
                "side": self.colour_choice.get(),
                "position": self.show_board.get()}

    def _relayout(self):
        """Put up the stripes showing() asks for, and only those.

        Everything is unpacked and packed again rather than patched, so what
        is on screen is in STRIPES order however it got there. That is
        affordable because it only runs when the set has really changed,
        which is a handful of times a game.
        """
        want = showing(self._state())
        if want == self._shown:
            return
        self._shown = want
        for name in STRIPES:
            self._pack[name][0].pack_forget()
        for name in want:
            widget, how = self._pack[name]
            widget.pack(**how)

    def _wrap(self, event):
        """Wrap the move line at the width it has been given, so a long
        message (Stockfish failing to start) folds rather than running off the
        edge. Only on a real change: setting it fires <Configure> again."""
        if event.width != int(self.lbl_coach.cget("wraplength")):
            self.lbl_coach.configure(wraplength=event.width)

    def _toggle_setup(self):
        self.setup_open = not self.setup_open
        self._relayout()

    def _toggle_position(self):
        self._save_config()
        self._relayout()

    # -- actions -----------------------------------------------------
    def _apply_saved_switches(self):
        """Idempotent: whichever of the two is already running is left alone."""
        if self.coach_on.get() and self.coach is None:
            self._toggle_coach(asked=False)
        if self.arrow_on.get() and self.arrow is None:
            self._toggle_arrow()

    def _check_now(self):
        if self.worker:
            self.worker.check_now.set()
            self.note_until = time.time() + NOTE_SECONDS
            self.lbl_check.configure(text="rechecking every square", fg=MUTED)

    def _set_colour(self):
        self._save_config()
        if self.worker:
            self.worker.tracker.set_colour(self.colour_choice.get())

    def _toggle(self):
        self._stop() if self.worker else self._start()

    def _start(self):
        # A region saved by an older build was written down exactly as it was
        # dragged, and a stale one that is a few pixels off goes on reading
        # every square off centre for as long as it stays saved: the worker
        # only ever re-hunts a manual region once it stops holding a board at
        # all, which one four pixels short of the board still does. So it is
        # snapped on the way in and the corrected one written back.
        snapped = snap_region(self.board_region)
        if snapped != self.board_region:
            self.board_region = snapped
            self._save_config()
        self.worker = Worker(self.board_region, self.q)
        self.worker.tracker.set_colour(self.colour_choice.get())
        # Every worker builds a fresh reader on the bundled sheet, so pieces
        # taught by hand have to be handed back on every start or they would
        # last only until the next restart, which is most of the point of
        # having taught them.
        if self.taught_sheet:
            self._use_taught(self.taught_sheet)
        self.worker.start()
        self.btn.configure(text="Stop", bg="#b33a3a",
                           activebackground="#8f2f2f")

    def _stop(self):
        if not self.worker:
            return
        self.worker.stop_flag.set()
        game = self.worker.tracker.game
        if game and game.moves:
            game.save()
            # Still the same path, and still the way into the folder, so it is
            # written the same way it is written while a game is running.
            self.lbl_file.configure(
                text="games\\" + os.path.basename(game.path))
        self.worker = None
        self.btn.configure(text="Start", bg=ACCENT,
                           activebackground="#6d9245")
        self.lbl_status.configure(text="stopped", fg=MUTED)
        # No frames are coming any more, so nothing else would ever take it
        # down and it would sit on the board pointing at a dead position.
        self._hide_arrow()

    def _pick(self):
        picked = RegionPicker(self.root, "Drag a box around the BOARD, corner to corner").result
        if not picked:
            return
        # Snapped here and not inside the picker, which is still showing its
        # own dimmed window over the desktop while it runs: a screenshot taken
        # then is a picture of the dim.
        self.board_region = snap_region(picked)
        self._save_config()
        if self.worker:
            self._stop()
        self._start()

    def _toggle_coach(self, asked=True):
        """Stockfish is started the first time coaching is on, which since it
        is on by default is the first launch. asked is False for that one, and
        it is the difference between a warning and a remark.
        """
        self._save_config()
        if not self.coach_on.get():
            self.lbl_coach.configure(text="")
            self.lbl_detail.configure(text="")
            self.coach_fen = None
            self._hide_arrow()
            return
        if self.coach is None:
            path = CO.find_engine()
            if path is None:
                # On the note line rather than in the move slot. It is a
                # sentence, the move slot is 14pt bold, and nothing would ever
                # clear it again: the switch has just turned itself back off,
                # so no later frame comes past to take it down.
                #
                # Muted unless it was clicked for. Nobody asked for coaching on
                # a first launch, so this is the answer to why there is no move
                # line rather than a report of something going wrong. The
                # switch stays on in config.json either way, so installing
                # Stockfish later is all it takes.
                self.coach_on.set(False)
                self.note_until = time.time() + NOTE_SECONDS
                self.lbl_check.configure(
                    text="no Stockfish, so no coaching. See the README.",
                    fg=WARN if asked else MUTED)
                return
            self.coach = CO.Coach(path, think_seconds=self._think_seconds())
            self.coach.start()
        # The receipt for the tick, so it belongs to the tick. This runs at
        # launch now, where nothing has been asked and no board has been found
        # yet, and a 14pt line saying it is thinking would be the first thing a
        # new user saw and would not be true. The first answer fills the line
        # in either case.
        if asked:
            self.lbl_coach.configure(text="thinking...", fg=MUTED)

    def _think_seconds(self):
        return float(self.think_choice.get()[:-1])

    def _set_think(self):
        """Write the new think time down and hand it to the engine thread.

        Set on the running Coach rather than restarting it, because the next
        search reads the attribute and the one in progress belongs to a
        position you are about to move past anyway."""
        self._save_config()
        if self.coach is not None:
            self.coach.think_seconds = self._think_seconds()

    def _toggle_arrow(self):
        """The arrow needs the coach, since it draws what the coach found."""
        self._save_config()
        if not self.arrow_on.get():
            self._sync_arrow()
            return
        if not self.coach_on.get():
            self.coach_on.set(True)
            self._toggle_coach()
        if self.arrow is None and self.coach_on.get():
            self.arrow = OV.Arrow(self.root)
        self._sync_arrow()

    def _show_arrow(self, uci, fen, mine=True):
        """Remember what the engine said, which position it said it about and
        whose move it was. Whether that is still worth drawing is _sync_arrow's
        decision, and mine is which of the two colours it gets.

        fen has no default on purpose. None is also the "nothing cleared"
        sentinel, so a call that forgot to pass one would suppress the arrow
        for the rest of the session rather than fail.
        """
        self.arrow_uci = uci
        self.arrow_fen = fen
        self.arrow_mine = mine
        self._sync_arrow()

    def _hide_arrow(self):
        """Forget the advice as well as taking the arrow down, so a later frame
        cannot decide it is still current."""
        self.arrow_uci = self.arrow_fen = None
        self._sync_arrow()

    def _sync_arrow(self):
        """Put the arrow where the position on screen says it belongs.

        Runs on every frame, so an arrow the board has moved past comes down
        here rather than waiting for a reply that may never arrive. The whole
        decision is OV.wanted(), which holds no window and can be checked on
        its own. A region that has moved is not stale advice, only stale
        pixels, so that case comes back as the same move at the new rectangle
        and the arrow follows. show() compares before it redraws, so calling it
        on every frame costs nothing while nothing is changing.
        """
        if self.arrow is None:
            self.arrow_drawn = False
            return
        want = OV.wanted(self.arrow_on.get(), self.region, self.coach_fen,
                         self.arrow_fen, self.arrow_uci, self.arrow_cleared,
                         self.flipped, self.arrow_mine)
        # The clear button is only on screen while there is something drawn
        # for it to take away, which is this.
        self.arrow_drawn = want is not None
        if want is None:
            self.arrow.hide()
            return
        region, uci, flipped, mine = want
        self.arrow.show(region, chess.Move.from_uci(uci), flipped, mine)

    def _clear_arrows(self):
        """Take down whatever is drawn on the board now, and keep the reply the
        engine is already working on from putting it straight back.

        Suppression is by position rather than a mode you have to switch off
        again: the engine answers a fen, so remembering the fen that was
        cleared beats the reply already in flight for it, and the next move
        brings arrows back on its own.
        """
        self.arrow_cleared = self.coach_fen
        self._hide_arrow()

    def _teach_pieces(self):
        """Label the pieces on the board by hand. The only route into a game
        joined part way through on a set nothing has ever seen, since learning
        needs all twelve types on the board and such a game never has them."""
        import enroll        # deferred: enroll imports this module for the grab
        if self.worker is None or self.region is None:
            self.note_until = time.time() + NOTE_SECONDS
            self.lbl_check.configure(text="no board on screen to teach from",
                                     fg=WARN)
            return
        if self.teacher is not None and self.teacher.win.winfo_exists():
            self.teacher.win.lift()      # one at a time, like the arrow
            return
        self.teacher = enroll.Enroller(self.root, grab(self.region),
                                       self.worker.reader,
                                       on_saved=self._use_taught)

    def _use_taught(self, path):
        """Read with the sheet that was just taught, and keep reading with it
        after a restart.

        use_bundled() is the one public way in: it loads self.sheet whatever
        that points at, all or nothing, and resets the learned size stale()
        judges. It replaces the whole set rather than merging, which is the
        right way round here. Teaching is only ever reached by clicking the
        button, it is offered for a board nothing can read, and the first
        starting position of the next game relearns from the screen and takes
        the better set straight back.
        """
        reader = self.worker.reader if self.worker else None
        if reader is None:
            return
        was = reader.sheet
        reader.sheet = path
        if reader.use_bundled():
            reader.source = "taught by hand"
            self.taught_sheet = path
            self._save_config()
            self.note_until = time.time() + NOTE_SECONDS
            self.lbl_check.configure(text="reading with the pieces you taught",
                                     fg=ACCENT)
            return
        # relearn() falls back on this path when a learned set goes stale, so
        # leaving it pointed at a sheet that will not load would break the
        # fallback as well as this.
        reader.sheet = was
        if self.taught_sheet == path:
            # It loaded once and has since been deleted or damaged. Forget it
            # rather than complaining about it at every launch from now on.
            self.taught_sheet = None
            self._save_config()
        self.note_until = time.time() + NOTE_SECONDS
        self.lbl_check.configure(text="that sheet would not load", fg=WARN)

    def _open_folder(self):
        os.makedirs(W.GAMES_DIR, exist_ok=True)
        os.startfile(W.GAMES_DIR)

    # -- ui updates --------------------------------------------------
    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "frame":
                    self._render(payload)
                elif kind == "searching":
                    self.lbl_status.configure(text="no board", fg=MUTED)
                    self.lbl_board.configure(text="no board on screen")
                    # There are no board pixels left for an arrow to be right
                    # about. Forgetting the region is what takes it down.
                    self.region = None
                    self._sync_arrow()
                elif kind == "error":
                    # A word on the top row, the sentence on the note line,
                    # which has the width for it.
                    self.lbl_status.configure(text="error", fg=WARN)
                    self.note_until = time.time() + NOTE_SECONDS
                    self.lbl_check.configure(text=payload[:70], fg=WARN)
        except queue.Empty:
            pass
        self._drain_coach()
        # Notes come down here rather than where they went up, so one put
        # there by a click still goes away when no frames are arriving.
        if self.lbl_check.cget("text") and time.time() > self.note_until:
            self.lbl_check.configure(text="")
        self._relayout()
        self.root.after(120, self._drain)

    def _drain_coach(self):
        """Advice for a position that has already been played past is thrown
        away rather than shown against the wrong board."""
        if self.coach is None:
            return
        try:
            while True:
                kind, payload = self.coach.out.get_nowait()
                if kind == "engine":
                    if payload != "ready":
                        self.note_until = time.time() + NOTE_SECONDS
                        self.lbl_check.configure(text=payload[:70], fg=WARN)
                        self.lbl_coach.configure(text="")
                        self.lbl_detail.configure(text="")
                        self.coach_on.set(False)
                elif kind == "advice" and self.coach_on.get():
                    if payload["fen"] != self.coach_fen:
                        continue
                    if payload.get("over"):
                        self.lbl_coach.configure(text="the game is over", fg=MUTED)
                        self.lbl_detail.configure(text="")
                        self._hide_arrow()
                        continue
                    # Their best move is what they are threatening, so it is
                    # drawn too, in the other colour. Until the orientation
                    # settles my_colour is None and nothing is yours yet, which
                    # puts the arrow in their colour and the label agrees.
                    mine = payload["turn"] == self.my_colour
                    whose = "your move" if mine else "their move"
                    self._show_arrow(payload["uci"], payload["fen"], mine)
                    # Whose move it is and what to play, large, on their own.
                    # The naming of the squares, the score, and the mark that
                    # says the engine has not finished are the small print
                    # under it. The mark goes there rather than on the move
                    # because the move line is sized to hold the longest SAN
                    # there is at 150% scaling in a 400px window, with 3px to
                    # spare, and five more characters do not fit; and because
                    # what an unfinished answer is hedging is the score, which
                    # is already on that line.
                    self.lbl_coach.configure(
                        text="%s  %s" % (whose, payload["san"]),
                        fg=ACCENT if mine else MUTED)
                    self.lbl_detail.configure(
                        text="   ".join(part for part in
                                        (payload["text"], payload["score"],
                                         "" if payload.get("final") else "...")
                                        if part))
        except queue.Empty:
            pass

    def _render(self, f):
        self.my_colour = f["color"]
        self.flipped = f.get("flipped", False)
        self.region = f["region"]
        region = f["region"]

        # One word. At 400px and 150% display scaling this row has about
        # fifteen characters to spare beside the two buttons, and how many
        # moves have been recorded is the move list, right underneath.
        if f["locked"]:
            self.lbl_status.configure(text="recording", fg=ACCENT)
        else:
            self.lbl_status.configure(text="waiting", fg=MUTED)

        if f["result"] != "*":
            word = {"won": "You won", "lost": "You lost",
                    "draw": "Draw"}.get(f["outcome"], f["result"])
            self.lbl_result.configure(text="%s by %s" % (word, f["termination"]))
        else:
            self.lbl_result.configure(text="")

        box = self.moves_box
        box.configure(state="normal")
        box.delete("1.0", "end")
        if not f["locked"]:
            # One line, and no line breaks in it. This is the first thing seen
            # on launch and the largest text on screen while it is up, and it
            # was three lines of prose hand-wrapped to 41 characters, which
            # soft-wrapped again at 150% scaling. The box wraps by word.
            box.insert("end", "Open a game. Recording starts from the opening "
                              "position.", "hint")
        elif not f["rows"]:
            box.insert("end", "playing as %s, no moves yet" % f["color"], "hint")
        else:
            mine_is_white = f["color"] == "white"
            if f.get("joined"):
                # Numbers cannot line up with chess.com here: nothing in the
                # picture says how many moves were played before we looked.
                box.insert("end",
                           "picked this game up part way through, so these are"
                           " counted from where watching started, not from"
                           " chess.com's numbers\n\n", "warn")
            for num, white, black in f["rows"]:
                label = ("+%d." % num) if f.get("joined") else ("%d." % num)
                box.insert("end", "%5s " % label, "num")
                # A game joined part way can begin on black's move.
                box.insert("end", "%-9s" % (white or "..."),
                           "mine" if mine_is_white else "theirs")
                if black:
                    box.insert("end", black, "theirs" if mine_is_white else "mine")
                box.insert("end", "\n")
        box.configure(state="disabled")
        box.see("end")

        if self.show_board.get():
            self.board_box.configure(state="normal")
            self.board_box.delete("1.0", "end")
            self.board_box.insert("1.0", f["board"] or "(no position yet)")
            self.board_box.configure(state="disabled")

        # Where the board was found and what is reading it, on one line in the
        # drawer. Both are diagnostics: they are worth reading when you have
        # opened the drawer because something looks wrong, and never otherwise.
        where = "no board on screen"
        if region:
            where = "board %dx%d at %d,%d" % (region[2], region[3],
                                              region[0], region[1])
        kind = f.get("templates") or ""
        if kind == "bundled" and f.get("sheet") not in (None,
                                                        pieces.TEMPLATE_SHEET):
            # use_bundled() calls whatever self.sheet points at "bundled", and
            # relearn() calls use_bundled(), so the reader's own word for a
            # taught sheet does not survive the first relearn. Which file is
            # loaded does.
            kind = "taught by hand"
        self.lbl_board.configure(
            text=where + ("   pieces %s" % kind if kind else ""))

        # The note line carries two things: a complaint from the piece
        # checker, which is worth the whole time it is true, and a receipt for
        # something you clicked, which is worth a few seconds. The complaint
        # wins. "position confirmed" is the checker finding nothing to say,
        # and it says that almost every time, so it never reaches the line at
        # all: as a permanent line it said nothing and still cost the height.
        note = f.get("check") or ""
        if note and note != "position confirmed":
            if self.lbl_check.cget("text") != note:
                self.lbl_check.configure(text=note, fg=MUTED)
            self.note_until = time.time() + NOTE_SECONDS

        if self.coach is not None and self.coach_on.get():
            if f.get("fen") and f["result"] == "*":
                self.coach.ask(f["fen"])
                self.coach_fen = f["fen"]
            else:
                self.lbl_coach.configure(text="")
                self.lbl_detail.configure(text="")
                self.coach_fen = None
                self._hide_arrow()

        if f["path"]:
            self.lbl_file.configure(text="games\\" + os.path.basename(f["path"]))

        # A cleared arrow is cleared for one position, not for the rest of the
        # session. Two games in a sitting reach byte-identical opening FENs,
        # clocks and move numbers included, so a suppression left lying about
        # would swallow the arrow in the next game with nothing clicked in it.
        if f.get("event") == "newgame" or self.arrow_cleared != self.coach_fen:
            self.arrow_cleared = None

        # Last, because it reads the region, the flip and the position this
        # frame just set. Any of the three changing is what makes an arrow
        # drawn for the frame before it wrong.
        self._sync_arrow()

    # -- config ------------------------------------------------------
    def _load_config(self):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def _save_config(self):
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"board_region": self.board_region,
                       "colour": self.colour_choice.get(),
                       "coach": bool(self.coach_on.get()),
                       "think_seconds": self._think_seconds(),
                       "arrow": bool(self.arrow_on.get()),
                       "position": bool(self.show_board.get()),
                       "sheet": self.taught_sheet}, fh, indent=2)
        os.replace(tmp, CONFIG_PATH)

    def _quit(self):
        self._stop()
        if self.coach is not None:
            self.coach.stop()
        if self.arrow is not None:
            self.arrow.destroy()
        self.root.destroy()


_instance_lock = None


def claim_single_instance():
    """True if we are the only ChessWatch. Two copies watching the same screen
    write two different files for the same game, so only one may run."""
    global _instance_lock
    if sys.platform != "win32":
        return True
    ERROR_ALREADY_EXISTS = 183
    _instance_lock = ctypes.windll.kernel32.CreateMutexW(
        None, False, "ChessWatch.SingleInstance")
    return ctypes.windll.kernel32.GetLastError() != ERROR_ALREADY_EXISTS


def main():
    root = tk.Tk()
    if not claim_single_instance():
        root.withdraw()
        messagebox.showinfo(
            "ChessWatch is already running",
            "ChessWatch is already open and watching.\n\n"
            "Look for its window, or close it before starting a new one.")
        return
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
