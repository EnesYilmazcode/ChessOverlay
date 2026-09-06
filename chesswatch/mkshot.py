"""Regenerate docs/arrow.png: a real middlegame, not move two, and without the
stray rank labels the old renderer baked onto all 64 squares."""
import chess
from PIL import Image
import watcher as W
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

SIDE, PAD = 580, 10
board = r.render(game).resize((SIDE, SIDE), Image.LANCZOS)
board = OT.paint_arrow(board, (0, 0, SIDE, SIDE), chess.Move.from_uci("b1d2"))

out = Image.new("RGB", (SIDE + PAD * 2, SIDE + PAD * 2), (38, 36, 33))
out.paste(board, (PAD, PAD))
out.save("arrow_new.png")
print("wrote arrow_new.png", out.size, "| position after 22 plies")
print("legal:", game.is_valid(), "| pieces:", len(game.piece_map()))
