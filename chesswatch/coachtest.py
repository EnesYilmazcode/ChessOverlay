"""Checks for the coaching half: the engine wrapper and the label it feeds.

Run:  python coachtest.py

The engine checks need a Stockfish binary. Without one they are skipped and the
rest still runs, because the wording and the stale-answer filter are the parts
most likely to break.
"""

import json
import os
import queue
import sys
import time

import chess

import coach as CO

R = []

# A quiet middlegame with plenty to think about, so a search on it does not
# finish before the checks around it can look at what it published.
BUSY = "r1bq1rk1/pp2ppbp/2np1np1/8/2BNP3/2N1B3/PPP2PPP/R2Q1RK1 w - - 0 1"
MATE = "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1"
OVER = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"


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


def start(path, think):
    """A running coach with the engine already up, so nothing below is timing
    the engine's own start-up."""
    c = CO.Coach(path, think_seconds=think)
    c.start()
    end = time.time() + 30
    while time.time() < end:
        try:
            kind, payload = c.out.get(timeout=0.5)
        except queue.Empty:
            continue
        if kind == "engine":
            return c, payload
    return c, "never said anything"


def engine_checks(path):
    print("\n-- against the real engine -------------------------------")
    c, ready = start(path, 0.20)
    check("the engine starts", ready, "ready")

    def wait(seconds=15):
        """The last word on a position. Anything before it is the engine still
        looking, and is allowed to say something else."""
        end = time.time() + seconds
        while time.time() < end:
            try:
                kind, payload = c.out.get(timeout=0.2)
            except queue.Empty:
                continue
            if kind == "engine":
                return payload
            if payload.get("final") or payload.get("over"):
                return payload
        return None

    # Mate in one. There is no room for an opinion here.
    c.ask(MATE)
    got = wait()
    check("finds the mate in one", got and got["san"], "Ra8#")
    check("and says so in the score", got and got["score"], "mate in 1")

    # Winning a free queen.
    c.ask("rnbqkbnr/ppp1pppp/8/3p4/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 2")
    got = wait()
    check("whose move it is", got and got["turn"], "white")

    c.ask(OVER)
    got = wait()
    check("a finished game gets no advice", got and got.get("over"), True)
    c.stop()


def streaming_checks(path):
    """The engine is read while it is thinking, so the two things that were
    impossible before are now the things most worth checking: an answer that
    is not the last word, and a search that has to be cut short."""
    print("\n-- while it is still thinking ----------------------------")
    c, _ = start(path, 2.0)

    seen = []
    t0 = time.time()
    c.ask(BUSY)
    while time.time() - t0 < 10:
        try:
            kind, payload = c.out.get(timeout=0.2)
        except queue.Empty:
            continue
        if kind != "advice":
            continue
        seen.append((time.time() - t0, payload))
        if payload.get("final"):
            break
    check("something is on screen long before the engine has finished",
          bool(seen) and seen[0][0] < 0.25, True)
    check("everything before the end says it is not the last word",
          all(not p.get("final") for _, p in seen[:-1]), True)
    check("the last one says it is", bool(seen) and seen[-1][1].get("final"), True)
    check("and it carries how far ahead the engine got",
          bool(seen) and seen[-1][1]["depth"] >= 12, True)

    # A search that is still running has to give way at once. Left to itself
    # this one would hold the engine for the rest of its two seconds.
    c.ask(BUSY)                      # BUSY is finished, so this is free
    c.ask("r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 1")
    time.sleep(0.4)
    t0 = time.time()
    c.ask(MATE)
    first = None
    while time.time() - t0 < 10:
        try:
            kind, payload = c.out.get(timeout=0.2)
        except queue.Empty:
            continue
        if kind == "advice" and payload["fen"] == MATE:
            first = time.time() - t0
            break
    check("a new position does not wait for the old search to run out",
          first is not None and first < 0.5, True)
    c.stop()

    # The app asks about the board it can see roughly eight times a second, so
    # everything below is about what those repeats are allowed to cost.
    print("\n-- what the repeats cost ---------------------------------")
    c, _ = start(path, 0.20)
    finals = 0
    t0 = time.time()
    while time.time() - t0 < 4:
        c.ask(BUSY)
        try:
            kind, payload = c.out.get(timeout=0.1)
        except queue.Empty:
            continue
        if kind == "advice" and payload.get("final") and payload["fen"] == BUSY:
            finals += 1
    check("a board held still is searched once, not once a frame", finals, 1)

    overs = 0
    t0 = time.time()
    while time.time() - t0 < 2:
        c.ask(OVER)
        try:
            kind, payload = c.out.get(timeout=0.1)
        except queue.Empty:
            continue
        if kind == "advice" and payload.get("over"):
            overs += 1
    check("a finished game is called over once, not once a frame", overs, 1)
    c.stop()
    c.join(timeout=10)
    check("stopping it puts the thread away", c.is_alive(), False)


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

    frame["fen"] = MATE
    app._render(frame)
    end = time.time() + 15
    while time.time() + 0 < end and "thinking" in app.lbl_coach.cget("text"):
        app._drain_coach()
        root.update()
        time.sleep(0.05)
    check("your move is labelled as yours",
          app.lbl_coach.cget("text").startswith("your move  Ra8#"), True)

    # An answer the engine has not finished with is marked, so a move that is
    # about to be replaced does not read as the verdict.
    def advice(final):
        app.coach.out.put(("advice", {
            "fen": MATE, "over": False, "final": final, "depth": 14,
            "turn": "white", "san": "Ra8#", "uci": "a1a8",
            "text": "rook: a1 to a8, with check", "score": "mate in 1"}))
        app._drain_coach()
        return app.lbl_coach.cget("text")

    app.coach_fen = MATE
    check("an answer still being worked on is marked as such",
          advice(False).endswith("  ..."), True)
    check("and the mark goes when it is the last word",
          advice(True).endswith("mate in 1"), True)

    # The same position with black to play is the opponent's move, and the
    # advice for the position just left behind must not be shown against it.
    app.lbl_coach.configure(text="stale")
    app.coach_fen = "something else entirely"
    app._drain_coach()
    check("advice for a position already played past is dropped",
          app.lbl_coach.cget("text"), "stale")

    # How long the engine gets is a setting, and it has to survive a restart
    # and reach an engine that is already running.
    app.think_choice.set("2.5s")
    app._set_think()
    saved = json.load(open(C.CONFIG_PATH, encoding="utf-8"))
    check("the think time is written to config.json", saved.get("think_seconds"), 2.5)
    check("and reaches an engine that is already going",
          app.coach.think_seconds, 2.5)

    app.coach_on.set(False)
    app._toggle_coach()
    check("switching it off clears the label", app.lbl_coach.cget("text"), "")

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
        streaming_checks(path)
        if os.environ.get("CHESSWATCH_NO_TK"):
            print("SKIP  label checks (CHESSWATCH_NO_TK)")
        else:
            label_checks(path)
    print("\n%d/%d passed" % (sum(R), len(R)))
    return 0 if all(R) else 1


if __name__ == "__main__":
    sys.exit(main())
