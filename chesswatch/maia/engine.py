"""Maia-3 5M as a program of its own: which move a player of a given rating plays.

This folder is AGPL-3.0, see LICENSE beside this file, and it is a separate
program from the MIT code around it. ChessWatch starts it as a child process
and talks to it over a pipe, the same way it talks to Stockfish. Nothing outside
this folder imports anything in it.

Run:  python engine.py

It reads one JSON object per line and writes one JSON line back for each:

    {"fen": "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
     "elo": 800}
    {"moves": [["f1c4", 0.29], ["d2d4", 0.18], ...]}

Every legal move is in the answer with its probability, most likely first, and
the probabilities add up to 1. A finished game has no moves. A request may carry
an "id", which is copied onto its answer. A line that cannot be answered, for
any reason, gets {"error": "..."} and the program carries on with the next one.
If the model itself would not load, every answer is that error with
"fatal": true added, so whoever asked can tell a question it got wrong from a
program that will never answer one. It exits at the end of its input.

The rating is used for both players, which is the same thing maia3's own UCI
engine does with its Elo option. The model is given the current board in all
eight of its history slots, which is maia3's default when it is not told the
moves that led here.

Needs onnxruntime, numpy (which onnxruntime installs) and python-chess. Not
torch: maia3-5m.onnx was exported once from the published weights by
export_onnx.py, and README.md in this folder says how.
"""

import json
import os
import sys

import chess

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "maia3-5m.onnx")

HISTORY = 8          # positions the 5M model looks at
PLANES = 12          # six piece types, two colours
THREADS = 2          # it shares the machine with Stockfish and the reader


def all_moves():
    """The model's 4352 move names, in maia3.utils.get_all_possible_moves order:
    every from-square to every to-square, then the promotions off the 7th rank."""
    names = [chess.square_name(s) for s in range(64)]
    moves = [a + b for a in names for b in names]
    moves += ["%s7%s8%s" % (ff, ft, p)
              for ff in "abcdefgh" for ft in "abcdefgh" for p in "qrbn"]
    return moves


def mirror(uci):
    """A move with its ranks flipped, promotion letter kept (maia3.utils.mirror_move)."""
    return uci[0] + str(9 - int(uci[1])) + uci[2] + str(9 - int(uci[3])) + uci[4:]


class Model:
    """The network and the encoding around it. Built once, asked many times."""

    def __init__(self, path=MODEL, threads=THREADS):
        # Imported here rather than at the top, so that a machine without them
        # still gets an answer saying so instead of a pipe that closes.
        import numpy as np
        import onnxruntime as ort
        self.np = np
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(path, options,
                                            providers=["CPUExecutionProvider"])
        self.index = {move: i for i, move in enumerate(all_moves())}

    def tokens(self, board):
        """(1, 64, 97): the board eight times over, then an empty clock column.

        The side to move is always drawn as white, so a board with black to move
        is mirrored first: planes 0-5 are its own pawn to king and planes 6-11
        the opponent's (maia3.dataset.tokenize_board).
        """
        np = self.np
        if board.turn == chess.BLACK:
            board = board.mirror()
        one = np.zeros((64, PLANES), dtype=np.float32)
        for square, piece in board.piece_map().items():
            plane = piece.piece_type - 1 + (6 if piece.color == chess.BLACK else 0)
            one[square, plane] = 1.0
        clock = np.zeros((64, 1), dtype=np.float32)
        return np.concatenate([one] * HISTORY + [clock], axis=1)[None, :, :]

    def moves(self, board, elo):
        """Every legal move and how likely a player rated elo is to play it."""
        np = self.np
        if board.is_game_over():
            return []
        black = board.turn == chess.BLACK
        legal, rows = [], []
        for move in board.legal_moves:
            uci = move.uci()
            row = self.index.get(mirror(uci) if black else uci)
            if row is None:
                raise ValueError("a legal move the model has no name for: " + uci)
            legal.append(uci)
            rows.append(row)
        rating = np.array([int(elo)], dtype=np.int64)
        logits = self.session.run(["logits_move"], {
            "tokens": self.tokens(board),
            "self_elos": rating,
            "oppo_elos": rating,
        })[0][0]
        # Softmax over the legal moves only, which is what maia3 does by
        # masking the rest to minus infinity first.
        picked = logits[rows].astype(np.float64)
        picked -= picked.max()
        p = np.exp(picked)
        p /= p.sum()
        order = np.argsort(-p, kind="stable")
        return [[legal[i], float(p[i])] for i in order]


def answer(model, line, problem="the model did not load"):
    """The reply to one line of input, as a dict. model is None if it would not
    load, and problem is then why."""
    try:
        request = json.loads(line)
    except ValueError:
        return {"error": "not JSON"}
    if not isinstance(request, dict):
        return {"error": "not a JSON object"}
    reply = {"id": request["id"]} if "id" in request else {}
    fen, elo = request.get("fen"), request.get("elo")
    if not isinstance(fen, str):
        reply["error"] = "no fen"
        return reply
    if isinstance(elo, bool) or not isinstance(elo, (int, float)) or elo != elo:
        reply["error"] = "no elo"
        return reply
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        reply["error"] = "bad fen: %s" % exc
        return reply
    if not board.is_valid():
        reply["error"] = "not a position that can happen in a game"
        return reply
    if model is None:
        reply["error"] = problem
        reply["fatal"] = True
        return reply
    try:
        reply["moves"] = model.moves(board, elo)
    except Exception as exc:
        reply["error"] = "%s: %s" % (type(exc).__name__, exc)
    return reply


def main():
    model, problem = None, None
    try:
        model = Model()
    except Exception as exc:
        # Kept running rather than exiting, so whoever started it is told why
        # on the line they are waiting for instead of finding a closed pipe.
        problem = "the model did not load: %s: %s" % (type(exc).__name__, exc)
    out = sys.stdout
    for line in sys.stdin:
        out.write(json.dumps(answer(model, line, problem)) + "\n")
        out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
