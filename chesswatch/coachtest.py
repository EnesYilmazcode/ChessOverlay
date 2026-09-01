"""Checks for the coaching half: the engine wrapper and the label it feeds.

Run:  python coachtest.py

The engine checks need a Stockfish binary. Without one they are skipped and the
rest still runs, because the wording and the stale-answer filter are the parts
most likely to break.
"""

import os
import queue
import sys
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


def engine_checks(path):
    print("\n-- against the real engine -------------------------------")
    c = CO.Coach(path, movetime=0.20)
    c.start()

    def wait(kind, seconds=15):
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

    check("the engine starts", wait("engine"), "ready")

    # Mate in one. There is no room for an opinion here.
    mate = "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1"
    got = wait("advice")
    c.ask(mate)
    got = wait("advice")
    check("finds the mate in one", got and got["san"], "Ra8#")
    check("and says so in the score", got and got["score"], "mate in 1")

    # Winning a free queen.
    c.ask("rnbqkbnr/ppp1pppp/8/3p4/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 2")
    got = wait("advice")
    check("whose move it is", got and got["turn"], "white")

    over = "6k1/5ppp/8/8/8/8/5PPP/6K1 w - - 0 1"      # dead drawn, not over
    c.ask("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")           # checkmate on the board
    got = wait("advice")
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

    # The app starts watching the screen the moment it is built. Point it at a
    # scratch folder so a stray frame cannot land in your real games, and at a
    # scratch config so the test does not depend on which switches you left on.
    C.W.GAMES_DIR = tempfile.mkdtemp(prefix="chesswatch-test-")
    C.CONFIG_PATH = os.path.join(C.W.GAMES_DIR, "config.json")

    root = tk.Tk()
    root.withdraw()
    app = C.App(root)
    app._stop()                       # do not watch the screen during a test
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
    screen to be tested: the grabber is asked what it hands back rather than
    what it captured, and the hunt is driven off a clock we control."""
    import threading
    import chesswatch as C

    print("\n-- capture ----------------------------------------------")
    mine = C._sct()
    check("one screen grabber per thread, reused", C._sct() is mine, True)
    theirs = {}
    t = threading.Thread(target=lambda: theirs.setdefault("sct", C._sct()))
    t.start()
    t.join()
    check("  and another thread gets its own", theirs["sct"] is mine, False)
    C.close_sct()
    check("  closing drops it", getattr(C._local, "sct", None), None)

    # The bug this replaced: `self.region is None or self._should_refind()`
    # meant the left half was true for as long as no board was found, so the
    # backoff never got a say and the hunt ran on every tick.
    class Idle:
        locked_on = False

    w = C.Worker.__new__(C.Worker)
    w.region = None
    w.manual = False
    w._quiet = 0
    w._frames = 0
    w.tracker = Idle()

    w._misses, w._last_hunt = 0, 0.0
    check("hunts at once when nothing has been tried",
          C.Worker._should_refind(w), True)

    w._misses, w._last_hunt = 3, time.time()
    check("  but not again straight away", C.Worker._should_refind(w), False)
    w._last_hunt = time.time() - (C.IDLE_BACKOFF[3] + 0.05)
    check("  and does once the gap has passed", C.Worker._should_refind(w), True)

    check("the gap widens with each miss",
          list(C.IDLE_BACKOFF) == sorted(C.IDLE_BACKOFF)
          and C.IDLE_BACKOFF[0] == 0.0 and C.IDLE_BACKOFF[-1] > 0.5, True)
    check("  and stops widening rather than running away",
          max(C.IDLE_BACKOFF) <= 5.0, True)

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


def main():
    wording()
    capture_checks()
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
