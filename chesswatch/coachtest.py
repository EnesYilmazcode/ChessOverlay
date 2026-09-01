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

    app.coaching.set(True)
    app._toggle_coaching()
    check("switching it on starts the engine", app.coach is not None, True)

    mate = "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1"
    frame["fen"] = mate
    app._render(frame)
    end = time.time() + 15
    while time.time() + 0 < end and "thinking" in app.lbl_detail.cget("text"):
        app._drain_coach()
        root.update()
        time.sleep(0.05)
    check("the move is the big thing on screen", app.lbl_move.cget("text"), "Ra8#")
    check("  said in words underneath it",
          app.lbl_detail.cget("text"), "rook: a1 to a8, with check")
    check("  and whose move it is", app.lbl_turn.cget("text"), "your move")
    def points(widget):
        return max(int(part) for part in str(widget.cget("font")).split()
                   if part.isdigit())

    check("  the move is the biggest text in the window",
          points(app.lbl_move) > max(points(w) for w in
                                     (app.lbl_detail, app.lbl_turn,
                                      app.lbl_status, app.lbl_result)), True)

    # The same position with black to play is the opponent's move, and the
    # advice for the position just left behind must not be shown against it.
    app.lbl_move.configure(text="stale")
    app.coach_fen = "something else entirely"
    app._drain_coach()
    check("advice for a position already played past is dropped",
          app.lbl_move.cget("text"), "stale")

    # One switch now. It used to be two, and turning the arrow on already
    # force-enabled the advice behind it, so they were never independent.
    check("there is one coaching switch, not two",
          (hasattr(app, "coaching"), hasattr(app, "coach_on"),
           hasattr(app, "arrow_on")), (True, False, False))
    app.coaching.set(False)
    app._toggle_coaching()
    check("switching it off clears the move", app.lbl_move.cget("text"), "")

    # The drawer holds everything that exists to debug the reader.
    check("the diagnostics start hidden", app.drawer.winfo_manager(), "")
    app._toggle_drawer()
    check("  and the drawer shows them", app.drawer.winfo_manager(), "pack")
    app._toggle_drawer()
    check("  and puts them away again", app.drawer.winfo_manager(), "")

    # A missing engine has to say so where the move would have been, rather
    # than silently unticking a box and leaving the window empty.
    engine, running = CO.find_engine, app.coach
    try:
        CO.find_engine = lambda: None
        app.coach = None
        app.coaching.set(True)
        app._toggle_coaching()
    finally:
        CO.find_engine = engine
        app.coach = running
    check("a missing engine says so in the move's place",
          ("Stockfish" in app.lbl_detail.cget("text"), app.coaching.get()),
          (True, False))

    app.coach.stop()
    root.destroy()


def main():
    wording()
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
