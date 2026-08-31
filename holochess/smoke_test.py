"""Headless smoke test: engine starts, plays a reply, and the hint arrow lands."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import chess
from PyQt6.QtCore import QTimer, QEventLoop
from PyQt6.QtWidgets import QApplication

import holochess

FAILURES = []


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print("[%s] %s %s" % (mark, label, detail))
    if not condition:
        FAILURES.append(label)


def wait_for(predicate, timeout_ms=25000):
    loop = QEventLoop()
    elapsed = {"ms": 0}

    def poll():
        elapsed["ms"] += 50
        if predicate() or elapsed["ms"] >= timeout_ms:
            timer.stop()
            loop.quit()

    timer = QTimer()
    timer.setInterval(50)
    timer.timeout.connect(poll)
    timer.start()
    loop.exec()
    return predicate()


def main():
    app = QApplication(sys.argv)
    win = holochess.MainWindow()
    win.resize(1040, 720)
    win.show()

    ready = wait_for(lambda: "ready" in win.status_label.text()
                             or "Best" in win.status_label.text()
                             or win.engine_name != "Stockfish")
    check("engine starts", ready, "-> %r" % win.status_label.text())
    if not ready:
        return

    board = win.board_widget

    # force a known setup so the test is deterministic
    win.colour_box.setCurrentText("White")
    win.elo_slider.setValue(1600)
    win.new_game()
    check("new game is startpos", board.board.fen() == chess.STARTING_FEN)
    check("human plays white", board.human_colour == chess.WHITE)
    check("board not flipped", board.flipped is False)

    # geometry mapping must round-trip
    sq = board.sq_rect(chess.E4)
    check("square mapping round-trips",
          board.square_at(sq.center()) == chess.E4,
          "-> %s" % chess.square_name(board.square_at(sq.center())))

    # human move via the same path a click takes
    board.selected = chess.E2
    board.targets = {m.to_square: m for m in board.board.legal_moves
                     if m.from_square == chess.E2}
    board._push(chess.Move(chess.E2, chess.E4))
    check("human move applied", board.board.piece_at(chess.E4) is not None)
    check("move list has the move", win.move_list.count() == 1,
          "-> %d rows" % win.move_list.count())

    replied = wait_for(lambda: board.board.turn == chess.WHITE
                               and len(board.board.move_stack) == 2)
    check("engine replied", replied,
          "-> %s" % (board.board.peek().uci() if board.board.move_stack else "none"))
    check("board unlocked after reply", board.locked is False)
    check("move list paired the reply", win.move_list.count() == 1,
          "-> %r" % (win.move_list.item(0).text() if win.move_list.count() else ""))

    # hint
    win.overlay._move = None
    win.request_hint()
    got_hint = wait_for(lambda: win.overlay._move is not None)
    check("hint arrow produced", got_hint, "-> %s" % (win.overlay._move,))
    if got_hint:
        from_sq, to_sq = win.overlay._move
        legal = chess.Move(from_sq, to_sq) in board.board.legal_moves or any(
            m.from_square == from_sq and m.to_square == to_sq
            for m in board.board.legal_moves)
        check("hint is a legal move for the human", legal,
              "-> %s%s" % (chess.square_name(from_sq), chess.square_name(to_sq)))
        check("hint moves a white piece",
              board.board.piece_at(from_sq) is not None
              and board.board.piece_at(from_sq).color == chess.WHITE)
        check("overlay sized to the board",
              win.overlay.width() > 0 and win.overlay.width() == win.overlay.height(),
              "-> %dx%d" % (win.overlay.width(), win.overlay.height()))

    # overlay orientation must follow the flip
    centre_before = win.overlay._centre(chess.A1, 100.0)
    win.flip_board()
    win.overlay._flipped = board.flipped
    centre_after = win.overlay._centre(chess.A1, 100.0)
    check("flip moves the arrow anchor", centre_before != centre_after,
          "-> %s then %s" % (centre_before, centre_after))
    win.flip_board()

    # undo takes back both plies
    plies = len(board.board.move_stack)
    win.undo()
    check("undo removes both plies", len(board.board.move_stack) == plies - 2,
          "-> %d left" % len(board.board.move_stack))
    check("undo is still the human's turn", board.board.turn == board.human_colour)

    win.close()
    print("\n%d checks failed" % len(FAILURES))
    if FAILURES:
        print("failed:", ", ".join(FAILURES))
    app.quit()
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
