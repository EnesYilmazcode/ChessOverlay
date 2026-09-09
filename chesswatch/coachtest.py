"""Checks for the coaching half: the engine wrapper and the labels it feeds.

Run:  python coachtest.py

The engine checks need a Stockfish binary. Without one they are skipped and the
rest still runs, because the wording and the stale-answer filter are the parts
most likely to break. How the engine process is started is checked against a
stub instead, so that part runs with or without a binary.
"""

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import time

import chess
import chess.engine

import coach as CO

R = []

# A quiet middlegame with plenty to think about, so a search on it does not
# finish before the checks around it can look at what it published.
BUSY = "r1bq1rk1/pp2ppbp/2np1np1/8/2BNP3/2N1B3/PPP2PPP/R2Q1RK1 w - - 0 1"
MATE = "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1"
OVER = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"

# Black's king is not on it. chess.Board builds this happily, and Stockfish
# exits with an access violation the moment it is asked about it, taking the
# whole coaching thread with it.
NO_KING = "8/5ppp/8/8/8/6P1/5P1P/R5K1 w - - 0 1"


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

    c = CO.Coach("stockfish", think_seconds=0.05)
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


def config_bounds():
    """What a hand-edited config.json is allowed to ask the engine for.

    Nothing else enforces it. The menu can only offer what it lists, but the
    file it saves to is text, and a think time nobody bounded is a core held
    for as long as you take over a move.
    """
    print("\n-- what the config file can ask for ----------------------")
    check("an offered time is kept", CO.nearest_think(2.5), 2.5)
    check("one in between lands on the nearer",
          [CO.nearest_think(v) for v in (0.4, 0.9, 2.0)], [0.3, 1.0, 2.5])
    check("an unbounded think is pulled back to the longest offered",
          CO.nearest_think(60), 2.5)
    check("and nonsense falls back rather than raising before the window exists",
          [CO.nearest_think(v) for v in (None, "", "soon", [])],
          [CO.DEFAULT_THINK] * 4)


# --------------------------------------------------- the move to avoid

def line(cp, uci, depth=14):
    return CO.Line(chess.engine.Cp(cp), chess.Move.from_uci(uci), depth)


def mistake_rules():
    """Which move gets the warning, with no engine anywhere near it.

    Every rule here was written against a measurement over 45 positions from
    real recorded games, and each one is a case where the shallow answer was
    wrong about something. They are separate functions so they can be checked
    against made-up scores, which is the only way to reach the cases that
    matter: a mate that is not there, and a position already won.
    """
    print("\n-- which move the warning is about ------------------------")
    board = after(["e4", "e5", "Nf3", "Nc6"])         # white to move
    best = chess.Move.from_uci("f1c4")

    rough = [line(30, "f1c4"), line(-20, "d2d3"), line(-90, "h2h3"),
             line(-300, "f3g1"), line(-400, "f1a6")]
    check("a move has to be far enough behind to be nominated at all",
          CO.shortlist(board, best, rough, drop=100),
          [chess.Move.from_uci(u) for u in ("h2h3", "f3g1", "f1a6")])
    check("  and the move it is being compared against is never one of them,"
          " however small the drop it is asked for",
          best in CO.shortlist(board, best, rough, drop=0), False)
    check("  the shortlist is capped, since every one of them is searched",
          len(CO.shortlist(board, best, rough, drop=0, most=2)), 2)
    check("nothing to nominate is not an error", CO.shortlist(board, best, []), [])

    # Nxe5 is a capture and h4 is not, and the capture is the worse of the two.
    # A player's eye goes to the capture, so that is the one worth warning
    # about even though it is not the closest call.
    eye = [line(30, "f1c4"), line(-120, "h2h4"), line(-260, "f3e5")]
    check("a capture is preferred to a quiet move that is not as bad",
          CO.shortlist(board, best, eye, drop=100)[0],
          chess.Move.from_uci("f3e5"))

    # The weak engine's move is a different kind of guess from the shortlist's:
    # the shortlist reads temptation off the board, this is a move something
    # actually played. It is added rather than swapped in.
    caps = CO.shortlist(board, best, rough, drop=100, most=2)
    weak = chess.Move.from_uci("b1c3")
    check("the weak engine's move joins the shortlist",
          CO.with_tempted(caps, best, weak), caps + [weak])
    check("  on top of the cap, not in place of anything on it",
          len(CO.with_tempted(caps, best, weak)), len(caps) + 1)
    check("  and it can be the only candidate there is",
          CO.with_tempted([], best, weak), [weak])
    check("a weak engine that agrees with the strong one names no mistake",
          CO.with_tempted(caps, best, best), caps)
    check("  nor does it when it picks something already nominated",
          CO.with_tempted(caps, best, caps[0]), caps)
    check("no second engine leaves the shortlist as it was",
          CO.with_tempted(caps, best, None), caps)

    # Three things, not one thing and silence. The wording depends on which,
    # because "not" about a move half a pawn behind teaches that everything
    # except the engine's first choice is wrong.
    def sc(cp):
        return chess.engine.Cp(cp)
    check("far enough behind, in a game still worth playing, is a mistake",
          CO.standing(sc(30), sc(-120)), "mistake")
    check("  worse but not by much is weaker, not a mistake",
          CO.standing(sc(30), sc(-20)), "weaker")
    check("  and two moves that are the same move are nothing at all",
          CO.standing(sc(30), sc(20)), None)
    check("a game already won is decided, however big the drop",
          CO.standing(sc(900), sc(600)), "decided")
    check("  and so is one already lost",
          CO.standing(sc(-700), sc(-900)), "decided")
    check("  the bar for decided is the worse move still winning, not the best",
          CO.standing(sc(700), sc(200)), "mistake")

    check("the warning is the closest call that still clears the bar",
          CO.most_tempting([(400, chess.Move.from_uci("c6d4")),
                            (150, chess.Move.from_uci("g8h6")),
                            (40, chess.Move.from_uci("d7d6"))], bar=100),
          (150, chess.Move.from_uci("g8h6")))
    check("  and there is no warning when nothing clears it",
          CO.most_tempting([(40, chess.Move.from_uci("d7d6"))], bar=100), None)
    check("  nor when there was nothing to compare", CO.most_tempting([]), None)

    check("lines searched to one depth are evidence about each other",
          CO.comparable([line(30, "e2e4"), line(-100, "g1h3")]), True)
    check("  and lines searched to different depths are not",
          CO.comparable([line(30, "e2e4"), line(-100, "g1h3", depth=9)]), False)
    check("  which nothing at all also is not", CO.comparable([]), False)

    check("a position already won is no place for a warning",
          CO.already_decided(chess.engine.Cp(900), chess.engine.Cp(700)), True)
    check("  nor is one already lost",
          CO.already_decided(chess.engine.Cp(-900), chess.engine.Cp(-1200)),
          True)
    check("  but losing the whole of an advantage is",
          CO.already_decided(chess.engine.Cp(900), chess.engine.Cp(20)), False)
    check("  and so is an ordinary position",
          CO.already_decided(chess.engine.Cp(30), chess.engine.Cp(-90)), False)

    check("a drop that rests on a mate is not believed on one search",
          CO.needs_confirming(500, chess.engine.Mate(3), chess.engine.Cp(50)),
          True)
    check("  in either direction",
          CO.needs_confirming(500, chess.engine.Cp(50), chess.engine.Mate(-3)),
          True)
    check("  nor is a drop too large to be true",
          CO.needs_confirming(900, chess.engine.Cp(50), chess.engine.Cp(-850)),
          True)
    check("  while an ordinary mistake is taken at its word",
          CO.needs_confirming(150, chess.engine.Cp(50), chess.engine.Cp(-100)),
          False)

    check("how much worse, in pawns",
          CO.drop_words(chess.engine.Cp(40), chess.engine.Cp(-80)), "1.2 worse")
    check("  except that mate is not a number of pawns",
          CO.drop_words(chess.engine.Mate(3), chess.engine.Cp(120)),
          "throws away mate in 3")
    check("  in either direction",
          CO.drop_words(chess.engine.Cp(120), chess.engine.Mate(-2)),
          "walks into mate in 2")
    check("the think time dial drives how deep this pass goes",
          [CO.depths_for(t) for t in CO.THINK_CHOICES],
          [CO.DEPTHS[t] for t in CO.THINK_CHOICES])
    check("  including whatever a hand-edited config file asks for",
          CO.depths_for(60), CO.DEPTHS[2.5])
    check("  and every offered time has an answer",
          sorted(CO.DEPTHS), sorted(CO.THINK_CHOICES))


def mistake_engine_checks(path):
    """The whole second pass against the real engine.

    Which move it picks is not checked, and cannot be: at 0.3s the same
    position gives Nxe5 one run and b4 the next, and both are real mistakes.
    What is checked is everything that would be a bug whichever move it names.
    """
    print("\n-- the move to avoid, against the real engine -------------")
    c, ready = start(path, 0.30)
    check("the engine starts", ready, "ready")

    def both(fen, seconds=30):
        """The last word on a position, and the warning that follows it."""
        c.ask(fen)
        advice = warning = None
        order = []
        end = time.time() + seconds
        while time.time() < end and warning is None:
            try:
                kind, payload = c.out.get(timeout=0.2)
            except queue.Empty:
                continue
            if kind == "engine" and payload != "ready":
                print("        engine said: %s" % payload)
                break
            if kind == "advice" and payload.get("final"):
                advice, order = payload, order + ["advice"]
            elif kind == "mistake":
                warning, order = payload, order + ["mistake"]
        return advice, warning, order

    board = chess.Board(BUSY)
    advice, warning, order = both(BUSY)
    check("a position with a real mistake in it gets a warning",
          warning is not None, True)
    if advice is not None and warning is not None:
        move = chess.Move.from_uci(warning["uci"])
        print("      it named %s, %s" % (warning["san"], warning["worse"]))
        check("  which is a legal move in the position it is about",
              move in board.legal_moves, True)
        check("  and not the move it has just told you to play",
              warning["uci"] == advice["uci"], False)
        check("  worth at least the bar it has to clear",
              warning["drop"] >= CO.MIN_DROP, True)
        check("  named the way the move to play is, and about the same board",
              (warning["fen"], warning["turn"],
               chess.square_name(move.from_square) in warning["text"],
               chess.square_name(move.to_square) in warning["text"]),
              (BUSY, "white", True, True))
    check("the move to play is published first, so it never waits on this",
          order, ["advice", "mistake"])

    # The crash this guard is for takes the engine down for the rest of the
    # session, so the check is that the position after it is still answered.
    c.ask(NO_KING)
    time.sleep(0.5)
    advice, _, _ = both(MATE)
    check("a position with a king missing does not take the engine down",
          advice and advice["san"], "Ra8#")
    c.stop()


def engine_checks(path):
    print("\n-- against the real engine -------------------------------")
    c, ready = start(path, 0.20)
    check("the engine starts", ready, "ready")

    def last_word(seconds=15):
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
    got = last_word()
    check("finds the mate in one", got and got["san"], "Ra8#")
    check("and says so in the score", got and got["score"], "mate in 1")

    # Winning a free queen.
    c.ask("rnbqkbnr/ppp1pppp/8/3p4/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 2")
    got = last_word()
    check("whose move it is", got and got["turn"], "white")

    c.ask(OVER)
    got = last_word()
    check("a finished game gets no advice", got and got.get("over"), True)
    c.stop()


def streaming_checks(path):
    """The two properties of reading the engine mid-search: an answer that is
    not the last word arrives long before the search ends, and a search still
    running gives way at once to a new position."""
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
    # about to be replaced does not read as the verdict. The mark is on the
    # small print rather than on the move itself: the move line is sized to
    # hold the longest SAN there is in a 400px window at 150% scaling, and
    # what an unfinished answer hedges is the score, which is already there.
    def advice(final):
        app.coach.out.put(("advice", {
            "fen": MATE, "over": False, "final": final, "depth": 14,
            "turn": "white", "san": "Ra8#", "uci": "a1a8",
            "text": "rook: a1 to a8, with check", "score": "mate in 1"}))
        app._drain_coach()
        return app.lbl_detail.cget("text")

    app.coach_fen = MATE
    check("an answer still being worked on is marked as such",
          advice(False).endswith("   ..."), True)
    check("and the mark goes when it is the last word",
          advice(True).endswith("mate in 1"), True)
    check("and the move itself reads the same either way",
          app.lbl_coach.cget("text"), "your move  Ra8#")

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
    mistake_rules()
    capture_checks()
    config_bounds()
    launch_checks()
    path = CO.find_engine()
    print("\n      engine:", path or "not found")
    if path is None:
        print("SKIP  engine checks (set STOCKFISH_PATH or see the README)")
    else:
        engine_checks(path)
        mistake_engine_checks(path)
        streaming_checks(path)
        if os.environ.get("CHESSWATCH_NO_TK"):
            print("SKIP  label checks (CHESSWATCH_NO_TK)")
        else:
            label_checks(path)
    print("\n%d/%d passed" % (sum(R), len(R)))
    return 0 if all(R) else 1


if __name__ == "__main__":
    sys.exit(main())
