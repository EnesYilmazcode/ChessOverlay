"""Checks for the coaching half: the engine wrapper and the label it feeds.

Run:  python coachtest.py

The engine checks need a Stockfish binary. Without one they are skipped and the
rest still runs, because the wording and the stale-answer filter are the parts
most likely to break. How the engine process is started is checked against a
stub instead, so that part runs with or without a binary.
"""

import os
import queue
import shutil
import subprocess
import sys
import tempfile
import time

import chess

import coach as CO

R = []


def check(name, got, want):
    ok = got == want
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        print("        got  ", got)
        print("        want ", want)
    R.append(ok)
    return ok


def after(sans):
    b = chess.Board()
    for s in sans:
        b.push_san(s)
    return b


def read_text(path):
    """What a file holds, or None if it was never written."""
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return None


def wait(c, kind, seconds=15):
    """The next message of this kind off a coach, or a complaint, or None."""
    end = time.time() + seconds
    while time.time() < end:
        try:
            k, payload = c.out.get(timeout=0.2)
        except queue.Empty:
            continue
        if k == kind:
            return payload
        if k == "engine" and payload != "ready":
            return payload
    return None


def wording():
    print("\n-- what it says ------------------------------------------")
    b = after(["e4", "e5"])
    san, text, _ = CO.describe(b, chess.Move.from_uci("g1f3"), None)
    check("names the piece and both squares", (san, text),
          ("Nf3", "knight: g1 to f3"))

    b = after(["e4", "e5", "Nf3", "d5"])
    _, text, _ = CO.describe(b, chess.Move.from_uci("f3e5"), None)
    check("says what a capture takes", text,
          "knight: f3 to e5, taking the pawn")

    b = after(["e4", "e5", "Bc4", "Bc5", "Qh5", "Nf6"])
    _, text, _ = CO.describe(b, chess.Move.from_uci("h5f7"), None)
    check("says when a move gives check", text,
          "queen: h5 to f7, taking the pawn, with check")

    # En passant leaves the captured square empty, so the piece has to be named
    # from the move rather than from what is standing there.
    b = after(["e4", "a6", "e5", "d5"])
    _, text, _ = CO.describe(b, chess.Move.from_uci("e5d6"), None)
    check("gets en passant right", text, "pawn: e5 to d6, taking the pawn")

    b = after(["e4", "e5"])
    sc = chess.engine.PovScore(chess.engine.Cp(40), chess.WHITE)
    check("a score is from the mover's side, white", CO.read_score(sc, chess.WHITE), "+0.4")
    check("and flips for black", CO.read_score(sc, chess.BLACK), "-0.4")
    mate = chess.engine.PovScore(chess.engine.Mate(3), chess.WHITE)
    check("mate is counted in moves", CO.read_score(mate, chess.WHITE), "mate in 3")
    check("and says so when it is against you",
          CO.read_score(mate, chess.BLACK), "mated in 3")


# A stand-in for Stockfish. It answers the four UCI lines that need a reply with
# one move that is legal in the starting position, which is enough to prove the
# engine starts and answers and nothing at all about chess, and it reports the
# window handle of its own console so the flag can be judged on what it did.
STUB = """
import sys

handle = 0
if sys.platform == "win32":
    import ctypes
    handle = ctypes.windll.kernel32.GetConsoleWindow()
open(sys.argv[1], "w").write(str(handle))

for line in sys.stdin:
    cmd = line.strip()
    if cmd == "uci":
        print("id name stub")
        print("uciok")
    elif cmd == "isready":
        print("readyok")
    elif cmd.startswith("go"):
        print("info depth 1 score cp 12 pv e2e4")
        print("bestmove e2e4")
    elif cmd == "quit":
        break
"""


def spawn_point():
    """The class asyncio builds the engine process with.

    popen_uci passes anything extra to the event loop, which passes it here, so
    this is where a claim about how Stockfish is started can be read back.
    Windows uses its own Popen subclass rather than subprocess.Popen.
    """
    if sys.platform == "win32":
        import asyncio.windows_utils
        return asyncio.windows_utils, "Popen"
    return subprocess, "Popen"


def launch_checks():
    """What the engine is started with, what it gets, and that it still starts."""
    print("\n-- how the engine is started -----------------------------")
    mod, attr = spawn_point()
    real = getattr(mod, attr)
    scratch = tempfile.mkdtemp(prefix="chesswatch-stub-")
    report = os.path.join(scratch, "console")
    seen = {}

    def spy(args, **kwargs):
        # The path never reaches the process: the stub is substituted for it so
        # this runs without a binary. Everything else is passed on untouched,
        # so the stub is started exactly as Stockfish would have been.
        seen.update(kwargs)
        return real([sys.executable, "-u", "-c", STUB, report], **kwargs)

    c = CO.Coach("stockfish", movetime=0.05)
    try:
        setattr(mod, attr, spy)
        c.start()
        check("the engine starts", wait(c, "engine"), "ready")
        c.ask(chess.STARTING_FEN)
        got = wait(c, "advice")
        check("and answers a position", got and got["san"], "e4")
        if sys.platform == "win32":
            # The handle is the bug itself: 0 means the console the engine was
            # given has no window. The flag is read back as well, and by
            # equality, so asking for a new console alongside it would show up.
            check("and has no console window", read_text(report), "0")
            check("created with that flag and no other",
                  seen.get("creationflags"), subprocess.CREATE_NO_WINDOW)
        else:
            # CREATE_NO_WINDOW is a Windows name. Asking for it here would be an
            # AttributeError rather than a stray window.
            check("and asks for nothing Windows-only elsewhere",
                  "creationflags" in seen, False)
    finally:
        c.stop()
        c.join(timeout=5)
        setattr(mod, attr, real)
        shutil.rmtree(scratch, ignore_errors=True)


def engine_checks(path):
    print("\n-- against the real engine -------------------------------")
    c = CO.Coach(path, movetime=0.20)
    c.start()

    check("the engine starts", wait(c, "engine"), "ready")

    # Mate in one. There is no room for an opinion here.
    mate = "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1"
    got = wait(c, "advice")
    c.ask(mate)
    got = wait(c, "advice")
    check("finds the mate in one", got and got["san"], "Ra8#")
    check("and says so in the score", got and got["score"], "mate in 1")

    # Winning a free queen.
    c.ask("rnbqkbnr/ppp1pppp/8/3p4/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 2")
    got = wait(c, "advice")
    check("whose move it is", got and got["turn"], "white")

    over = "6k1/5ppp/8/8/8/8/5PPP/6K1 w - - 0 1"      # dead drawn, not over
    c.ask("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")           # checkmate on the board
    got = wait(c, "advice")
    check("a finished game gets no advice", got and got.get("over"), True)

    before = c.out.qsize()
    c.ask(mate)
    c.ask(mate)
    time.sleep(0.6)
    check("asking twice for one position answers once",
          c.out.qsize() - before <= 1, True)
    c.stop()


def label_checks(path):
    """The real Tk app, fed a frame, asked what it put on the label."""
    print("\n-- the label in the app ----------------------------------")
    import tempfile
    import tkinter as tk
    import chesswatch as C
    import testscreen as TS

    # The app starts watching the screen the moment it is built. Point it at a
    # scratch folder so a stray frame cannot land in your real games, and at a
    # scratch config so the test does not depend on which switches you left on.
    C.W.GAMES_DIR = tempfile.mkdtemp(prefix="chesswatch-test-")
    C.CONFIG_PATH = os.path.join(C.W.GAMES_DIR, "config.json")

    # App.__init__ ends by starting a worker, and a worker with no region hunts
    # the whole desktop, which means a test that calls itself headless would
    # screenshot everything you have open. Point the capture at a blank image
    # for as long as that worker is alive: it finds no board and gives up.
    screen = TS.PaperScreen(320, 200)

    root = tk.Tk()
    root.withdraw()
    app = C.App(root)
    watching = app.worker              # _stop() forgets it, and it is a thread
    app._stop()                        # do not watch the screen during a test
    if watching:
        watching.join(timeout=2)       # before the real capture is put back
    screen.close()
    frame = {"region": None, "locked": True, "rows": [], "count": 0,
             "color": "white", "result": "*", "termination": "", "outcome": None,
             "path": None, "saved": 0, "joined": False, "board": "",
             "check": "", "templates": "", "fen": None}

    app.coach_on.set(True)
    app._toggle_coach()
    check("switching it on starts the engine", app.coach is not None, True)

    mate = "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1"
    frame["fen"] = mate
    app._render(frame)
    end = time.time() + 15
    while time.time() + 0 < end and "thinking" in app.lbl_coach.cget("text"):
        app._drain_coach()
        root.update()
        time.sleep(0.05)
    check("your move is labelled as yours",
          app.lbl_coach.cget("text").startswith("your move  Ra8#"), True)

    # The same position with black to play is the opponent's move, and the
    # advice for the position just left behind must not be shown against it.
    app.lbl_coach.configure(text="stale")
    app.coach_fen = "something else entirely"
    app._drain_coach()
    check("advice for a position already played past is dropped",
          app.lbl_coach.cget("text"), "stale")

    app.coach_on.set(False)
    app._toggle_coach()
    check("switching it off clears the label", app.lbl_coach.cget("text"), "")

    app.coach.stop()
    root.destroy()


def capture_checks():
    """The screen grabber and the idle board hunt, neither of which needs a
    screen: the grabber is stood in for, and the hunt is driven off a clock we
    control."""
    import threading
    import chesswatch as C

    print("\n-- capture ----------------------------------------------")

    class Shot:
        width, height = 1, 1
        bgra = bytes(4)

    class Fake:
        """Stands in for mss, which wants a display this run has not got.
        Counts what it is asked for, because one per screenshot was the bug."""
        built = 0
        closed = 0

        def __init__(self):
            Fake.built += 1

        def grab(self, box):
            return Shot()

        def close(self):
            Fake.closed += 1

    class Flaky:
        """A grabber a display change has invalidated. The instance already
        built stops working, a newly built one is fine, which is what unplugging
        a monitor or changing the resolution does to the device context mss
        holds."""
        live = None

        def __init__(self):
            Flaky.live = self
            self.ok = True

        def _still_there(self):
            if not self.ok:
                raise OSError("the display changed under it")

        @property
        def monitors(self):
            self._still_there()
            return [{"left": 0, "top": 0, "width": 8, "height": 4}]

        def grab(self, box):
            self._still_there()
            return Shot()

        def close(self):
            pass

    def broke(fn):
        """What fn raised, or what it returned if it did not raise."""
        try:
            return fn()
        except Exception as exc:
            return "%s: %s" % (type(exc).__name__, exc)

    real = C._MSS
    C.close_sct()          # the stub only reaches grab() if the real one is gone
    C._MSS = Fake
    try:
        C.grab((0, 0, 1, 1))
        C.grab((0, 0, 1, 1))
        check("two screenshots, one grabber built", Fake.built, 1)
        mine = C._sct()
        theirs = {}
        t = threading.Thread(target=lambda: theirs.setdefault("sct", C._sct()))
        t.start()
        t.join()
        check("  and another thread gets its own", theirs["sct"] is mine, False)
        C.close_sct()
        check("  closing drops it", getattr(C._local, "sct", None), None)
        check("  and shuts it rather than leaking the device context",
              Fake.closed, 1)

        # Holding one costs what building one per shot gave away for free: a
        # grabber that has gone bad has to be dropped or the app never captures
        # again, and this is a display change away rather than a rare one.
        C._MSS = Flaky
        C.close_sct()
        C.grab((0, 0, 1, 1))
        Flaky.live.ok = False
        check("a screenshot that fails is reported",
              broke(lambda: C.grab((0, 0, 1, 1))),
              "OSError: the display changed under it")
        check("  and the next one works, the bad grabber having been dropped",
              broke(lambda: C.grab((0, 0, 1, 1)).size), (1, 1))
        C.virtual_screen()
        Flaky.live.ok = False
        check("the screen bounds recover the same way",
              (broke(C.virtual_screen), broke(C.virtual_screen)),
              ("OSError: the display changed under it", (0, 0, 8, 4)))
    finally:
        C._MSS = real
        C.close_sct()

    # The bug this replaced: `self.region is None or self._should_refind()`
    # meant the left half was true for as long as no board was found, so the
    # backoff never got a say and the hunt ran on every tick.
    class Idle:
        locked_on = False

    w = C.Worker.__new__(C.Worker)
    w.region = None
    w.manual = False
    w.lost = False
    w._quiet = 0
    # Not frame zero. The shape this replaced re-hunts on every REFIND_IDLE-th
    # frame, and frame zero is one of those, so a check made there would pass
    # against the bug as well as against the fix.
    w._frames = 1
    w.tracker = Idle()

    w._misses, w._last_hunt = 0, 0.0
    check("hunts at once when nothing has been tried",
          C.Worker._should_refind(w), True)

    w._misses, w._last_hunt = 3, time.time()
    check("  but not again straight away", C.Worker._should_refind(w), False)
    w._last_hunt = time.time() - (C.IDLE_BACKOFF[3] + 0.05)
    check("  and does once the gap has passed", C.Worker._should_refind(w), True)

    # Read the widening through _should_refind rather than off the table, since
    # the index that clamps it is the part that can be wrong. Half a second
    # clears the early steps and not the late ones, and the miss count is run
    # well past the end of the table to show it clamps instead of raising.
    w._last_hunt = time.time() - 0.55
    w._misses = 0
    early = C.Worker._should_refind(w)
    w._misses = len(C.IDLE_BACKOFF) * 10
    late = C.Worker._should_refind(w)
    check("half a second is enough early and not enough later", (early, late),
          (True, False))
    w._last_hunt = time.time() - (max(C.IDLE_BACKOFF) + 0.05)
    check("  and the wait stops widening rather than running away",
          C.Worker._should_refind(w), True)

    # A board that turns up has to reset the wait, or one idle stretch would
    # slow every later hunt down for the rest of the session. _tick does that
    # by zeroing _misses, so drive the real thing rather than assert the table.
    w.region = None
    w._misses, w._last_hunt = 5, 0.0
    w.out = queue.Queue()
    found = (100, 100, 400, 400)
    # Stop the tick once the hunt has been dealt with. Everything past this is
    # the reader, which has a screen of its own to be tested against.
    w._read_settled = lambda: (None, None, False)
    hunt = C.find_board_on_screen
    try:
        C.find_board_on_screen = lambda: found
        C.Worker._tick(w)
    finally:
        C.find_board_on_screen = hunt
    check("finding a board resets the wait", (w.region, w._misses), (found, 0))

    # A hunt that misses while a region is still held is not the kind of miss
    # the backoff is counting. The routine re-hunt comes back empty whenever
    # the board has not moved, and counting those left the first hunt after the
    # region was finally given up waiting the longest gap in the table.
    w.lost = False
    w._misses, w._quiet, w._frames = 0, 0, 0
    w.out = queue.Queue()
    try:
        C.find_board_on_screen = lambda: None
        for _ in range(C.REFIND_IDLE * 10):
            C.Worker._tick(w)
    finally:
        C.find_board_on_screen = hunt
    check("misses under a region that is still held are not counted",
          (w.region, w._misses), (found, 0))

    # Idle frames are the ones this branch is meant to make cheap, and every
    # "searching" costs the Tk thread two label reconfigures and an arrow sync.
    w.region, w.lost = None, False
    w._misses, w._last_hunt, w._searching = 0, 0.0, False
    w.out = queue.Queue()
    try:
        C.find_board_on_screen = lambda: None
        for _ in range(10):
            C.Worker._tick(w)
    finally:
        C.find_board_on_screen = hunt
    check("ten idle frames say there is no board once", w.out.qsize(), 1)


def main():
    print("mode: headless. The label checks build the real Tk app with its"
          " window withdrawn")
    print("      and its capture pointed at a blank image, so nothing is drawn"
          " and your")
    print("      desktop is never screenshotted. They need Stockfish to run at"
          " all.\n")
    wording()
    capture_checks()
    launch_checks()
    path = CO.find_engine()
    print("\n      engine:", path or "not found")
    if path is None:
        print("SKIP  engine checks (set STOCKFISH_PATH or see the README)")
    else:
        engine_checks(path)
        if os.environ.get("CHESSWATCH_NO_TK"):
            print("SKIP  label checks (CHESSWATCH_NO_TK)")
        else:
            label_checks(path)
    print("\n%d/%d passed" % (sum(R), len(R)))
    return 0 if all(R) else 1


if __name__ == "__main__":
    sys.exit(main())
