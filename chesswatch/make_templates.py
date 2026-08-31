"""Regenerate pieces.png, the bundled piece templates.

The app normally re-learns the pieces from your own screen the moment it sees a
starting position. This sheet is only the bootstrap, used before that happens,
which is what lets it read a game already in progress.

Run:  python make_templates.py <screenshot.png> [FEN]

The screenshot must show a chess.com board whose position matches the FEN. With
no FEN it assumes the opening position.
"""

import sys

import chess
from PIL import Image

import watcher as W
from pieces import ORDER, TEMPLATE_PX, TEMPLATE_SHEET


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    img = Image.open(sys.argv[1]).convert("RGB")
    board = chess.Board(sys.argv[2]) if len(sys.argv) > 2 else chess.Board()

    rect = W.find_board(img)
    if not rect:
        print("No chess.com board found in that screenshot.")
        return 1
    x0, y0, size = rect
    step = size / 8.0
    print("board at (%d,%d) %dpx, squares %.1fpx" % (x0, y0, size, step))

    sheet = Image.new("RGB", (TEMPLATE_PX * len(ORDER), TEMPLATE_PX))
    missing = []
    for slot, symbol in enumerate(ORDER):
        square = next((s for s in chess.SQUARES
                       if board.piece_at(s) and board.piece_at(s).symbol() == symbol),
                      None)
        if square is None:
            missing.append(symbol)
            continue
        col = chess.square_file(square)
        row = 7 - chess.square_rank(square)
        crop = img.crop((int(x0 + col * step), int(y0 + row * step),
                         int(x0 + (col + 1) * step), int(y0 + (row + 1) * step)))
        sheet.paste(crop.resize((TEMPLATE_PX, TEMPLATE_PX), Image.LANCZOS),
                    (slot * TEMPLATE_PX, 0))

    if missing:
        print("That position is missing:", " ".join(missing))
        return 1
    sheet.save(TEMPLATE_SHEET)
    print("wrote", TEMPLATE_SHEET, sheet.size)

    return 0


if __name__ == "__main__":
    sys.exit(main())
