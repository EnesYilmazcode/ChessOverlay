"""Run with python -m unittest reviewtest. Includes the installed Stockfish."""
import io
import json
import tempfile
import time
import unittest
from unittest.mock import Mock

import chess
import chess.engine
import chess.pgn

import coach
import review
import watcher


class Ratings(unittest.TestCase):
    def setUp(self):
        self.board = chess.Board()
        self.move = self.board.parse_san("e4")

    def rating(self, best, played, **kw):
        return review.classify(self.board, self.move, chess.engine.Cp(best),
                               chess.engine.Cp(played), **kw)["category"]

    def test_loss_thresholds(self):
        for cp, expected in [(0, "excellent"), (-40, "good"),
                             (-60, "inaccuracy"), (-120, "mistake"),
                             (-300, "blunder")]:
            with self.subTest(cp=cp):
                self.assertEqual(self.rating(0, cp), expected)

    def test_best_and_great(self):
        self.assertEqual(self.rating(30, 30, is_best=True), "best")
        self.assertEqual(self.rating(0, 0, is_best=True,
                                    second=chess.engine.Cp(-250)), "great")

    def test_decided_position_not_blunder_for_cp_noise(self):
        self.assertEqual(self.rating(1500, 1000), "good")
        self.assertEqual(self.rating(-1000, -1500), "good")

    def test_mate_and_black_pov(self):
        self.assertEqual(review.classify(self.board, self.move, chess.engine.Cp(0),
                                         chess.engine.Mate(-2))["category"], "blunder")
        self.board.push_san("e4")
        self.move = self.board.parse_san("e5")
        self.assertEqual(self.rating(0, -300), "blunder")

    def test_forced(self):
        board = chess.Board("R6k/8/5K2/8/8/8/8/8 b - - 0 1")
        self.assertEqual(board.legal_moves.count(), 1)
        self.assertEqual(review.classify(board, next(iter(board.legal_moves)),
                                         chess.engine.Cp(-500), chess.engine.Cp(-500))
                         ["category"], "forced")

    def test_new_sacrifice(self):
        # Equal material; a bishop offered to a pawn with a recapture possible.
        board = chess.Board("6kr/8/3p4/8/8/2B5/8/3R2K1 w - - 0 1")
        move = board.parse_san("Be5")
        self.assertTrue(review.sacrifice(board, move))
        self.assertEqual(review.classify(board, move, chess.engine.Cp(40),
                                         chess.engine.Cp(40), is_best=True)
                         ["category"], "brilliant")
        self.assertEqual(review.classify(board, move, chess.engine.Cp(40),
                                         chess.engine.Cp(-300), is_best=False)
                         ["category"], "blunder")

    def test_quiet_move_not_brilliant(self):
        self.assertFalse(review.sacrifice(self.board, self.move))
        self.assertEqual(self.rating(20, 20, is_best=True), "best")

    def test_incomplete_search_refused(self):
        engine = Mock()
        engine.analyse.return_value = [{"pv": [self.move], "depth": 10,
                                        "score": chess.engine.PovScore(chess.engine.Cp(0), True)}]
        with self.assertRaises(ValueError):
            review.analyse(engine, self.board, self.move)

    def test_saved_and_takeback_safe(self):
        with tempfile.TemporaryDirectory() as folder:
            game = watcher.Game("white", folder)
            game.moves = ["e4", "e5", "Nf3"]
            rating = {"category": "blunder", "symbol": "??", "label": "Blunder"}
            game.reviews[("e4", "e5", "Nf3")] = rating
            path = game.save()
            with open(path[:-4] + ".json") as fh:
                self.assertEqual(json.load(fh)["moves"][2]["review"], rating)
            pgn = chess.pgn.read_game(io.StringIO(game.to_pgn()))
            node = pgn.end()
            self.assertIn(chess.pgn.NAG_BLUNDER, node.nags)
            self.assertNotIn("best move", node.comment)
            game.moves = ["e4", "e5", "Bc4"]
            self.assertIsNone(game.review_at(2))
            game.moves = ["e4", "e5", "Nf3"]
            self.assertEqual(game.review_at(2), rating)

    def test_joined_black_numbering(self):
        game = watcher.Game("black", start_fen="6k1/8/8/8/8/8/8/6K1 b - - 0 20")
        game.moves = ["Kf7"]
        game.reviews[("Kf7",)] = {"category": "good", "symbol": ".", "label": "Good"}
        self.assertEqual(game.rows(), [(20, None, "Kf7")])
        self.assertEqual(game.to_dict()["moves"][0]["review"]["category"], "good")

    @unittest.skipUnless(coach.find_engine(), "Stockfish not installed")
    def test_real_stockfish_blunder_and_mate(self):
        with chess.engine.SimpleEngine.popen_uci(str(coach.find_engine()), **coach.POPEN_FLAGS) as engine:
            board = chess.Board()
            for san in ["f3", "e5"]:
                board.push_san(san)
            self.assertEqual(review.analyse(engine, board, board.parse_san("g4"))
                             ["category"], "blunder")
            board.push_san("g4")
            result = review.analyse(engine, board, board.parse_san("Qh4#"))
            self.assertIn(result["category"], ("great", "best"))
            self.assertEqual(result["loss_cp"], 0)

    @unittest.skipUnless(coach.find_engine(), "Stockfish not installed")
    def test_recorder_reviews_and_saves_finished_game(self):
        import queue
        import chesswatch as app
        with tempfile.TemporaryDirectory() as folder:
            worker = app.Worker(None, queue.Queue(), folder, review_moves=True)
            worker.tracker._start(False)
            for san in ["f3", "e5", "g4", "Qh4#"]:
                worker.tracker._apply([worker.tracker.board.parse_san(san)])
            worker._review_moves()
            worker._review_moves()
            self.assertEqual(len(worker.review_requested), 4)
            try:
                deadline = time.monotonic() + 15
                while len(worker.tracker.game.reviews) < 4 and time.monotonic() < deadline:
                    time.sleep(.05)
                    worker._collect_reviews()
                game = worker.tracker.game
                self.assertEqual(len(game.reviews), 4)
                self.assertEqual(game.review_at(2)["category"], "blunder")
                with open(game.path[:-4] + ".json") as fh:
                    saved = json.load(fh)
                self.assertEqual(saved["result"], "0-1")
                self.assertEqual(saved["moves"][2]["review"]["category"], "blunder")
            finally:
                worker.reviewer.stop()
                worker.reviewer.join(timeout=3)

    def test_live_labels_and_symbols_without_advice(self):
        import tkinter as tk
        import chesswatch as app
        from unittest.mock import patch
        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(app.App, "_start"), patch.object(app.App, "_load_config", return_value={"coach": False}):
                ui = app.App(root)
            frame = dict(color="white", region=None, locked=True, result="*",
                         outcome=None, rows=[(1, "f3", "e5"), (2, "g4", None)],
                         ratings={0: None, 1: None, 2: {"category": "blunder",
                                  "symbol": "??", "label": "Blunder"}},
                         board="", fen=None, path=None)
            ui._render(frame)
            self.assertEqual(ui.lbl_status.cget("text"), "?? Blunder")
            self.assertIn("g4", ui.moves_box.get("1.0", "end"))
            self.assertIn("??", ui.moves_box.get("1.0", "end"))
            self.assertEqual(ui.lbl_coach.cget("text"), "")
            frame["ratings"][2] = None
            ui._render(frame)
            self.assertEqual(ui.lbl_status.cget("text"), "rating...")
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
