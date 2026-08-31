#!/usr/bin/env python3
"""
HoloChess - play Stockfish locally, with a click-through hologram arrow
that shows the best move on demand.

Everything runs on this machine. Nothing talks to any chess website.
"""
from __future__ import annotations

import math
import os
import random
import shutil
import sys
from pathlib import Path

import chess
import chess.engine

from PyQt6.QtCore import (Qt, QObject, QThread, QTimer, QRectF, QPointF,
                          pyqtSignal, pyqtSlot)
from PyQt6.QtGui import (QPainter, QPainterPath, QPolygonF, QColor, QPen,
                         QBrush, QFont, QFontDatabase, QShortcut, QKeySequence,
                         QRadialGradient)
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QSlider,
                             QComboBox, QListWidget, QDialog, QCheckBox,
                             QMessageBox, QFrame)

APP_DIR = Path(__file__).resolve().parent


def _find_engine():
    """Stockfish, from the bundled copy, $STOCKFISH_PATH, or PATH.

    The binary is not in the repository: it is 114 MB, above GitHub's file
    limit, and GPL licensed separately. Drop one in engine/stockfish/ or point
    STOCKFISH_PATH at your own.
    """
    env = os.environ.get("STOCKFISH_PATH")
    if env and Path(env).exists():
        return Path(env)
    local = APP_DIR / "engine" / "stockfish"
    if local.is_dir():
        for f in sorted(local.iterdir()):
            if f.is_file() and f.stem.startswith("stockfish"):
                if os.name != "nt" or f.suffix.lower() == ".exe":
                    return f
    found = shutil.which("stockfish")
    if found:
        return Path(found)
    return local / "stockfish"


ENGINE_PATH = _find_engine()

LIGHT_SQ  = QColor("#EEEED2")
DARK_SQ   = QColor("#769656")
LAST_MOVE = QColor(246, 246, 105, 130)
SEL_SQ    = QColor(255, 213,  79, 165)
CHECK_SQ  = QColor(214,  60,  50, 195)
HOLO      = QColor(0, 232, 255)

GLYPH = {
    chess.KING:   "♚", chess.QUEEN:  "♛", chess.ROOK: "♜",
    chess.BISHOP: "♝", chess.KNIGHT: "♞", chess.PAWN: "♟",
}

_PIECE_FONT = None


def piece_font_family() -> str:
    """Pick an installed font that actually has the solid chess glyphs."""
    global _PIECE_FONT
    if _PIECE_FONT is None:
        installed = set(QFontDatabase.families())
        for name in ("Segoe UI Symbol", "Arial Unicode MS", "DejaVu Sans",
                     "FreeSerif", "Segoe UI Emoji"):
            if name in installed:
                _PIECE_FONT = name
                break
        else:
            _PIECE_FONT = "Segoe UI Symbol"
    return _PIECE_FONT


def glyph_path(piece_type: int, rect: QRectF, scale: float = 0.80) -> QPainterPath:
    """Outline of a chess glyph, centred inside rect."""
    font = QFont(piece_font_family())
    font.setPixelSize(max(8, int(rect.height() * scale)))
    path = QPainterPath()
    path.addText(0.0, 0.0, font, GLYPH[piece_type])
    bounds = path.boundingRect()
    path.translate(rect.center().x() - bounds.center().x(),
                   rect.center().y() - bounds.center().y())
    return path


def arrow_polygon(tail: QPointF, tip: QPointF, sq: float) -> QPolygonF:
    """A single straight arrow from tail to tip, sized relative to a square."""
    dx, dy = tip.x() - tail.x(), tip.y() - tail.y()
    length = math.hypot(dx, dy)
    if length < 1.0:
        return QPolygonF()
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux

    shaft = sq * 0.20
    head_w = sq * 0.52
    head_l = min(sq * 0.46, length * 0.55)
    base = QPointF(tip.x() - ux * head_l, tip.y() - uy * head_l)

    def off(point: QPointF, amount: float) -> QPointF:
        return QPointF(point.x() + nx * amount, point.y() + ny * amount)

    return QPolygonF([
        off(tail,  shaft / 2), off(base,  shaft / 2), off(base,  head_w / 2),
        tip,
        off(base, -head_w / 2), off(base, -shaft / 2), off(tail, -shaft / 2),
    ])


class EngineWorker(QObject):
    """Owns the Stockfish process. Lives on its own thread."""

    ready  = pyqtSignal(str)
    failed = pyqtSignal(str)
    result = pyqtSignal(str, str, str)   # tag, fen searched, uci move

    def __init__(self, path: Path):
        super().__init__()
        self._path = str(path)
        self._engine = None
        self._limited = -1

    @pyqtSlot()
    def start(self):
        try:
            self._engine = chess.engine.SimpleEngine.popen_uci(self._path)
            threads = max(1, (os.cpu_count() or 4) - 4)
            self._engine.configure({"Threads": threads, "Hash": 2048})
            self.ready.emit(self._engine.id.get("name", "Stockfish"))
        except Exception as exc:
            self.failed.emit("Could not start Stockfish: %s" % exc)

    def _set_strength(self, elo: int):
        """elo of 0 means full strength. Only reconfigure when it changes."""
        if elo == self._limited:
            return
        if elo:
            self._engine.configure({"UCI_LimitStrength": True, "UCI_Elo": elo})
        else:
            self._engine.configure({"UCI_LimitStrength": False})
        self._limited = elo

    @pyqtSlot(str, str, float, int)
    def think(self, tag: str, fen: str, movetime: float, elo: int):
        if self._engine is None:
            return
        try:
            self._set_strength(elo)
            board = chess.Board(fen)
            played = self._engine.play(board, chess.engine.Limit(time=movetime))
            if played.move is not None:
                self.result.emit(tag, fen, played.move.uci())
        except Exception as exc:
            self.failed.emit("Engine error: %s" % exc)

    @pyqtSlot()
    def shutdown(self):
        try:
            if self._engine is not None:
                self._engine.quit()
        except Exception:
            pass
        self._engine = None


class HologramOverlay(QWidget):
    """Translucent click-through layer that draws the arrow above the board.

    This is deliberately a CHILD widget of the app window, not a top-level
    always-on-top window. A top-level always-on-top overlay floats above every
    other application on the desktop, which would turn this into a move overlay
    for anything on screen. As a child it is clipped to HoloChess and can only
    ever sit over HoloChess's own board.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._move = None
        self._flipped = False
        self._phase = 0.0
        self._suppressed = False

        self._pulse = QTimer(self)
        self._pulse.setInterval(33)
        self._pulse.timeout.connect(self._tick)

    def _tick(self):
        self._phase = (self._phase + 0.055) % (2 * math.pi)
        self.update()

    def _refresh(self):
        if self._move is not None and not self._suppressed:
            if not self._pulse.isActive():
                self._pulse.start()
            self.show()
            self.raise_()
            self.update()
        else:
            self._pulse.stop()
            self.hide()

    def set_suppressed(self, suppressed: bool):
        if suppressed != self._suppressed:
            self._suppressed = suppressed
            self._refresh()

    def show_move(self, from_sq: int, to_sq: int, flipped: bool):
        self._move = (from_sq, to_sq)
        self._flipped = flipped
        self._refresh()

    def clear(self):
        self._move = None
        self._refresh()

    def _centre(self, square: int, sq: float) -> QPointF:
        file_idx, rank_idx = chess.square_file(square), chess.square_rank(square)
        col = (7 - file_idx) if self._flipped else file_idx
        row = rank_idx if self._flipped else (7 - rank_idx)
        return QPointF((col + 0.5) * sq, (row + 0.5) * sq)

    def paintEvent(self, _event):
        if self._move is None:
            return
        sq = self.width() / 8.0
        tail = self._centre(self._move[0], sq)
        tip  = self._centre(self._move[1], sq)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        breathe = 0.5 + 0.5 * math.sin(self._phase)

        # halo on the piece you should move
        radius = sq * (0.50 + 0.06 * breathe)
        halo = QRadialGradient(tail, radius)
        halo.setColorAt(0.0, QColor(HOLO.red(), HOLO.green(), HOLO.blue(), 0))
        halo.setColorAt(0.7, QColor(HOLO.red(), HOLO.green(), HOLO.blue(),
                                    int(40 + 30 * breathe)))
        halo.setColorAt(1.0, QColor(HOLO.red(), HOLO.green(), HOLO.blue(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(halo))
        painter.drawEllipse(tail, radius, radius)

        poly = arrow_polygon(tail, tip, sq)
        if poly.isEmpty():
            painter.end()
            return

        # glow, drawn as widening translucent outlines
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for width, alpha in ((sq * 0.30, 16), (sq * 0.18, 30), (sq * 0.09, 60)):
            pen = QPen(QColor(HOLO.red(), HOLO.green(), HOLO.blue(),
                              int(alpha * (0.75 + 0.25 * breathe))))
            pen.setWidthF(width)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawPolygon(poly)

        # solid body
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(HOLO.red(), HOLO.green(), HOLO.blue(),
                                int(170 + 40 * breathe)))
        painter.drawPolygon(poly)

        # bright edge
        edge = QPen(QColor(205, 255, 255, int(200 + 45 * breathe)))
        edge.setWidthF(max(1.5, sq * 0.028))
        edge.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(edge)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(poly)

        # ring on the destination square
        ring = QPen(QColor(HOLO.red(), HOLO.green(), HOLO.blue(),
                           int(90 + 80 * breathe)))
        ring.setWidthF(max(2.0, sq * 0.045))
        painter.setPen(ring)
        painter.drawEllipse(tip, sq * 0.42, sq * 0.42)

        painter.end()


class PromotionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Promote to")
        self.setModal(True)
        self.choice = chess.QUEEN
        layout = QHBoxLayout(self)
        layout.setSpacing(6)
        for piece_type in (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT):
            button = QPushButton(GLYPH[piece_type])
            font = QFont(piece_font_family())
            font.setPixelSize(40)
            button.setFont(font)
            button.setFixedSize(62, 62)
            button.clicked.connect(lambda _=False, pt=piece_type: self._pick(pt))
            layout.addWidget(button)

    def _pick(self, piece_type: int):
        self.choice = piece_type
        self.accept()


class BoardWidget(QWidget):
    humanMoved = pyqtSignal(str, int, bool)   # san, fullmove number, side that moved
    geometryChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.board = chess.Board()
        self.flipped = False
        self.human_colour = chess.WHITE
        self.locked = False
        self.selected = None
        self.targets = {}
        self.last_move = None
        self.setMinimumSize(420, 420)

    def geom(self):
        side = min(self.width(), self.height())
        sq = side / 8.0
        return int((self.width() - sq * 8) / 2), int((self.height() - sq * 8) / 2), sq

    def sq_rect(self, square: int) -> QRectF:
        ox, oy, sq = self.geom()
        file_idx, rank_idx = chess.square_file(square), chess.square_rank(square)
        col = (7 - file_idx) if self.flipped else file_idx
        row = rank_idx if self.flipped else (7 - rank_idx)
        return QRectF(ox + col * sq, oy + row * sq, sq, sq)

    def square_at(self, pos: QPointF):
        ox, oy, sq = self.geom()
        col = int((pos.x() - ox) // sq)
        row = int((pos.y() - oy) // sq)
        if not (0 <= col < 8 and 0 <= row < 8):
            return None
        file_idx = (7 - col) if self.flipped else col
        rank_idx = row if self.flipped else (7 - row)
        return chess.square(file_idx, rank_idx)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.geometryChanged.emit()

    def moveEvent(self, event):
        super().moveEvent(event)
        self.geometryChanged.emit()

    def mousePressEvent(self, event):
        if self.locked or self.board.is_game_over():
            return
        if event.button() != Qt.MouseButton.LeftButton:
            self.selected, self.targets = None, {}
            self.update()
            return
        square = self.square_at(event.position())
        if square is None:
            return

        if self.selected is not None and square in self.targets:
            self._push(self.targets[square])
            return

        piece = self.board.piece_at(square)
        if piece is not None and piece.color == self.board.turn == self.human_colour:
            self.selected = square
            self.targets = {m.to_square: m for m in self.board.legal_moves
                            if m.from_square == square}
        else:
            self.selected, self.targets = None, {}
        self.update()

    def _push(self, move: chess.Move):
        if move.promotion is not None:
            dialog = PromotionDialog(self)
            dialog.exec()
            move = chess.Move(move.from_square, move.to_square,
                              promotion=dialog.choice)
        san = self.board.san(move)          # must be read before the push
        fullmove, mover = self.board.fullmove_number, self.board.turn
        self.board.push(move)
        self.last_move = move
        self.selected, self.targets = None, {}
        self.update()
        self.humanMoved.emit(san, fullmove, mover)

    def apply_engine_move(self, move: chess.Move):
        self.board.push(move)
        self.last_move = move
        self.selected, self.targets = None, {}
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#262421"))

        ox, oy, sq = self.geom()
        painter.setPen(Qt.PenStyle.NoPen)

        check_sq = self.board.king(self.board.turn) if self.board.is_check() else None

        for square in chess.SQUARES:
            rect = self.sq_rect(square)
            light = (chess.square_file(square) + chess.square_rank(square)) % 2 == 1
            painter.fillRect(rect, LIGHT_SQ if light else DARK_SQ)

            if self.last_move is not None and square in (self.last_move.from_square,
                                                         self.last_move.to_square):
                painter.fillRect(rect, LAST_MOVE)
            if square == self.selected:
                painter.fillRect(rect, SEL_SQ)
            if square == check_sq:
                glow = QRadialGradient(rect.center(), sq * 0.6)
                glow.setColorAt(0.0, CHECK_SQ)
                glow.setColorAt(1.0, QColor(214, 60, 50, 0))
                painter.setBrush(QBrush(glow))
                painter.drawRect(rect)
                painter.setBrush(Qt.BrushStyle.NoBrush)

        # file and rank labels
        label_font = QFont("Segoe UI")
        label_font.setPixelSize(max(9, int(sq * 0.17)))
        label_font.setBold(True)
        painter.setFont(label_font)
        for i in range(8):
            file_idx = (7 - i) if self.flipped else i
            rank_idx = i if self.flipped else (7 - i)
            dark_text = (i % 2 == 0)
            painter.setPen(DARK_SQ if dark_text else LIGHT_SQ)
            painter.drawText(
                QRectF(ox + i * sq + sq * 0.06, oy + 8 * sq - sq * 0.26, sq, sq * 0.24),
                Qt.AlignmentFlag.AlignLeft, chess.FILE_NAMES[file_idx])
            painter.setPen(LIGHT_SQ if dark_text else DARK_SQ)
            painter.drawText(
                QRectF(ox + 8 * sq - sq * 0.28, oy + i * sq + sq * 0.04,
                       sq * 0.22, sq * 0.3),
                Qt.AlignmentFlag.AlignRight, chess.RANK_NAMES[rank_idx])

        # pieces
        for square in chess.SQUARES:
            piece = self.board.piece_at(square)
            if piece is None:
                continue
            path = glyph_path(piece.piece_type, self.sq_rect(square))

            shadow = QPainterPath(path)
            shadow.translate(sq * 0.025, sq * 0.035)
            painter.fillPath(shadow, QColor(0, 0, 0, 70))

            if piece.color == chess.WHITE:
                fill, line = QColor("#FFFFFF"), QColor("#1A1A1A")
            else:
                fill, line = QColor("#2B2B2B"), QColor("#F2F2F2")
            painter.fillPath(path, fill)
            pen = QPen(line)
            pen.setWidthF(max(1.0, sq * 0.030))
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.strokePath(path, pen)

        # legal move markers for the selected piece
        painter.setPen(Qt.PenStyle.NoPen)
        for square, move in self.targets.items():
            rect = self.sq_rect(square)
            if self.board.piece_at(square) is not None or self.board.is_en_passant(move):
                ring = QPen(QColor(20, 20, 20, 110))
                ring.setWidthF(sq * 0.085)
                painter.setPen(ring)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(rect.center(), sq * 0.42, sq * 0.42)
                painter.setPen(Qt.PenStyle.NoPen)
            else:
                painter.setBrush(QColor(20, 20, 20, 90))
                painter.drawEllipse(rect.center(), sq * 0.145, sq * 0.145)

        painter.end()


SIDE_STYLE = """
QWidget#side { background: #302E2B; }
QLabel { color: #D8D6D2; font-family: 'Segoe UI'; font-size: 12px; }
QLabel#title { color: #FFFFFF; font-size: 19px; font-weight: 600; }
QLabel#status { color: #9FD17A; font-size: 12px; }
QPushButton {
    background: #3E3C39; color: #EDEDED; border: none; border-radius: 5px;
    padding: 8px 10px; font-family: 'Segoe UI'; font-size: 12px;
}
QPushButton:hover { background: #4C4945; }
QPushButton:pressed { background: #2E2C2A; }
QPushButton#hint {
    background: #0E5A66; color: #B9F6FF; font-weight: 600;
}
QPushButton#hint:hover { background: #12727F; }
QComboBox {
    background: #3E3C39; color: #EDEDED; border: none; border-radius: 5px;
    padding: 6px 8px; font-size: 12px;
}
QComboBox QAbstractItemView {
    background: #3E3C39; color: #EDEDED; selection-background-color: #12727F;
}
QListWidget {
    background: #272522; color: #C9C7C3; border: none; border-radius: 5px;
    font-family: 'Consolas'; font-size: 12px; padding: 4px;
}
QCheckBox { color: #D8D6D2; font-family: 'Segoe UI'; font-size: 12px; }
QSlider::groove:horizontal { height: 4px; background: #4C4945; border-radius: 2px; }
QSlider::handle:horizontal {
    background: #00E8FF; width: 14px; margin: -6px 0; border-radius: 7px;
}
"""


class MainWindow(QMainWindow):
    requestThink = pyqtSignal(str, str, float, int)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("HoloChess")
        self.resize(1040, 720)
        self.setStyleSheet("QMainWindow { background: #262421; }")

        self.board_widget = BoardWidget()
        self.overlay = HologramOverlay(self.board_widget)
        self.engine_name = "Stockfish"
        self.pending_hint = False

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)
        layout.addWidget(self.board_widget, 1)
        layout.addWidget(self._build_side_panel())
        self.setCentralWidget(root)

        self.board_widget.humanMoved.connect(self.on_human_moved)
        self.board_widget.geometryChanged.connect(self.sync_overlay)

        for key, slot in (("H", self.request_hint), ("F", self.flip_board),
                          ("Ctrl+Z", self.undo), ("Ctrl+N", self.new_game),
                          ("Esc", self.overlay.clear)):
            QShortcut(QKeySequence(key), self, activated=slot)

        # keep the hologram glued to the board even when the window moves
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(120)
        self._sync_timer.timeout.connect(self.sync_overlay)
        self._sync_timer.start()

        self._start_engine()

    # ------------------------------------------------------------- side panel
    def _build_side_panel(self) -> QWidget:
        side = QWidget()
        side.setObjectName("side")
        side.setStyleSheet(SIDE_STYLE)
        side.setFixedWidth(258)

        column = QVBoxLayout(side)
        column.setContentsMargins(16, 16, 16, 16)
        column.setSpacing(9)

        title = QLabel("HoloChess")
        title.setObjectName("title")
        column.addWidget(title)

        self.status_label = QLabel("Starting engine...")
        self.status_label.setObjectName("status")
        self.status_label.setWordWrap(True)
        column.addWidget(self.status_label)

        column.addSpacing(6)

        self.hint_button = QPushButton("Show best move   (H)")
        self.hint_button.setObjectName("hint")
        self.hint_button.clicked.connect(self.request_hint)
        column.addWidget(self.hint_button)

        self.auto_hint = QCheckBox("Keep it on every turn")
        self.auto_hint.stateChanged.connect(self._auto_hint_changed)
        column.addWidget(self.auto_hint)

        column.addSpacing(10)
        column.addWidget(self._divider())
        column.addSpacing(4)

        column.addWidget(QLabel("Opponent strength"))
        strength_row = QHBoxLayout()
        self.elo_slider = QSlider(Qt.Orientation.Horizontal)
        self.elo_slider.setRange(1320, 3190)
        self.elo_slider.setSingleStep(10)
        self.elo_slider.setValue(1600)
        self.elo_slider.valueChanged.connect(self._elo_changed)
        self.elo_label = QLabel("1600")
        self.elo_label.setFixedWidth(38)
        strength_row.addWidget(self.elo_slider)
        strength_row.addWidget(self.elo_label)
        column.addLayout(strength_row)

        column.addSpacing(8)
        column.addWidget(QLabel("You play"))
        self.colour_box = QComboBox()
        self.colour_box.addItems(["White", "Black", "Random"])
        column.addWidget(self.colour_box)

        new_button = QPushButton("New game   (Ctrl+N)")
        new_button.clicked.connect(self.new_game)
        column.addWidget(new_button)

        row = QHBoxLayout()
        undo_button = QPushButton("Undo")
        undo_button.clicked.connect(self.undo)
        flip_button = QPushButton("Flip")
        flip_button.clicked.connect(self.flip_board)
        row.addWidget(undo_button)
        row.addWidget(flip_button)
        column.addLayout(row)

        column.addSpacing(10)
        column.addWidget(self._divider())
        column.addSpacing(4)

        column.addWidget(QLabel("Moves"))
        self.move_list = QListWidget()
        column.addWidget(self.move_list, 1)

        return side

    @staticmethod
    def _divider() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background: #45423E; max-height: 1px; border: none;")
        return line

    # ----------------------------------------------------------------- engine
    def _start_engine(self):
        if not ENGINE_PATH.exists():
            self._set_status(
                "Stockfish not found. Put it in engine/stockfish/ or set "
                "STOCKFISH_PATH. Looked at %s" % ENGINE_PATH)
            return
        self.engine_thread = QThread(self)
        self.worker = EngineWorker(ENGINE_PATH)
        self.worker.moveToThread(self.engine_thread)
        self.engine_thread.started.connect(self.worker.start)
        self.worker.ready.connect(self.on_engine_ready)
        self.worker.failed.connect(self._set_status)
        self.worker.result.connect(self.on_engine_result)
        self.requestThink.connect(self.worker.think)
        self.engine_thread.start()

    @pyqtSlot(str)
    def on_engine_ready(self, name: str):
        self.engine_name = name
        self._set_status("%s ready. Your move." % name)
        self.new_game()

    def _ask_engine(self, tag: str, movetime: float, elo: int):
        self.requestThink.emit(tag, self.board_widget.board.fen(), movetime, elo)

    @pyqtSlot(str, str, str)
    def on_engine_result(self, tag: str, fen: str, uci: str):
        board = self.board_widget.board
        if fen != board.fen():
            return  # position moved on while we were thinking, drop it
        move = chess.Move.from_uci(uci)

        if tag == "hint":
            self.pending_hint = False
            self.sync_overlay()
            self.overlay.show_move(move.from_square, move.to_square,
                                   self.board_widget.flipped)
            self.hint_button.setEnabled(True)
            self._set_status("Best move: %s" % board.san(move))
            return

        self._append_move(board.san(move), board.fullmove_number, board.turn)
        self.board_widget.apply_engine_move(move)
        self.board_widget.locked = False
        self._after_position_change()

    # ------------------------------------------------------------- game flow
    def new_game(self):
        choice = self.colour_box.currentText()
        if choice == "Random":
            choice = random.choice(["White", "Black"])
        human_white = (choice == "White")

        self.board_widget.board = chess.Board()
        self.board_widget.human_colour = chess.WHITE if human_white else chess.BLACK
        self.board_widget.flipped = not human_white
        self.board_widget.last_move = None
        self.board_widget.selected = None
        self.board_widget.targets = {}
        self.board_widget.locked = False
        self.board_widget.update()
        self.move_list.clear()
        self.overlay.clear()
        self._set_status("You are %s. Your move." % choice.lower())
        self._maybe_engine_turn()

    @pyqtSlot(str, int, bool)
    def on_human_moved(self, san: str, fullmove: int, mover: bool):
        self.overlay.clear()
        self._append_move(san, fullmove, mover)
        self._after_position_change()

    def _after_position_change(self):
        board = self.board_widget.board
        if board.is_game_over():
            self._announce_result()
            return
        if board.turn != self.board_widget.human_colour:
            self._maybe_engine_turn()
        else:
            self._set_status("Your move.")
            if self.auto_hint.isChecked():
                self.request_hint()

    def _maybe_engine_turn(self):
        board = self.board_widget.board
        if board.is_game_over():
            self._announce_result()
            return
        if board.turn == self.board_widget.human_colour:
            return
        self.board_widget.locked = True
        self._set_status("%s is thinking..." % self.engine_name)
        self._ask_engine("engine", 0.35, self.elo_slider.value())

    def request_hint(self):
        board = self.board_widget.board
        if board.is_game_over() or self.pending_hint:
            return
        if board.turn != self.board_widget.human_colour:
            return
        self.pending_hint = True
        self.hint_button.setEnabled(False)
        self._set_status("Finding the best move...")
        self._ask_engine("hint", 0.30, 0)   # 0 elo means full strength

    def _elo_changed(self, value: int):
        self.elo_label.setText(str(value))

    def _auto_hint_changed(self):
        if self.auto_hint.isChecked():
            self.request_hint()
        else:
            self.overlay.clear()

    def undo(self):
        board = self.board_widget.board
        if self.board_widget.locked or not board.move_stack:
            return
        board.pop()
        if board.move_stack and board.turn != self.board_widget.human_colour:
            board.pop()
        self.board_widget.last_move = board.peek() if board.move_stack else None
        self.board_widget.selected = None
        self.board_widget.targets = {}
        self.board_widget.update()
        self.overlay.clear()
        self._rebuild_move_list()
        self._set_status("Took it back. Your move.")

    def flip_board(self):
        self.board_widget.flipped = not self.board_widget.flipped
        self.board_widget.update()
        self.sync_overlay()
        self.overlay.update()

    def _announce_result(self):
        board = self.board_widget.board
        outcome = board.outcome()
        if outcome is None:
            return
        if outcome.winner is None:
            text = "Draw by %s." % outcome.termination.name.lower().replace("_", " ")
        elif outcome.winner == self.board_widget.human_colour:
            text = "You win by %s." % outcome.termination.name.lower().replace("_", " ")
        else:
            text = "You lose by %s." % outcome.termination.name.lower().replace("_", " ")
        self._set_status(text)
        self.overlay.clear()
        QMessageBox.information(self, "Game over", text)

    # ------------------------------------------------------------- move list
    def _append_move(self, san: str, fullmove: int, turn: bool):
        if turn == chess.WHITE:
            self.move_list.addItem("%3d. %s" % (fullmove, san))
        else:
            row = self.move_list.count() - 1
            if row >= 0:
                item = self.move_list.item(row)
                item.setText("%s   %s" % (item.text(), san))
            else:
                self.move_list.addItem("%3d. ...  %s" % (fullmove, san))
        self.move_list.scrollToBottom()

    def _rebuild_move_list(self):
        self.move_list.clear()
        replay = chess.Board()
        for move in self.board_widget.board.move_stack:
            san = replay.san(move)
            self._append_move(san, replay.fullmove_number, replay.turn)
            replay.push(move)

    # ---------------------------------------------------------------- overlay
    def sync_overlay(self):
        """Keep the hologram layer exactly over the 8x8 area of the board."""
        ox, oy, sq = self.board_widget.geom()
        side = int(sq * 8)
        self.overlay.setGeometry(ox, oy, side, side)

    def _set_status(self, text: str):
        self.status_label.setText(text)

    def closeEvent(self, event):
        try:
            self.worker.shutdown()
            self.engine_thread.quit()
            self.engine_thread.wait(3000)
        except Exception:
            pass
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("HoloChess")
    window = MainWindow()
    window.show()
    window.sync_overlay()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
