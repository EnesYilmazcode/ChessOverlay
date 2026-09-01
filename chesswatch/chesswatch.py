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

# How often the slower piece-by-piece check runs, in frames. While the fast
# reader is stuck it runs far sooner, because that is exactly when a gap is
# still small enough to be bridged.
CHECK_EVERY = 35
STUCK_CHECK = 6

# And sooner still when the last-move highlight says the wrong side just moved.
# Two frames rather than none so a check that cannot settle the argument is not
# then run on every frame after it.
DISPUTE_CHECK = 2

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

BG = "#262421"
PANEL = "#302e2b"
FG = "#e8e6e3"
ACCENT = "#7fa650"
MUTED = "#8b8987"
WARN = "#d08a70"

_MSS = getattr(mss, "MSS", None) or mss.mss


# ------------------------------------------------------------------ capture

def grab(region):
    """Screenshot one region. region is (left, top, width, height)."""
    left, top, width, height = region
    with _MSS() as sct:
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
        return Image.frombytes("RGB", (shot.width, shot.height), shot.bgra,
                               "raw", "BGRX")


def virtual_screen():
    with _MSS() as sct:
        m = sct.monitors[0]
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
        self.check_now = threading.Event()
        self._quiet = 0
        self._frames = 0
        self._since_check = 0
        self._accepted = None      # the last reading we acted on
        self._board_px = None      # board width the templates were last fitted to
        self.settle_stats = [0, 0]  # readings taken, readings acted on

    def run(self):
        while not self.stop_flag.is_set():
            started = time.time()
            try:
                self._tick()
            except Exception as exc:
                self.out.put(("error", "%s: %s" % (type(exc).__name__, exc)))
            time.sleep(max(0.05, POLL_SECONDS - (time.time() - started)))

    def _tick(self):
        self._frames += 1
        if self.region is None or self._should_refind():
            found = find_board_on_screen()
            if found:
                self.region = found
                self._quiet = 0
            elif self.region is None:
                self.out.put(("searching", None))
                return

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

        if self._run_check(shot, occ, event):
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
        }))

    def _run_check(self, shot, occ, event):
        """The piece-level pass. Runs on a timer, whenever the fast reader has
        been stuck for a while, whenever the highlight says we have the wrong
        side to move, and whenever you press the button.

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
               or (self.tracker.stuck and self._since_check >= STUCK_CHECK)
               or (self._since_check >= DISPUTE_CHECK
                   and self.tracker.turn_disputed(occ, shot)))
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
        """
        if not self.tracker.locked_on and not self.manual:
            return self._frames % REFIND_IDLE == 0
        if not self._quiet or self._quiet % REFIND_CHECK:
            return False
        try:
            here = grab(self.region)
        except Exception:
            return True
        return W.grid_score(here, 0, 0, self.region[2]) < STILL_A_BOARD


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
        self.region = None
        self.flipped = False

        root.title("ChessWatch")
        root.configure(bg=BG)
        root.geometry("430x690")
        root.minsize(400, 560)

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
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=12, pady=(12, 8))
        self.btn = tk.Button(head, text="Stop watching", command=self._toggle,
                             bg="#b33a3a", fg="white", relief="flat",
                             font=("Segoe UI", 11, "bold"), padx=14, pady=7,
                             activebackground="#8f2f2f", cursor="hand2")
        self.btn.pack(side="left")
        self.lbl_status = tk.Label(head, text="starting", bg=BG, fg=MUTED,
                                   font=("Segoe UI", 9), justify="left")
        self.lbl_status.pack(side="left", padx=10)

        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=12, pady=(0, 8))
        self.lbl_board = tk.Label(bar, text="looking for the board", bg=BG,
                                  fg=MUTED, font=("Segoe UI", 8), anchor="w")
        self.lbl_board.pack(side="left", fill="x", expand=True)
        tk.Button(bar, text="pick board manually", command=self._pick,
                  relief="flat", bg="#3d3a37", fg=FG, cursor="hand2",
                  font=("Segoe UI", 8)).pack(side="right")

        tools = tk.Frame(self.root, bg=BG)
        tools.pack(fill="x", padx=12, pady=(0, 6))
        tk.Button(tools, text="check the pieces now", command=self._check_now,
                  relief="flat", bg="#3d3a37", fg=FG, cursor="hand2",
                  font=("Segoe UI", 8)).pack(side="left")
        tk.Label(tools, text="I play:", bg=BG, fg=MUTED,
                 font=("Segoe UI", 8)).pack(side="left", padx=(10, 2))
        for text, value in (("auto", "auto"), ("white", "white"),
                            ("black", "black")):
            tk.Radiobutton(tools, text=text, value=value,
                           variable=self.colour_choice, command=self._set_colour,
                           bg=BG, fg=MUTED, selectcolor=BG, activebackground=BG,
                           activeforeground=FG, font=("Segoe UI", 8),
                           cursor="hand2").pack(side="left")

        # Their own row: the tools row above is already full at this width, and
        # a Checkbutton that does not fit is silently not drawn at all.
        switches = tk.Frame(self.root, bg=BG)
        switches.pack(fill="x", padx=12, pady=(0, 4))
        tk.Label(switches, text="coaching:", bg=BG, fg=MUTED,
                 font=("Segoe UI", 8)).pack(side="left", padx=(0, 4))
        self.coach_on = tk.BooleanVar(value=bool(self.cfg.get("coach", False)))
        tk.Checkbutton(switches, text="best move", variable=self.coach_on,
                       command=self._toggle_coach, bg=BG, fg=MUTED, selectcolor=BG,
                       activebackground=BG, activeforeground=FG,
                       font=("Segoe UI", 8), cursor="hand2").pack(side="left")
        self.arrow_on = tk.BooleanVar(value=bool(self.cfg.get("arrow", False)))
        tk.Checkbutton(switches, text="arrow on board", variable=self.arrow_on,
                       command=self._toggle_arrow, bg=BG, fg=MUTED, selectcolor=BG,
                       activebackground=BG, activeforeground=FG,
                       font=("Segoe UI", 8), cursor="hand2").pack(side="left")

        self.lbl_coach = tk.Label(self.root, text="", bg=BG, fg=ACCENT,
                                  font=("Segoe UI", 10, "bold"), anchor="w")
        self.lbl_coach.pack(fill="x", padx=12, pady=(2, 0))

        self.lbl_check = tk.Label(self.root, text="", bg=BG, fg=MUTED,
                                  font=("Segoe UI", 8), anchor="w")
        self.lbl_check.pack(fill="x", padx=12)

        self.lbl_result = tk.Label(self.root, text="", bg=BG, fg=ACCENT,
                                   font=("Segoe UI", 11, "bold"))
        self.lbl_result.pack(fill="x", padx=12)

        self.moves_box = tk.Text(self.root, bg=PANEL, fg=FG, relief="flat",
                                 font=("Consolas", 12), state="disabled",
                                 padx=12, pady=10, height=13)
        self.moves_box.pack(fill="both", expand=True, padx=12, pady=(4, 6))
        self.moves_box.tag_configure("num", foreground=MUTED)
        self.moves_box.tag_configure("mine", foreground=ACCENT,
                                     font=("Consolas", 12, "bold"))
        self.moves_box.tag_configure("theirs", foreground=FG)
        self.moves_box.tag_configure("hint", foreground=MUTED,
                                     font=("Consolas", 10))
        self.moves_box.tag_configure("warn", foreground=WARN,
                                     font=("Consolas", 10))

        self.show_board = tk.BooleanVar(value=True)
        self.board_box = tk.Text(self.root, bg="#1e1c1a", fg=MUTED, relief="flat",
                                 font=("Consolas", 10), height=8, padx=10, pady=6,
                                 state="disabled")
        self.board_box.pack(fill="x", padx=12, pady=(0, 6))

        foot = self.foot = tk.Frame(self.root, bg=BG)
        foot.pack(fill="x", padx=12, pady=(0, 10))
        self.lbl_file = tk.Label(foot, text="", bg=BG, fg=MUTED,
                                 font=("Segoe UI", 8), anchor="w")
        self.lbl_file.pack(side="left", fill="x", expand=True)
        tk.Checkbutton(foot, text="show board", variable=self.show_board,
                       command=self._toggle_board, bg=BG, fg=MUTED, selectcolor=BG,
                       activebackground=BG, activeforeground=FG,
                       font=("Segoe UI", 8), cursor="hand2").pack(side="right", padx=6)
        tk.Button(foot, text="open games folder", command=self._open_folder,
                  relief="flat", bg="#3d3a37", fg=FG, cursor="hand2",
                  font=("Segoe UI", 8)).pack(side="right")

    # -- actions -----------------------------------------------------
    def _apply_saved_switches(self):
        """Idempotent: whichever of the two is already running is left alone."""
        if self.coach_on.get() and self.coach is None:
            self._toggle_coach()
        if self.arrow_on.get() and self.arrow is None:
            self._toggle_arrow()

    def _check_now(self):
        if self.worker:
            self.worker.check_now.set()
            self.lbl_check.configure(text="relearning the pieces, then checking "
                                          "every square...", fg=MUTED)

    def _set_colour(self):
        self._save_config()
        if self.worker:
            self.worker.tracker.set_colour(self.colour_choice.get())

    def _toggle(self):
        self._stop() if self.worker else self._start()

    def _start(self):
        self.worker = Worker(self.board_region, self.q)
        self.worker.tracker.set_colour(self.colour_choice.get())
        self.worker.start()
        self.btn.configure(text="Stop watching", bg="#b33a3a",
                           activebackground="#8f2f2f")

    def _stop(self):
        if not self.worker:
            return
        self.worker.stop_flag.set()
        game = self.worker.tracker.game
        if game and game.moves:
            game.save()
            self.lbl_file.configure(text="saved " + os.path.basename(game.path))
        self.worker = None
        self.btn.configure(text="Start watching", bg=ACCENT,
                           activebackground="#6d9245")
        self.lbl_status.configure(text="stopped", fg=MUTED)

    def _pick(self):
        picked = RegionPicker(self.root, "Drag a box around the BOARD, corner to corner").result
        if not picked:
            return
        self.board_region = picked
        self._save_config()
        if self.worker:
            self._stop()
        self._start()

    def _toggle_coach(self):
        """Stockfish is only started when you actually ask for advice."""
        self._save_config()
        if not self.coach_on.get():
            self.lbl_coach.configure(text="")
            self.coach_fen = None
            if self.arrow:
                self.arrow.hide()
            return
        if self.coach is None:
            path = CO.find_engine()
            if path is None:
                self.coach_on.set(False)
                self.lbl_coach.configure(
                    text="no Stockfish found. See the README.", fg=WARN)
                return
            self.coach = CO.Coach(path)
            self.coach.start()
        self.lbl_coach.configure(text="thinking...", fg=MUTED)

    def _toggle_arrow(self):
        """The arrow needs the coach, since it draws what the coach found."""
        self._save_config()
        if not self.arrow_on.get():
            if self.arrow:
                self.arrow.hide()
            return
        if not self.coach_on.get():
            self.coach_on.set(True)
            self._toggle_coach()
        if self.arrow is None and self.coach_on.get():
            self.arrow = OV.Arrow(self.root)

    def _show_arrow(self, uci):
        """Draw the suggestion on the board itself, if it is wanted and we know
        where the board is."""
        if not (self.arrow_on.get() and self.arrow and self.region and uci):
            if self.arrow:
                self.arrow.hide()
            return
        self.arrow.show(self.region, chess.Move.from_uci(uci), self.flipped)

    def _toggle_board(self):
        if self.show_board.get():
            self.board_box.pack(fill="x", padx=12, pady=(0, 6), before=self.foot)
        else:
            self.board_box.pack_forget()

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
                    self.lbl_status.configure(text="looking for a chess board",
                                              fg=MUTED)
                    self.lbl_board.configure(text="no board on screen yet")
                elif kind == "error":
                    self.lbl_status.configure(text=payload[:70], fg=WARN)
        except queue.Empty:
            pass
        self._drain_coach()
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
                        self.lbl_coach.configure(text=payload[:70], fg=WARN)
                        self.coach_on.set(False)
                elif kind == "advice" and self.coach_on.get():
                    if payload["fen"] != self.coach_fen:
                        continue
                    if payload.get("over"):
                        self.lbl_coach.configure(text="the game is over", fg=MUTED)
                        self._show_arrow(None)
                        continue
                    whose = ("your move" if payload["turn"] == self.my_colour
                             else "their move")
                    # Only your own move is worth drawing on the board. Their
                    # move is still shown in words.
                    self._show_arrow(payload["uci"] if whose == "your move"
                                     or self.my_colour is None else None)
                    self.lbl_coach.configure(
                        text="%s  %s  (%s)  %s" % (whose, payload["san"],
                                                   payload["text"],
                                                   payload["score"]),
                        fg=ACCENT if whose == "your move" else MUTED)
        except queue.Empty:
            pass

    def _render(self, f):
        self.my_colour = f["color"]
        self.flipped = f.get("flipped", False)
        self.region = f["region"]
        region = f["region"]
        if region:
            self.lbl_board.configure(
                text="board %dx%d at %d,%d" % (region[2], region[3],
                                               region[0], region[1]))

        if not f["locked"]:
            self.lbl_status.configure(
                text="waiting for a game to start", fg=MUTED)
        else:
            self.lbl_status.configure(
                text="recording  |  %d moves  |  %d saved" % (f["count"], f["saved"]),
                fg=ACCENT)

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
            box.insert("end", "waiting for a game to start\n\n", "hint")
            box.insert("end",
                       "Open a game on chess.com. Recording begins\n"
                       "from the opening position, so start a new\n"
                       "game rather than joining one midway.", "hint")
        elif not f["rows"]:
            box.insert("end", "game found, playing as %s\n\n" % f["color"], "hint")
            box.insert("end", "no moves yet", "hint")
        else:
            mine_is_white = f["color"] == "white"
            if f.get("joined"):
                # Numbers cannot line up with chess.com here: nothing in the
                # picture says how many moves were played before we looked.
                box.insert("end",
                           "picked this game up part way through, so\n"
                           "these are counted from where watching\n"
                           "started, not from chess.com's numbers\n\n", "warn")
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

        # Which templates are reading the board decides how much the rest of
        # this line is worth, so it shows on its own and not only when there
        # happens to be a check note to hang it off.
        note = f.get("check") or ""
        if f.get("templates"):
            note = (note + "   " if note else "") + "(pieces %s)" % f["templates"]
        self.lbl_check.configure(
            text=note, fg=ACCENT if "confirmed" in note else MUTED)

        if self.coach is not None and self.coach_on.get():
            if f.get("fen") and f["result"] == "*":
                self.coach.ask(f["fen"])
                self.coach_fen = f["fen"]
            else:
                self.lbl_coach.configure(text="")
                self.coach_fen = None
                self._show_arrow(None)

        if f["path"]:
            self.lbl_file.configure(text="games\\" + os.path.basename(f["path"]))

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
                       "arrow": bool(self.arrow_on.get())}, fh, indent=2)
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
