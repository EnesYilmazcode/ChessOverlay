"""Post-move ratings. Independent of coaching; never publishes a best move.

These are ChessWatch estimates, not Chess.com's proprietary classifications.
Every job retains the entire recorded prefix so takebacks cannot reuse ratings
from a different line. Only the recorder thread changes or saves Game objects.
"""
import math
import queue
import threading

import chess
import chess.engine

import coach

BADGES = {
    "brilliant": ("!!", "Brilliant", "#26c6da", 3),
    "great": ("!", "Great", "#6d9eeb", 1),
    "best": ("*", "Best", "#95bb4a", None),
    "excellent": ("+", "Excellent", "#95bb4a", None),
    "good": ("·", "Good", "#a9b98c", None),
    "inaccuracy": ("?!", "Inaccuracy", "#f0c15a", 6),
    "mistake": ("?", "Mistake", "#ed9850", 2),
    "blunder": ("??", "Blunder", "#ef6464", 4),
    "forced": ("=", "Forced", "#a8a29e", None),
}
VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
          chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}


def expectation(score):
    if score.is_mate():
        return 1.0 if score.mate() > 0 else 0.0
    return 1 / (1 + math.exp(-max(-10000, min(10000, score.score())) / 250))


def offered_pieces(board):
    """Non-pawns an opponent could take for a material profit right now.

Use legal captures, including pins and checks; allow an immediate recapture.
This is deliberately a conservative sacrifice heuristic, not a full SEE.
"""
    result = set()
    for capture in list(board.legal_moves):
        victim = board.piece_at(capture.to_square)
        if not victim or victim.piece_type in (chess.PAWN, chess.KING):
            continue
        attacker = board.piece_at(capture.from_square)
        board.push(capture)
        recaptured = any(m.to_square == capture.to_square
                         for m in board.legal_moves)
        board.pop()
        cost = VALUES[attacker.piece_type] if recaptured else 0
        if VALUES[victim.piece_type] - cost >= 2:
            result.add(capture.to_square)
    return result


def sacrifice(board, move):
    if move.promotion or board.is_check():
        return False
    previous = board.copy(stack=False)
    previous.turn = not board.turn
    previous.ep_square = None
    old = offered_pieces(previous)
    after = board.copy(stack=False)
    after.push(move)
    # A piece already hanging before the move is not a new sacrifice.
    mapped = {move.to_square if sq == move.from_square else sq for sq in old}
    return bool(offered_pieces(after) - mapped)


def classify(board, move, best, played, second=None, is_best=False):
    """Scores are always from the mover's POV, including for black."""
    loss = max(0, best.score(mate_score=100000) - played.score(mate_score=100000))
    drop = max(0, expectation(best) - expectation(played))
    if board.legal_moves.count() == 1:
        kind = "forced"
    elif drop >= .20:
        kind = "blunder"
    elif drop >= .10:
        kind = "mistake"
    elif drop >= .05:
        kind = "inaccuracy"
    elif is_best and loss <= 20:
        material = sum(VALUES[p.piece_type] * (1 if p.color == board.turn else -1)
                       for p in board.piece_map().values())
        if (not best.is_mate() and not played.is_mate()
                and played.score() >= 0 and material <= 2 and sacrifice(board, move)):
            kind = "brilliant"
        elif (second is not None and expectation(played) >= .4
              and expectation(played) - expectation(second) >= .15):
            kind = "great"
        else:
            kind = "best"
    elif loss <= 25:
        kind = "excellent"
    else:
        kind = "good"
    return {"category": kind, "symbol": BADGES[kind][0],
            "label": BADGES[kind][1], "loss_cp": loss,
            "expected_score_loss": round(drop, 4),
            "source": "ChessWatch / Stockfish estimate"}


def analyse(engine, board, move):
    # Same fixed depth for alternatives and the played move. A time cap can
    # produce uneven evidence: refuse it rather than inventing a rating.
    limit = chess.engine.Limit(depth=16, time=1.0)
    top = engine.analyse(board, limit, multipv=min(2, board.legal_moves.count()))
    if not top or not top[0].get("pv"):
        raise ValueError("No engine line")
    best_move = top[0]["pv"][0]
    played = next((line for line in top if line.get("pv", [None])[0] == move), None)
    if played is None:
        played = engine.analyse(board, limit, root_moves=[move])
    evidence = top + [played]
    if any(line.get("depth", 0) < 16 or "score" not in line for line in evidence):
        raise ValueError("Search incomplete; move unrated")
    score = lambda line: line["score"].pov(board.turn)
    result = classify(board, move, score(top[0]), score(played),
                      score(top[1]) if len(top) > 1 else None, move == best_move)
    result["depth"] = 16
    return result


class Reviewer(threading.Thread):
    daemon = True

    def __init__(self, path):
        super().__init__()
        self.path = path
        self.jobs = queue.Queue()
        self.out = queue.Queue()
        self.stopping = threading.Event()

    def stop(self):
        self.stopping.set()
        self.jobs.put(None)

    def run(self):
        try:
            with chess.engine.SimpleEngine.popen_uci(str(self.path), **coach.POPEN_FLAGS) as engine:
                engine.configure({"Threads": 1, "Hash": 32})
                while not self.stopping.is_set():
                    job = self.jobs.get()
                    if job is None:
                        break
                    game, prefix, board, move = job
                    try:
                        rating = analyse(engine, board, move)
                    except (ValueError, chess.engine.EngineError) as exc:
                        rating = {"category": "unrated", "symbol": "—",
                                  "label": "Unrated", "reason": str(exc)}
                    self.out.put((game, prefix, rating))
        except Exception as exc:
            self.out.put((None, None, str(exc)))
