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


def main():
    print("mode: headless. The label checks build the real Tk app with its"
          " window withdrawn")
    print("      and its capture pointed at a blank image, so nothing is drawn"
          " and your")
    print("      desktop is never screenshotted. They need Stockfish to run at"
          " all.\n")
    wording()
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
