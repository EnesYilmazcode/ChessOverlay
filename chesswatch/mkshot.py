"""Regenerate docs/arrow.png: a real middlegame, not move two, and without the
stray rank labels the old renderer baked onto all 64 squares.

Both arrows come from the real coach rather than being typed in here, so the
picture is a record of what the program actually said about that position and
not an illustration of what it might say. Without Stockfish it draws the move
to play alone and says so.
"""
import queue
import time

import chess
from PIL import Image
import watcher as W
import coach as CO
from fakeboard import Renderer
import overlaytest as OT

# 1.png is the position after 1.e4 c5 2.d4 e6, so the renderer has to be told
# that rather than assuming the opening, or every sprite comes from the wrong
# square.
ref = chess.Board()
for san in ("e4", "c5", "d4", "e6"):
    ref.push_san(san)

img = Image.open("testdata/1.png").convert("RGB")
rect = W.find_board(img)
r = Renderer("testdata/1.png", rect, board=ref)

# Ruy Lopez, Chigorin. Both sides castled, both knights out, tension in the
# centre: a position that looks like a game rather than an opening.
game = chess.Board()
for san in ("e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6", "O-O", "Be7",
            "Re1", "b5", "Bb3", "O-O", "c3", "d6", "h3", "Na5", "Bc2", "c5",
            "d4", "Qc7"):
    game.push_san(san)


def asked(fen, seconds=40):
    """What the coach says about this position: the move to play, and the move
    to avoid if it finds one worth showing."""
    path = CO.find_engine()
    if path is None:
        return None, None
    c = CO.Coach(path, think_seconds=2.5)
    c.start()
    c.ask(fen)
    play = avoid = None
    end = time.time() + seconds
    while time.time() < end and avoid is None:
        try:
            kind, payload = c.out.get(timeout=0.2)
        except queue.Empty:
            continue
        if kind == "advice" and payload.get("final"):
            play = payload
        elif kind == "mistake":
            avoid = payload
    c.stop()
    return play, avoid


play, avoid = asked(game.fen())
best = chess.Move.from_uci(play["uci"]) if play else chess.Move.from_uci("b1d2")
bad = chess.Move.from_uci(avoid["uci"]) if avoid else None

SIDE, PAD = 580, 10
board = r.render(game).resize((SIDE, SIDE), Image.LANCZOS)
board = OT.paint_arrow(board, (0, 0, SIDE, SIDE), best, bad=bad)

out = Image.new("RGB", (SIDE + PAD * 2, SIDE + PAD * 2), (38, 36, 33))
out.paste(board, (PAD, PAD))
out.save("arrow_new.png")
print("wrote arrow_new.png", out.size, "| position after 22 plies")
print("play :", game.san(best), play["text"] if play else "(no engine)")
print("avoid:", (game.san(bad) + "  " + avoid["worse"]) if bad
      else "(nothing worth warning about)")
print("legal:", game.is_valid(), "| pieces:", len(game.piece_map()))
