"""Headless checks for board reading, move inference and saving.

Run:  python selftest.py [screenshot.png ...]
"""

import sys
import time
import tempfile

import chess
from PIL import Image

import watcher as W
from shots import shot


def check(name, got, want):
    ok = got == want
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        print("        got  ", got)
        print("        want ", want)
    return ok


def play(tracker, board, sans):
    """Feed the tracker the occupancy of each position in turn, as the screen
    would show it."""
    events = []
    for san in sans:
        board.push_san(san)
        events.append(tracker.feed(W.occupancy_of(board, tracker.flipped)))
    return events


def main():
    r = []

    # -- move inference from occupancy alone -----------------------------
    t = W.Tracker = W.BoardTracker(directory=tempfile.mkdtemp())
    r.append(check("locks on when the start position appears",
                   t.feed(W.START_WHITE_VIEW), "newgame"))
    r.append(check("does not re-trigger while nothing has moved",
                   t.feed(W.START_WHITE_VIEW), None))
    r.append(check("colour comes from board orientation", t.game.my_color, "white"))

    b = chess.Board()
    play(t, b, ["d4", "e6", "e4", "c5"])
    r.append(check("follows a normal opening", t.game.moves, ["d4", "e6", "e4", "c5"]))

    # The whole point: pieces are identified by the rules, not by pixels.
    play(t, b, ["Nf3", "Nc6", "Bb5", "Qb6"])
    r.append(check("names the pieces correctly", t.game.moves[-4:],
                   ["Nf3", "Nc6", "Bb5", "Qb6"]))

    # -- castling, en passant, promotion ---------------------------------
    t2 = W.BoardTracker(directory=tempfile.mkdtemp())
    t2.feed(W.START_WHITE_VIEW)
    b2 = chess.Board()
    play(t2, b2, ["e4", "e5", "Nf3", "Nf6", "Bc4", "Bc5", "O-O"])
    r.append(check("castling reads as castling", t2.game.moves[-1], "O-O"))

    t3 = W.BoardTracker(directory=tempfile.mkdtemp())
    t3.feed(W.START_WHITE_VIEW)
    b3 = chess.Board()
    play(t3, b3, ["e4", "d5", "e5", "f5", "exf6"])
    r.append(check("en passant reads as en passant", t3.game.moves[-1], "exf6"))

    t4 = W.BoardTracker(directory=tempfile.mkdtemp())
    t4.feed(W.START_WHITE_VIEW)
    b4 = chess.Board()
    play(t4, b4, ["a4", "b5", "axb5", "a6", "bxa6", "Nf6", "a7", "Ne4", "axb8=Q"])
    r.append(check("promotion defaults to a queen", t4.game.moves[-1], "axb8=Q"))

    # All four promotions leave an identical white/black occupancy map, so the
    # piece reader has to say which one it was. Without it an underpromotion is
    # silently written down as a queen.
    import pieces as _P
    from fakeboard import Renderer as _Rend
    _render = _Rend(shot("1"), (225, 63, 824))
    PROMO_LINE = ["a4", "b5", "axb5", "a6", "bxa6", "Nf6", "a7", "Ne4"]
    for promo in ("axb8=Q", "axb8=N", "axb8=R", "axb8=B"):
        tp = W.BoardTracker(directory=tempfile.mkdtemp(), reader=_P.PieceReader())
        tp.feed(W.START_WHITE_VIEW)
        bp = chess.Board()
        for san in PROMO_LINE:
            bp.push_san(san)
            tp.feed(W.occupancy_of(bp, False))
        bp.push_san(promo)
        tp.feed(W.occupancy_of(bp, False), _render.render(bp, False))
        r.append(check("  reads %s off the pixels" % promo,
                       tp.game.moves[-1], promo))

    # -- a dropped frame --------------------------------------------------
    t5 = W.BoardTracker(directory=tempfile.mkdtemp())
    t5.feed(W.START_WHITE_VIEW)
    b5 = chess.Board()
    b5.push_san("e4")
    b5.push_san("e5")
    ev = t5.feed(W.occupancy_of(b5, False))       # both moves in one frame
    r.append(check("recovers two moves from one frame",
                   (ev, t5.game.moves), ("moves", ["e4", "e5"])))

    # -- garbage frames are ignored, not guessed --------------------------
    junk = ["BBBBBBBB"] * 8
    before = list(t5.game.moves)
    r.append(check("ignores an unreadable frame", t5.feed(junk), None))
    r.append(check("game untouched by the junk frame", t5.game.moves, before))

    # -- playing black ----------------------------------------------------
    t6 = W.BoardTracker(directory=tempfile.mkdtemp())
    r.append(check("detects a flipped board", t6.feed(W.START_BLACK_VIEW), "newgame"))
    r.append(check("colour is black when the board is flipped",
                   t6.game.my_color, "black"))
    b6 = chess.Board()
    play(t6, b6, ["e4", "c5"])
    r.append(check("flipped board still reads moves", t6.game.moves, ["e4", "c5"]))
    r.append(check("attribution when I am black",
                   [m["by"] for m in t6.game.to_dict()["moves"]],
                   ["opponent", "me"]))

    # -- checkmate is detected from the position --------------------------
    t7 = W.BoardTracker(directory=tempfile.mkdtemp())
    t7.feed(W.START_WHITE_VIEW)
    b7 = chess.Board()
    evs = play(t7, b7, ["f3", "e5", "g4", "Qh4"])
    r.append(check("reports the game as finished", evs[-1], "finished"))
    r.append(check("result read off the board", t7.game.result, "0-1"))
    r.append(check("termination", t7.game.termination, "checkmate"))
    r.append(check("outcome from my side", t7.game.won, "lost"))
    r.append(check("pgn carries the result",
                   t7.game.to_pgn().strip().splitlines()[-1],
                   "1. f3 e5 2. g4 Qh4# 0-1"))

    # -- en passant on a game joined part way ------------------------------
    # A picture cannot show an en passant right, so a rebuilt position loses it
    # and the capture that depends on it looks impossible.
    bep = chess.Board()
    for san in ("e4", "Nf6", "e5", "d5"):
        bep.push_san(san)
    rebuilt = W.board_from_grid(W.grid_of(bep, False), False, chess.WHITE)
    r.append(check("a rebuilt position has no en passant right",
                   rebuilt.ep_square, None))
    tep = W.BoardTracker(directory=tempfile.mkdtemp())
    tep._begin(rebuilt, False, joined_late=True)
    bep.push_san("exd6")
    r.append(check("but the capture is still recognised",
                   (tep.feed(W.occupancy_of(bep, False)), tep.game.moves),
                   ("moves", ["exd6"])))
    r.append(check("  and the recorded start position gains the right",
                   tep.game.start_fen.split()[3], "d6"))

    # -- a finished game is not to be touched again -----------------------
    # chess.com opens Game Review the moment a game ends, and clicking back
    # through it puts an earlier position on screen. Reading that as a takeback
    # destroyed the finished record, including the copy already on disk.
    import pieces as _P2
    from fakeboard import Renderer as _R2
    _rend2 = _R2(shot("1"), (225, 63, 824))
    dirn = tempfile.mkdtemp()
    tf = W.BoardTracker(directory=dirn, reader=_P2.PieceReader())
    tf.feed(W.START_WHITE_VIEW)
    bf = chess.Board()
    for san in ("f3", "e5", "g4", "Qh4"):
        bf.push_san(san)
        tf.feed(W.occupancy_of(bf, False))
    tf.game.save()
    saved_path = tf.game.path
    rewound = chess.Board()
    for san in ("f3", "e5"):
        rewound.push_san(san)
    r.append(check("reviewing a finished game changes nothing",
                   tf.check(_rend2.render(rewound)),
                   "game finished, leaving the record alone"))
    r.append(check("  moves survive review", tf.game.moves,
                   ["f3", "e5", "g4", "Qh4#"]))
    r.append(check("  result survives review", tf.game.result, "0-1"))
    tf.game.save()
    r.append(check("  and the file on disk is still the whole game",
                   open(saved_path, encoding="utf-8").read().strip().endswith(
                       "1. f3 e5 2. g4 Qh4# 0-1"), True))
    r.append(check("  the fast reader ignores review too",
                   tf.feed(W.occupancy_of(rewound, False)), None))

    # -- draws chess.com actually declares ---------------------------------
    # python-chess only reports the automatic fivefold / seventy-five move
    # versions on its own; chess.com ends the game at three and at fifty.
    gr = W.Game("white")
    brep = chess.Board()
    for san in ("Nf3", "Nf6", "Ng1", "Ng8", "Nf3", "Nf6", "Ng1", "Ng8"):
        brep.push_san(san)
    gr.note_outcome(brep)
    r.append(check("threefold repetition is a draw",
                   (gr.result, gr.termination), ("1/2-1/2", "repetition")))
    gf = W.Game("white")
    gf.note_outcome(chess.Board("8/8/4k3/8/8/4K3/8/6R1 w - - 100 80"))
    r.append(check("fifty move rule is a draw",
                   (gf.result, gf.termination), ("1/2-1/2", "fifty move rule")))

    # -- walking back into the starting position is not a new game ---------
    ts = W.BoardTracker(directory=tempfile.mkdtemp())
    ts.feed(W.START_WHITE_VIEW)
    bsh = chess.Board()
    for san in ("Nf3", "Nf6", "Ng1", "Ng8"):
        bsh.push_san(san)
        ev = ts.feed(W.occupancy_of(bsh, False))
    r.append(check("a knight shuffle back to the start stays one game",
                   (ev, ts.game.moves, len(ts.finished)),
                   ("moves", ["Nf3", "Nf6", "Ng1", "Ng8"], 0)))

    # -- a saved game is never shortened on disk --------------------------
    # A resignation or timeout leaves no mark on the board, so the tracker
    # cannot know the game is over, and Game Review looks exactly like a
    # takeback. Rewinding in memory is fine; overwriting the file is not.
    dres = tempfile.mkdtemp()
    tres = W.BoardTracker(directory=dres, reader=_P2.PieceReader())
    tres.feed(W.START_WHITE_VIEW)
    bres = chess.Board()
    RESIGNED = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6", "O-O", "Be7"]
    for san in RESIGNED:
        bres.push_san(san)
        tres.feed(W.occupancy_of(bres, False))
    tres.game.save()
    res_path = tres.game.path
    rewind = chess.Board()
    for san in RESIGNED[:4]:
        rewind.push_san(san)
    tres.check(_rend2.render(rewind))
    tres.game.save()
    r.append(check("reviewing a resigned game does not shorten the file",
                   open(res_path, encoding="utf-8").read().strip().endswith(
                       "5. O-O Be7 *"), True))
    # A real takeback diverges rather than truncating, and must still be saved.
    tres.game.moves = RESIGNED[:4] + ["d4"]
    tres.game.save()
    r.append(check("  but a real takeback still writes",
                   open(res_path, encoding="utf-8").read().strip().endswith(
                       "3. d4 *"), True))

    # -- turning the board round mid-game ---------------------------------
    tfl = W.BoardTracker(directory=tempfile.mkdtemp())
    tfl.feed(W.START_WHITE_VIEW)
    bfl = chess.Board()
    for san in ("e4", "e5", "Nf3", "Nc6"):
        bfl.push_san(san)
        tfl.feed(W.occupancy_of(bfl, False))
    tfl.feed(W.occupancy_of(bfl, True))          # chess.com flips on one key
    r.append(check("a board flip is not a lost game", tfl.flipped, True))
    for san in ("Bc4", "Bc5"):
        bfl.push_san(san)
        tfl.feed(W.occupancy_of(bfl, True))
    r.append(check("  and recording carries on through it", tfl.game.moves,
                   ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"]))

    # -- refusing stories that are genuinely ambiguous ---------------------
    # A double push then an en passant capture leaves the same picture as a
    # single push then an ordinary capture. Nothing can tell them apart.
    tam = W.BoardTracker(directory=tempfile.mkdtemp())
    tam.feed(W.START_WHITE_VIEW)
    bam = chess.Board()
    for san in ("e4", "f5", "e5", "f4", "Nf3", "h6"):
        bam.push_san(san)
        tam.feed(W.occupancy_of(bam, False))
    kept = list(tam.game.moves)
    bam.push_san("g4")
    bam.push_san("fxg3")
    r.append(check("refuses an en passant that could have been two pushes",
                   (tam.feed(W.occupancy_of(bam, False)), tam.game.moves),
                   (None, kept)))

    # A capture and recapture on one square hides the square completely.
    tcap = W.BoardTracker(directory=tempfile.mkdtemp())
    tcap._begin(chess.Board("8/2n5/8/1p1p4/8/2N5/k6K/8 w - - 0 1"), False)
    bcap = chess.Board("8/2n5/8/1p1p4/8/2N5/k6K/8 w - - 0 1")
    bcap.push_san("Nxb5")
    bcap.push_san("Nxb5")
    r.append(check("refuses a capture and recapture it cannot tell apart",
                   (tcap.feed(W.occupancy_of(bcap, False)), tcap.game.moves),
                   (None, [])))

    # -- board detection --------------------------------------------------
    ref = Image.open(shot("1")).convert("RGB")
    r.append(check("a correctly placed board scores full marks",
                   W.grid_score(ref, 225, 63, 824) == 1.0, True))
    # A rectangle shifted by part of a square used to score a perfect 1.00
    # while reading complete nonsense off it.
    r.append(check("a rectangle off by part of a square is rejected",
                   max(W.grid_score(ref, 225 + d, 63, 824)
                       for d in (20, 40, 60)) < 0.78, True))
    # Unless a board's size divides by eight, the browser antialiases the seven
    # internal seams, which used to split the board into eight fragments.
    board_only = ref.crop((225, 63, 225 + 824, 63 + 824))
    odd = []
    for size in (641, 613, 555, 499, 333, 251, 137):
        scaled = board_only.resize((size, size), Image.LANCZOS)
        canvas = Image.new("RGB", (size + 300, size + 240), (38, 36, 33))
        canvas.paste(scaled, (150, 120))
        odd.append(W.find_board(canvas) is not None)
    r.append(check("boards whose size does not divide by eight are found",
                   all(odd), True))

    # -- a new game rotates the old one to disk ---------------------------
    tmp = tempfile.mkdtemp()
    t8 = W.BoardTracker(directory=tmp)
    t8.feed(W.START_WHITE_VIEW)
    b8 = chess.Board()
    play(t8, b8, ["e4", "e5"])
    r.append(check("new game closes out the old one",
                   t8.feed(W.START_WHITE_VIEW), "newgame"))
    r.append(check("old game was saved", len(t8.finished), 1))
    r.append(check("and it is on disk", t8.finished[0].path is not None, True))
    r.append(check("fresh game starts empty", t8.game.moves, []))

    # Two games can begin inside the same second, a rematch especially, and
    # they must not land on the same filename.
    same = tempfile.mkdtemp()
    g1, g2 = W.Game("white", same), W.Game("white", same)
    g2.started = g1.started
    g1.moves, g2.moves = ["e4"], ["d4"]
    p1, p2 = g1.save(), g2.save()
    r.append(check("two games in the same second get separate files", p1 != p2, True))
    g1.moves = ["e4", "e5"]
    g1.save()
    r.append(check("  and each keeps autosaving to its own file",
                   (g1.path == p1,
                    open(p2, encoding="utf-8").read().strip().endswith("1. d4 *")),
                   (True, True)))

    # -- the piece-level checker -------------------------------------------
    import pieces as P
    from fakeboard import Renderer as _R
    render = _R(shot("1"), (225, 63, 824))
    reader = P.PieceReader()
    r.append(check("piece templates load", reader.ready, True))

    LINE = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "O-O", "Nf6", "d3", "d6",
            "Bg5", "h6"]

    def tracked(upto):
        t = W.BoardTracker(directory=tempfile.mkdtemp(), reader=P.PieceReader())
        t.feed(W.START_WHITE_VIEW)
        bd = chess.Board()
        for san in LINE[:upto]:
            bd.push_san(san)
            t.feed(W.occupancy_of(bd, False))
        return t, bd

    t, bd = tracked(8)
    r.append(check("checker confirms a correct game",
                   t.check(render.render(bd)), "position confirmed"))

    # Three moves happen with no frame captured in between.
    for san in LINE[8:11]:
        bd.push_san(san)
    t.check(render.render(bd))
    r.append(check("checker catches up after missed moves",
                   t.game.moves, LINE[:11]))

    # Three moves CAN transpose: 1.e4 e5 2.Nf3 and 1.Nf3 e5 2.e4 leave the same
    # picture. Writing down a plausible order that did not happen would be
    # worse than admitting the gap, so it must refuse.
    trans = W.BoardTracker(directory=tempfile.mkdtemp(), reader=P.PieceReader())
    trans.feed(W.START_WHITE_VIEW)
    bt = chess.Board()
    for san in ("e4", "e5", "Nf3"):
        bt.push_san(san)
    trans.check(render.render(bt))
    r.append(check("refuses a skip that could be two move orders",
                   trans.game.moves, []))

    two = W.BoardTracker(directory=tempfile.mkdtemp(), reader=P.PieceReader())
    two.feed(W.START_WHITE_VIEW)
    b2p = chess.Board()
    for san in ("e4", "e5"):
        b2p.push_san(san)
    two.check(render.render(b2p))
    r.append(check("but two skipped moves cannot transpose, so they are kept",
                   two.game.moves, ["e4", "e5"]))

    bd2 = chess.Board()
    for san in LINE[:9]:
        bd2.push_san(san)
    t.check(render.render(bd2))
    r.append(check("checker rewinds a takeback", t.game.moves, LINE[:9]))

    # Joining a game already under way. A knight move leaves the board's
    # orientation ambiguous, because a rotated board is a legal game too; the
    # first pawn move settles it.
    cold = W.BoardTracker(directory=tempfile.mkdtemp(), reader=P.PieceReader())
    bd3 = chess.Board()
    for san in LINE:
        bd3.push_san(san)
    cold.check(render.render(bd3))
    r.append(check("does not guess the orientation", cold.locked_on, False))
    bd3.push_san("Nbd2")
    cold.check(render.render(bd3))
    r.append(check("a piece move is still ambiguous", cold.locked_on, False))
    bd3.push_san("a6")
    cold.check(render.render(bd3))
    r.append(check("a pawn move settles it", cold.locked_on, True))
    r.append(check("  and the position is right",
                   cold.board.board_fen(), bd3.board_fen()))
    r.append(check("  colour from orientation", cold.game.my_color, "white"))
    r.append(check("  pgn records where it joined",
                   "FEN" in cold.game.to_pgn(), True))
    r.append(check("  pgn says so in words",
                   "in progress" in cold.game.to_pgn(), True))
    r.append(check("  json warns the numbering is relative",
                   "not chess.com" in cold.game.to_dict()["numbering"], True))

    # A game joined on black's move has no white move in its first row, and the
    # display has to cope with that rather than printing None.
    half = W.Game("white", tempfile.mkdtemp(),
                  start_fen="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1")
    half.moves = ["e5", "Nf3"]
    r.append(check("  a join on black's move leaves the white slot empty",
                   half.rows(), [(1, None, "e5"), (2, "Nf3", None)]))

    flip = W.BoardTracker(directory=tempfile.mkdtemp(), reader=P.PieceReader())
    bd4 = chess.Board()
    for san in LINE:
        bd4.push_san(san)
    flip.check(render.render(bd4, True))
    bd4.push_san("Nbd2")
    flip.check(render.render(bd4, True))
    bd4.push_san("a6")
    flip.check(render.render(bd4, True))
    r.append(check("joins a flipped game too",
                   (flip.locked_on, flip.game.my_color), (True, "black")))

    # Telling it your colour fixes the orientation but not whose turn it is,
    # so it still wants one move. The gain is that now ANY move will do, rather
    # than specifically a pawn move.
    told = W.BoardTracker(directory=tempfile.mkdtemp(), reader=P.PieceReader())
    told.set_colour("black")
    bd5 = chess.Board()
    for san in LINE:
        bd5.push_san(san)
    told.check(render.render(bd5, True))
    bd5.push_san("Nbd2")
    told.check(render.render(bd5, True))
    r.append(check("told the colour, a piece move is enough",
                   (told.locked_on, told.game.my_color if told.game else None),
                   (True, "black")))
    r.append(check("  and it joined at the right position",
                   told.board.board_fen(), bd5.board_fen()))

    # -- small browser windows --------------------------------------------
    # A narrow window hides the move list but shrinks the board too, so check
    # how far down it still reads. LANCZOS, because a real small board is
    # antialiased and that is the harsher test.
    from fakeboard import Renderer
    renderer = Renderer(shot("1"), (225, 63, 824))
    bs = chess.Board()
    for san in ("e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "O-O", "Nf6",
                "d3", "d6", "Bg5"):
        bs.push_san(san)
    full = renderer.render(bs)
    want = W.occupancy_of(bs, False)
    print()
    worst = None
    for size in (664, 480, 336, 240, 200, 160, 128):
        got = W.read_occupancy(full.resize((size, size), Image.LANCZOS))
        ok = got == want
        print("      board %3dpx (%.0fpx squares): %s"
              % (size, size / 8, "reads correctly" if ok else "too small"))
        if ok:
            worst = size
    r.append(check("  reads a board shrunk to 240px", worst is not None and worst <= 240,
                   True))

    # -- board widths that do not divide by 8 ------------------------------
    # Squares then land on fractional pixel boundaries, so one square's sampled
    # window can start a pixel out from its neighbour's.
    odd = [size for size in (823, 501, 333, 259, 205)
           if W.read_occupancy(full.resize((size, size), Image.LANCZOS)) != want]
    r.append(check("  reads a board whose width is not a multiple of 8", odd, []))

    # -- the arithmetic at one square's decision boundaries -----------------
    # Real captures never happen to land on a boundary, so these counts are
    # placed pixel by pixel. MIN_COVERAGE is 4.86 pixels out of 324, so 4
    # bright pixels is an empty square and 5 is not, and an exact tie reads B,
    # not W. Every border is filled with alternating black and white, so a
    # reading that strays outside the middle 56% cannot come back ".".
    step = W.GRID // 8
    margin = int(step * 0.22)
    inset = step - 2 * margin
    area = inset * inset
    on = next(n for n in range(area + 1) if n / area >= W.MIN_COVERAGE)
    under, half = on - 1, area // 2
    counts = [
        [(n, 0) for n in range(8)],
        [(0, n) for n in range(8)],
        [(n, n) for n in range(8)],
        [(n + 1, n) for n in range(8)],
        [(n, n + 1) for n in range(8)],
        [(area, 0), (0, area), (half, half), (half + 1, half - 1),
         (half - 1, half + 1), (area - on, on), (on, area - on), (1, area - 1)],
        [(half - 1, half), (half, half - 1), (half - 2, half + 2),
         (half + 2, half - 2), (on, on), (under, under), (0, on), (on, 0)],
        [(0, 0), (0, under), (under, 0), (0, on), (on, 0), (under, on),
         (on, under), (under, under)],
    ]
    pixels = bytearray([0, 255] * (W.GRID * W.GRID // 2))
    for row, pairs in enumerate(counts):
        for col, (bright, dark) in enumerate(pairs):
            fill = [255] * bright + [0] * dark + [150] * (area - bright - dark)
            top, left = row * step + margin, col * step + margin
            for i, value in enumerate(fill):
                pixels[(top + i // inset) * W.GRID + left + i % inset] = value
    r.append(check("  counts the coverage line, the tie and the border exactly",
                   W.read_occupancy(Image.frombytes("L", (W.GRID, W.GRID),
                                                    bytes(pixels))),
                   [".....WWW", ".....BBB", ".....BBB", "....WWWW",
                    "....BBBB", "WBBWBWBB", "BWBWB.BW", "...BWBW."]))

    # -- real screenshots -------------------------------------------------
    shots = sys.argv[1:] or [shot(n) for n in ("1", "2", "4", "5")]
    print()
    for path in shots:
        try:
            img = Image.open(path).convert("RGB")
        except Exception as exc:
            print("SKIP  %s (%s)" % (path, exc))
            continue
        rect = W.find_board(img)
        name = path.split("/")[-1].split("\\")[-1]
        clean = name.startswith(("1.", "5."))

        if not clean:
            # Screenshots 2 and 4 have the game-over dialog sitting over the
            # board. Refusing them is correct: by then the game is already
            # recorded, and inventing a position would be worse than nothing.
            if rect:
                x, y, size = rect
                occ = W.read_occupancy(img.crop((x, y, x + size, y + size)))
                tt = W.BoardTracker(directory=tempfile.mkdtemp())
                tt.feed(W.START_WHITE_VIEW)
                got = tt.feed(occ)
            else:
                got = None
            r.append(check("  invents nothing from the covered board in " + name,
                           got, None))
            continue

        if not rect:
            print("FAIL  found no board in " + name)
            r.append(False)
            continue
        x, y, size = rect
        print("      %s: board at (%d,%d) %dx%d" % (name, x, y, size, size))
        occ = W.read_occupancy(img.crop((x, y, x + size, y + size)))

        if name.startswith("1."):
            b = chess.Board()
            for san in ("d4", "e6", "e4", "c5"):
                b.push_san(san)
            expect = W.occupancy_of(b, False)
            label = "  reads the maximised window exactly"
        else:
            # Small browser window over a photo wallpaper, no move list on
            # screen at all, with a check marker and a move badge on the board.
            expect = ["W.....W.", ".......W", ".B......", ".W.W....",
                      "...W...B", "........", ".WWW.W.W", "........"]
            label = "  reads the small window over a wallpaper exactly"
        r.append(check(label, occ, expect))
        r.append(check("  grid check is confident on " + name,
                       W.grid_score(img, x, y, size) > 0.95, True))

    # -- speed -------------------------------------------------------------
    img = Image.open(shot("1")).convert("RGB")
    x, y, size = W.find_board(img)
    crop = img.crop((x, y, x + size, y + size))
    t0 = time.time()
    for _ in range(20):
        W.read_occupancy(crop)
    per = (time.time() - t0) / 20 * 1000
    print("\n      read_occupancy: %.0f ms per frame" % per)
    r.append(check("  fast enough to run live", per < 250, True))

    t0 = time.time()
    W.find_board(img)
    print("      find_board:     %.0f ms" % ((time.time() - t0) * 1000))

    print("\n%d/%d passed" % (sum(bool(x) for x in r), len(r)))
    return 0 if all(r) else 1


if __name__ == "__main__":
    sys.exit(main())
