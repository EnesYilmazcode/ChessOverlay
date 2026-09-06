"""Drive the highlight reader over whole games and count what it says.

This is the evidence behind the numbers in README.md for `last_mover`, kept
here so they can be re-run rather than taken on trust. It is not in the suite
and not in CI: 400 games took 300 seconds on the machine it was written on,
longer than every other suite put together, and the thing it measures does not
change move to move. selftest.py holds the cases that have to pass on every commit.

Every frame is a rendered board with the two squares of the move just played
lit in the colour chess.com paints, which is the picture the reader is for. It
opens no window and reads no screen.

Games end on the rules note_outcome ends them on, which is chess.com's three
fold and fifty move rather than python-chess's automatic five and seventy five.
Getting that wrong is what made the first run of this report a desync that was
really the harness playing on past a game the tracker had correctly closed.

Exits non-zero if the reader ever names the wrong colour. Naming nobody is
allowed and counted; naming the wrong side is the one thing it must not do.
"""

import argparse
import collections
import random
import sys
import tempfile
import time

import chess

import pieces as P
import watcher as W
from fakeboard import Renderer
from shots import shot


def refusal(tracker, occ, img):
    """Why last_mover said nothing, in the same order it decides."""
    if tracker.board is None:
        return "not locked on"
    if tracker.over:
        return "the game is closed"
    lit = W.highlight_squares(img, tracker._highlight_colours())
    if len(lit) != 2:
        return "%d lit squares" % len(lit)
    return "not exactly one lit square holds a piece"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--games", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20260905)
    ap.add_argument("--plies", type=int, default=200,
                    help="give up on a game that runs this long")
    args = ap.parse_args()

    rnd = random.Random(args.seed)
    render = Renderer(shot("1"), (225, 63, 824))
    reader = P.PieceReader()
    if not reader.ready:
        print("no piece templates, nothing to drive")
        return 1

    why = collections.Counter()
    frames = spoke = right = exact = wrong_position = 0
    started = time.time()

    for game in range(args.games):
        # Both ways up, since which way the board faces decides where every
        # square lands on screen and the reader has to survive either.
        flipped = bool(game % 2)
        tracker = W.BoardTracker(directory=tempfile.mkdtemp(), reader=reader)
        tracker.feed(W.START_BLACK_VIEW if flipped else W.START_WHITE_VIEW)
        shadow = chess.Board()
        played = []

        for _ in range(args.plies):
            if (shadow.is_game_over(claim_draw=False) or shadow.is_repetition(3)
                    or shadow.is_fifty_moves()):
                break
            move = rnd.choice(list(shadow.legal_moves))
            played.append(shadow.san(move))
            lit = (move.from_square, move.to_square)
            shadow.push(move)
            if not render.can_render(shadow):
                played.pop()             # the reference had no such piece to cut
                break

            img = render.render(shadow, flipped, lit=lit,
                                highlight=W.DEFAULT_HIGHLIGHT)
            occ = W.read_occupancy(img)
            tracker.feed(occ, img)
            frames += 1

            said = tracker.last_mover(occ, img)
            if said is None:
                why[refusal(tracker, occ, img)] += 1
            else:
                spoke += 1
                right += said == (not shadow.turn)
            if (tracker.board.board_fen() != shadow.board_fen()
                    or tracker.board.turn != shadow.turn):
                wrong_position += 1

        exact += bool(tracker.game and tracker.game.moves == played)

    print("games                              %d" % args.games)
    print("frames                             %d" % frames)
    print("games recorded move for move       %d" % exact)
    print("frames the tracker was out of step %d" % wrong_position)
    print("frames it named a mover            %d (%.2f%%)"
          % (spoke, 100.0 * spoke / max(frames, 1)))
    print("  of those, the right one          %d" % right)
    for reason, n in why.most_common():
        print("  said nothing: %-32s %d" % (reason, n))
    print("took %.0f s" % (time.time() - started))

    if right != spoke:
        print("\nFAIL  named the wrong colour on %d frames" % (spoke - right))
        return 1
    print("\nPASS  never named the wrong colour")
    return 0


if __name__ == "__main__":
    sys.exit(main())
