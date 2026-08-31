"""Prove the hologram renders on the board and cannot escape the app window.

A: render the HoloChess window itself -> the cyan arrow must be present.
B: grab the whole desktop while other apps sit in front -> there must be
   no cyan hologram pixel anywhere outside the HoloChess window.

B is the real check. The first version of this overlay was a top-level
always-on-top window and it painted its arrow straight onto a browser.
"""
import sys

import chess
from PyQt6.QtCore import Qt, QTimer, QEventLoop
from PyQt6.QtWidgets import QApplication

import holochess

HOLO_R, HOLO_G, HOLO_B = 0, 232, 255


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_for(pred, timeout_ms=25000):
    waited = 0
    while not pred() and waited < timeout_ms:
        wait(50)
        waited += 50
    return pred()


def count_holo_pixels(image, skip_rect=None):
    """Count pixels close to the hologram cyan, optionally ignoring a rect."""
    hits = 0
    width, height = image.width(), image.height()
    for y in range(0, height, 2):
        for x in range(0, width, 2):
            if skip_rect is not None and skip_rect.contains(x, y):
                continue
            colour = image.pixelColor(x, y)
            # the hologram body is (0,232,255) blended over the squares, which
            # stays far more saturated than any normal UI teal
            if (colour.red() < 90 and colour.green() > 195
                    and colour.blue() > 230):
                hits += 1
    return hits


def main():
    app = QApplication(sys.argv)
    win = holochess.MainWindow()
    win.show()
    wait(600)
    wait_for(lambda: win.engine_name != "Stockfish")

    win.colour_box.setCurrentText("White")
    win.elo_slider.setValue(1800)
    win.new_game()
    wait(300)

    board = win.board_widget
    for uci in ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5"):
        move = chess.Move.from_uci(uci)
        if move in board.board.legal_moves:
            board.board.push(move)
            board.last_move = move
    board.update()
    win._rebuild_move_list()
    wait(200)

    win.request_hint()
    got = wait_for(lambda: win.overlay._move is not None)
    wait(700)
    app.processEvents()

    results = []

    def check(label, ok, detail=""):
        results.append((label, ok))
        print("[%s] %s %s" % ("PASS" if ok else "FAIL", label, detail))

    check("hint produced", got, "-> %s" % (win.overlay._move,))

    # structural: the overlay must not be an independent top-level window
    flags = win.overlay.windowFlags()
    check("overlay is a child widget, not a window",
          not win.overlay.isWindow(),
          "-> parent=%s" % type(win.overlay.parent()).__name__)
    check("overlay is not always-on-top",
          not bool(flags & Qt.WindowType.WindowStaysOnTopHint))

    # A: render the app window itself
    shot_a = win.grab().toImage()
    shot_a.save("shot_a_app.png", "PNG")
    holo_in_app = count_holo_pixels(shot_a)
    check("arrow renders inside the app", holo_in_app > 200,
          "-> %d cyan px" % holo_in_app)

    # B: the whole desktop, with whatever else is in front
    desktop = app.primaryScreen().grabWindow(0).toImage()
    desktop.save("shot_b_desktop.png", "PNG")
    win_rect = win.frameGeometry()
    leaked = count_holo_pixels(desktop, skip_rect=win_rect)
    check("no hologram pixels outside the app window", leaked < 40,
          "-> %d cyan px outside %s" % (leaked, win_rect.getRect()))

    failed = [label for label, ok in results if not ok]
    print("\n%d checks failed" % len(failed))
    if failed:
        print("failed:", ", ".join(failed))

    win.close()
    app.quit()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
