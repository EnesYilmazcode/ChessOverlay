"""Core logic for ChessWatch.

Reads the chess.com BOARD off the screen, not the move list.

Two readers work together. The fast one classifies each square as white piece /
black piece / empty and infers the move from the rules, which is cheap enough to
run several times a second. The slower one in pieces.py identifies which piece
is actually on each square, and is used to check the fast reader, to catch up
when moves are missed, and to read a position cold.

No GUI in here on purpose, so it can be tested headlessly.
"""

import os
import json
import datetime

import chess
import chess.pgn
from PIL import Image, ImageChops

APP_DIR = os.path.dirname(os.path.abspath(__file__))
GAMES_DIR = os.path.join(APP_DIR, "games")

# chess.com's default green board, measured off real screenshots.
LIGHT_SQUARE = (235, 236, 208)
DARK_SQUARE = (115, 149, 82)
SQUARE_TOL = 18

# Square classification. Piece fills sit far outside the square colours, so the
# margin is enormous: measured on real screenshots, empty squares score exactly
# 0.0000 even under the last-move highlight, the coordinate labels and the check
# marker, while the faintest piece scores 0.15. The low MIN_COVERAGE is what
# lets a small browser window still work, down to roughly a 130px board.
BRIGHT = 244
DARK = 70
MIN_COVERAGE = 0.015

# Board is subsampled to this before classifying. NEAREST, so pixel values are
# preserved exactly rather than averaged away.
GRID = 8 * 32


# ------------------------------------------------------- finding the board

def _colour_mask(img, target, tol):
    mask = None
    for channel, want in zip(img.split(), target):
        hit = channel.point(lambda v, w=want: 255 if abs(v - w) <= tol else 0)
        mask = hit if mask is None else ImageChops.multiply(mask, hit)
    return mask


def _runs(profile, threshold, min_length, max_gap=0):
    """Every stretch of the profile above threshold, tolerating short gaps.

    The gap tolerance matters: unless a board's pixel size divides by eight, the
    browser antialiases one column at each of the seven internal square seams,
    and those columns are not board colour. Without tolerance the board is cut
    into eight fragments and never found at all.
    """
    out, start, gap = [], None, 0
    for i, value in enumerate(list(profile) + [0] * (max_gap + 1)):
        if value >= threshold:
            if start is None:
                start = i
            gap = 0
        elif start is not None:
            gap += 1
            if gap > max_gap:
                end = i - gap + 1
                if end - start >= min_length:
                    out.append((start, end))
                start, gap = None, 0
    return out


def grid_score(img, x0, y0, size):
    """How much a candidate rectangle actually looks like a chessboard.

    Samples two opposite corners of each square, which chess.com's pieces never
    reach, and checks the light/dark squares alternate. Returns 0.0 to 1.0. The
    last-move highlight and the check marker recolour a square or two, so a real
    board scores high but not perfect.

    Two corners rather than one on purpose. A rectangle shifted by part of a
    square keeps every single-corner sample inside its own square and still
    scores a perfect 1.00, while reading the pieces off it gives nonsense. The
    second sample lands in the neighbouring square and the score collapses.
    """
    step = size / 8.0
    patch = max(2, int(step * 0.12))
    checks = 0
    agree = 0
    for r in range(8):
        for c in range(8):
            for inset in (0.05, 0.83):
                px = int(x0 + c * step + step * inset)
                py = int(y0 + r * step + step * inset)
                crop = img.crop((px, py, px + patch, py + patch))
                data = list(crop.getdata())
                n = len(data)
                avg = [sum(d[i] for d in data) / n for i in range(3)]
                to_light = sum((a - b) ** 2 for a, b in zip(avg, LIGHT_SQUARE))
                to_dark = sum((a - b) ** 2 for a, b in zip(avg, DARK_SQUARE))
                # Top-left square of the board is light in both orientations.
                if (to_light < to_dark) == ((r + c) % 2 == 0):
                    agree += 1
                checks += 1
    return agree / float(checks)


BLOB_SCALE = 8


def _blobs(bits, w, h, min_side):
    """Bounding boxes of connected runs of True in a coarse bitmap."""
    seen = bytearray(w * h)
    out = []
    for start in range(w * h):
        if bits[start] and not seen[start]:
            stack = [start]
            seen[start] = 1
            x0 = x1 = start % w
            y0 = y1 = start // w
            while stack:
                i = stack.pop()
                x, y = i % w, i // w
                x0, x1 = min(x0, x), max(x1, x)
                y0, y1 = min(y0, y), max(y1, y)
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if 0 <= nx < w and 0 <= ny < h:
                        j = ny * w + nx
                        if bits[j] and not seen[j]:
                            seen[j] = 1
                            stack.append(j)
            if x1 - x0 >= min_side and y1 - y0 >= min_side:
                out.append((x0, y0, x1 + 1, y1 + 1))
    return out


def _refine_edges(mask, box):
    """Tighten a coarse bounding box to the exact extent of board colour."""
    x0, y0, x1, y1 = box
    crop = mask.crop(box)
    w, h = crop.size
    if w < 8 or h < 8:
        return None
    cols = list(crop.resize((w, 1), Image.BOX).getdata())
    rows = list(crop.resize((1, h), Image.BOX).getdata())
    # Low threshold on purpose: the crop is already just the board, and a line
    # of squares crowded with pieces still carries plenty of board colour.
    cx = _runs(cols, max(max(cols) * 0.15, 20), 8, max_gap=3)
    cy = _runs(rows, max(max(rows) * 0.15, 20), 8, max_gap=3)
    if not cx or not cy:
        return None
    cx = max(cx, key=lambda r: r[1] - r[0])
    cy = max(cy, key=lambda r: r[1] - r[0])
    return x0 + cx[0], y0 + cy[0], x0 + cx[1], y0 + cy[1]


def find_board(img, min_grid=0.78):
    """Locate a chess.com board in a screenshot.

    Returns (left, top, size) in image coordinates, or None.

    Board-coloured pixels are grouped into connected blobs, so two boards on
    screen stay separate instead of merging into one oversized rectangle, and
    then every candidate still has to prove it is a real checkerboard. Green UI
    panels and wallpaper get thrown out by that second step.
    """
    rgb = img.convert("RGB")
    mask = ImageChops.lighter(_colour_mask(rgb, LIGHT_SQUARE, SQUARE_TOL),
                              _colour_mask(rgb, DARK_SQUARE, SQUARE_TOL))
    w, h = mask.size
    sw, sh = max(1, w // BLOB_SCALE), max(1, h // BLOB_SCALE)
    small = mask.resize((sw, sh), Image.BOX)
    bits = [v >= 40 for v in small.getdata()]
    if not any(bits):
        return None

    best = None                                # (score, size, x0, y0)
    for box in _blobs(bits, sw, sh, 120 // BLOB_SCALE):
        # Pad by one coarse cell: the blob grid rounds inward and would
        # otherwise clip the last few pixels off the board's edges.
        coarse = (max(0, box[0] * BLOB_SCALE - BLOB_SCALE),
                  max(0, box[1] * BLOB_SCALE - BLOB_SCALE),
                  min(w, box[2] * BLOB_SCALE + BLOB_SCALE),
                  min(h, box[3] * BLOB_SCALE + BLOB_SCALE))
        exact = _refine_edges(mask, coarse)
        if not exact:
            continue
        x0, y0, x1, y1 = exact
        wide, tall = x1 - x0, y1 - y0
        if wide < 120 or tall < 120:
            continue

        if abs(wide - tall) <= max(wide, tall) * 0.12:
            corners = [(x0, y0)]
            sizes = {wide, tall, (wide + tall) // 2}
        else:
            # Not square, so something the same colour is stuck to one side.
            # Try the largest square in each corner of the blob.
            side = min(wide, tall)
            corners = [(x0, y0), (x1 - side, y0), (x0, y1 - side),
                       (x1 - side, y1 - side)]
            sizes = {side}

        for cx, cy in corners:
            for size in sizes:
                score = grid_score(rgb, cx, cy, size)
                if score >= min_grid and (best is None or
                                          (score, size) > (best[0], best[1])):
                    best = (score, size, cx, cy)
    if not best:
        return None
    return best[2], best[3], best[1]


# ------------------------------------------------------- reading the board

# One byte per grey level, tagging it 1 bright, 2 dark or 0 neither, so both
# tallies come off the bytes in one C pass. Tags are counted, never averaged: an
# 8 bit BOX resize of the same window looks equivalent and is not, because
# 255/324 is under one unit per pixel, so two different counts land on the same
# byte and a bright square reads as a tie, which scores B.
PIXEL_TAG = bytes(1 if v > BRIGHT else (2 if v < DARK else 0) for v in range(256))


def read_occupancy(board_img):
    """Classify all 64 squares. Returns 8 strings of W/B/. , top screen row
    first, each string running left to right across the screen.

    Only the middle 56% of each square is counted. The border is where the
    coordinate labels, the move badge and the neighbouring square's antialiased
    seam live, and letting those vote would put pieces on empty squares.

    That window is one bytes.count rather than 324 pixel reads. A row of squares
    cropped to its sampled pixel rows and transposed is a band 18 bytes wide, so
    each board column becomes one 18 byte row and a square's 18 columns become a
    single unbroken 324 byte run.
    """
    # Greyscale after the downscale, not before. NEAREST picks whole source
    # pixels and the conversion is per pixel, so the two commute exactly, and
    # this converts 65536 pixels instead of the whole board.
    small = board_img.resize((GRID, GRID), Image.NEAREST).convert("L")
    step = GRID // 8
    margin = int(step * 0.22)
    inset = step - 2 * margin
    area = inset * inset

    rows = []
    for r in range(8):
        top = r * step + margin
        band = small.crop((0, top, GRID, top + inset)).transpose(
            Image.TRANSPOSE).tobytes().translate(PIXEL_TAG)
        line = []
        for c in range(8):
            at = (c * step + margin) * inset
            end = at + area
            bf = band.count(1, at, end) / area
            df = band.count(2, at, end) / area
            if bf < MIN_COVERAGE and df < MIN_COVERAGE:
                line.append(".")
            else:
                line.append("W" if bf > df else "B")
        rows.append("".join(line))
    return rows


def occupancy_of(board, flipped=False):
    """The same 8-string map, but derived from a known position. flipped means
    black is at the bottom of the screen, which is how it looks when you play
    black."""
    ranks = range(8) if flipped else range(7, -1, -1)
    rows = []
    for rank in ranks:
        files = range(7, -1, -1) if flipped else range(8)
        line = ""
        for file in files:
            piece = board.piece_at(chess.square(file, rank))
            line += "." if piece is None else ("W" if piece.color else "B")
        rows.append(line)
    return rows


def grid_of(board, flipped=False):
    """8 rows of piece letters as they appear on screen."""
    ranks = range(8) if flipped else range(7, -1, -1)
    out = []
    for rank in ranks:
        files = range(7, -1, -1) if flipped else range(8)
        out.append([(board.piece_at(chess.square(f, rank)).symbol()
                     if board.piece_at(chess.square(f, rank)) else ".")
                    for f in files])
    return out


def board_from_grid(rows, flipped=False, turn=chess.WHITE):
    """Build a position from letters read off the screen.

    Castling rights are granted only where a king and rook are both still home,
    which is the most a picture can tell you. Returns None if the letters are
    not a position the rules allow, which is what rules out a wrong board
    orientation or the wrong side to move.
    """
    if any("?" in row for row in rows):
        return None
    board = chess.Board(None)
    ranks = range(8) if flipped else range(7, -1, -1)
    for r, rank in enumerate(ranks):
        files = range(7, -1, -1) if flipped else range(8)
        for c, file in enumerate(files):
            symbol = rows[r][c]
            if symbol != ".":
                board.set_piece_at(chess.square(file, rank),
                                   chess.Piece.from_symbol(symbol))
    board.turn = turn

    rights = ""
    for king_sq, rook_sq, letter in ((chess.E1, chess.H1, "K"),
                                     (chess.E1, chess.A1, "Q"),
                                     (chess.E8, chess.H8, "k"),
                                     (chess.E8, chess.A8, "q")):
        king = "K" if letter.isupper() else "k"
        rook = "R" if letter.isupper() else "r"
        if (board.piece_at(king_sq) == chess.Piece.from_symbol(king)
                and board.piece_at(rook_sq) == chess.Piece.from_symbol(rook)):
            rights += letter
    board.set_castling_fen(rights or "-")

    return board if board.is_valid() else None


START_WHITE_VIEW = occupancy_of(chess.Board(), False)
START_BLACK_VIEW = occupancy_of(chess.Board(), True)


# ------------------------------------------------------------------ game

class Game:
    def __init__(self, my_color=None, directory=GAMES_DIR, start_fen=None):
        self.started = datetime.datetime.now()
        self.moves = []
        self.my_color = my_color
        self.directory = directory
        self.start_fen = start_fen
        self.joined_late = start_fen is not None
        self.result = "*"
        self.termination = ""
        self.path = None
        self._base = None
        self._committed = []       # the longest run ever written out

        board = chess.Board(start_fen) if start_fen else chess.Board()
        self.start_ply = (board.fullmove_number - 1) * 2 + (0 if board.turn else 1)

    @property
    def stem(self):
        return self.started.strftime("%Y-%m-%d_%H%M%S")

    def board_at_start(self):
        return chess.Board(self.start_fen) if self.start_fen else chess.Board()

    def rows(self):
        """[(move_number, white_san_or_None, black_san_or_None), ...]."""
        out = []
        ply = self.start_ply
        for san in self.moves:
            number = ply // 2 + 1
            if ply % 2 == 0:
                out.append([number, san, None])
            elif out and out[-1][0] == number and out[-1][2] is None:
                out[-1][2] = san
            else:
                out.append([number, None, san])
            ply += 1
        return [tuple(r) for r in out]

    def note_outcome(self, board):
        """Read the result straight off the position. Resignations, timeouts and
        draws by agreement leave no trace on the board, so those stay unfinished.

        Threefold and the fifty move rule are asked for by name: python-chess
        only reports the automatic five-fold and seventy-five move versions, but
        chess.com ends the game at three and fifty, so the board goes quiet
        while those two are still unrecorded.
        """
        outcome = board.outcome(claim_draw=False)
        if outcome is not None:
            self.result = outcome.result()
            self.termination = outcome.termination.name.lower().replace("_", " ")
        elif board.is_repetition(3):
            self.result, self.termination = "1/2-1/2", "repetition"
        elif board.is_fifty_moves():
            self.result, self.termination = "1/2-1/2", "fifty move rule"

    @property
    def won(self):
        if self.result == "*" or not self.my_color:
            return None
        if self.result == "1/2-1/2":
            return "draw"
        return "won" if self.result == ("1-0" if self.my_color == "white" else "0-1") \
            else "lost"

    def to_pgn(self):
        g = chess.pgn.Game()
        board = self.board_at_start()
        if self.start_fen:
            g.setup(board)
        g.headers["Event"] = "chess.com"
        g.headers["Site"] = "chess.com"
        g.headers["Date"] = self.started.strftime("%Y.%m.%d")
        g.headers["White"] = "Me" if self.my_color == "white" else "Opponent"
        g.headers["Black"] = "Opponent" if self.my_color == "white" else "Me"
        g.headers["Result"] = self.result
        if self.termination:
            g.headers["Termination"] = self.termination
        if self.joined_late:
            g.headers["Annotator"] = "ChessWatch (joined this game in progress)"
        node = g
        for san in self.moves:
            move = board.parse_san(san)
            node = node.add_variation(move)
            board.push(move)
        return str(g)

    def to_dict(self):
        return {
            "started": self.started.isoformat(timespec="seconds"),
            "my_color": self.my_color,
            "result": self.result,
            "termination": self.termination,
            "outcome": self.won,
            "joined_in_progress": self.joined_late,
            "numbering": ("counted from where watching started, not chess.com's"
                          if self.joined_late else "from the first move"),
            "start_fen": self.start_fen,
            "move_count": len(self.moves),
            "moves": [
                {
                    "ply": self.start_ply + i,
                    "number": (self.start_ply + i) // 2 + 1,
                    "side": "white" if (self.start_ply + i) % 2 == 0 else "black",
                    "san": san,
                    "by": ("me" if ((self.start_ply + i) % 2 == 0)
                           == (self.my_color == "white") else "opponent")
                          if self.my_color else "unknown",
                }
                for i, san in enumerate(self.moves)
            ],
        }

    def save(self, directory=None):
        """Write PGN + JSON. Same filenames every time, so this is a safe autosave."""
        if not self.moves:
            return None
        # Never shorten a game already on disk. A resignation or a timeout
        # leaves no trace on the board, so the tracker cannot know the game is
        # over, and clicking back through chess.com's Game Review looks exactly
        # like a takeback. Rewinding in memory is harmless; overwriting the
        # finished file with a fragment is not. A real takeback diverges rather
        # than truncating, and that still gets written.
        if (len(self.moves) < len(self._committed)
                and self._committed[:len(self.moves)] == self.moves):
            return self.path

        directory = directory or self.directory
        os.makedirs(directory, exist_ok=True)

        # Claim a free name once, then keep writing to it. Two games can start
        # inside the same second, a rematch especially, and without this the
        # second one would quietly overwrite the first.
        if self._base is None:
            base = os.path.join(directory, self.stem)
            n = 2
            while os.path.exists(base + ".pgn"):
                base = os.path.join(directory, "%s-%d" % (self.stem, n))
                n += 1
            self._base = base
        base = self._base
        for path, data in ((base + ".pgn", self.to_pgn()),
                           (base + ".json", json.dumps(self.to_dict(), indent=2))):
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(data)
            os.replace(tmp, path)
        self.path = base + ".pgn"
        self._committed = list(self.moves)
        return self.path


# ---------------------------------------------------------------- tracker

def _ordered_moves(board):
    """Legal moves, queen promotions first. Two promotions of the same pawn look
    identical on screen, and chess.com auto-queens by default."""
    return sorted(board.legal_moves,
                  key=lambda m: 0 if m.promotion in (None, chess.QUEEN) else 1)


class BoardTracker:
    """Folds each frame of screen occupancy into a running game.

    feed() returns None, "moves", "newgame", or "finished".
    check() is the slower piece-level pass. Pass a pieces.PieceReader to enable
    it; without one the tracker still works, it just cannot verify itself or
    join a game already in progress.
    """

    def __init__(self, directory=GAMES_DIR, reader=None):
        self.directory = directory
        self.reader = reader
        self.board = None          # None until we lock onto a position
        self.flipped = False
        self.game = None
        self.finished = []
        self.last_check = ""
        self.preferred_flipped = None   # set when you tell it which colour you are
        self._last_unmatched = None
        self._pending = None       # a position seen but not yet explained

    @property
    def locked_on(self):
        return self.board is not None

    @property
    def over(self):
        """A finished game is read-only. chess.com drops you into Game Review
        the moment a game ends, and clicking back through it puts an earlier
        position on screen; without this that reads as a takeback and destroys
        the finished record, on disk as well as in memory."""
        return self.game is not None and self.game.result != "*"

    @property
    def stuck(self):
        """True when the last frame showed a position the fast reader could not
        reach from the game so far. That is the moment the piece checker is
        worth running, rather than a few seconds later."""
        return self._last_unmatched is not None

    # -- the fast per-frame path -------------------------------------
    def feed(self, occ, board_img=None):
        if occ == START_WHITE_VIEW or occ == START_BLACK_VIEW:
            flipped = occ == START_BLACK_VIEW
            if (self.board is not None and not self.game.moves
                    and not self.game.joined_late and self.flipped == flipped):
                return None                      # still sitting on move zero
            # A game can walk back into the starting position: 1.Nf3 Nf6 2.Ng1
            # Ng8 is exactly how a repetition draw gets forced. Continuing the
            # game we are already following beats splitting it in two.
            if (self.board is not None and self.game.moves and not self.over
                    and self.flipped == flipped):
                found = self._search_occupancy(occ)
                if found:
                    self._last_unmatched = None
                    return self._apply(found, board_img)
            return self._start(flipped)

        if self.board is None:
            return None                          # not locked on yet

        if self.over:
            return None                          # finished; only a new game matters

        if occ == occupancy_of(self.board, self.flipped):
            return None                          # nothing moved

        if occ == occupancy_of(self.board, not self.flipped):
            # The board was turned round, which chess.com does on one keystroke.
            # Same position, same game, drawn the other way up.
            self.flipped = not self.flipped
            self._last_unmatched = None
            return None

        if occ == self._last_unmatched:
            return None                          # same unreadable frame as before

        found = self._search_occupancy(occ) or self._try_en_passant(occ)
        if not found:
            self._last_unmatched = occ           # animation, dialog, dragged piece
            return None

        self._last_unmatched = None
        return self._apply(found, board_img)

    def _apply(self, moves, board_img=None):
        for move in moves:
            move = self._confirm_promotion(move, board_img)
            self.game.moves.append(self.board.san(move))
            self.board.push(move)
        self.game.note_outcome(self.board)
        return "finished" if self.game.result != "*" else "moves"

    def _en_passant_variants(self):
        """Copies of the position carrying an en passant right.

        A picture cannot show one. So a position rebuilt from pixels always
        loses it, and the very next move being an en passant capture then looks
        impossible and strands the game. Only worth trying on the first move
        after a rebuild, because python-chess tracks the right correctly once we
        are pushing moves ourselves.
        """
        board = self.board
        if board.move_stack:
            return []
        them = not board.turn
        if board.turn == chess.WHITE:
            home, live, behind = 6, 4, 5
        else:
            home, live, behind = 1, 3, 2

        out = []
        for file in range(8):
            if board.piece_at(chess.square(file, live)) != chess.Piece(chess.PAWN, them):
                continue
            if (board.piece_at(chess.square(file, behind))
                    or board.piece_at(chess.square(file, home))):
                continue          # something is there, so no double push happened
            if not any(board.piece_at(chess.square(n, live))
                       == chess.Piece(chess.PAWN, board.turn)
                       for n in (file - 1, file + 1) if 0 <= n <= 7):
                continue          # nothing of ours could capture anyway
            cand = board.copy(stack=False)
            cand.ep_square = chess.square(file, behind)
            if cand.is_valid():
                out.append(cand)
        return out

    def _try_en_passant(self, occ):
        """Retry the search allowing an en passant right the picture could not
        show. An en passant capture rearranges three particular squares that no
        ordinary move or pair of moves reproduces, so this only ever matches
        when the capture really happened."""
        original = self.board
        for cand in self._en_passant_variants():
            self.board = cand
            found = self._search_occupancy(occ)
            if found:
                if self.game is not None and not self.game.moves                         and self.game.start_fen:
                    self.game.start_fen = cand.fen()
                return found
            self.board = original
        return None

    def _in_flight_risk(self, move):
        """Could this move be a piece drawn part way through a longer one?

        chess.com slides a piece to its destination, and part way through it
        sits centred on a square in between. If that square is itself a legal
        destination for the same piece, the screen shows a legal move that
        never happened: e2-e3 during e2-e4, Rd3 during Rh3-a3, Kf1 during
        castling. Only moves that sit on someone else's line can be faked this
        way, and only those need waiting out.
        """
        for other in self.board.legal_moves:
            if (other.from_square == move.from_square
                    and other.to_square != move.to_square
                    and (chess.between(move.from_square, other.to_square)
                         & chess.BB_SQUARES[move.to_square])):
                return True
        return False

    def could_be_mid_move(self, occ):
        """True if believing this reading right now might record a piece that
        is still in flight. The caller should let it hold still for longer."""
        if self.board is None or self.over:
            return False
        if occ == occupancy_of(self.board, self.flipped):
            return False
        moves = self._search_occupancy(occ)
        if not moves:
            return False
        risky, pushed = False, 0
        for move in moves:
            if self._in_flight_risk(move):
                risky = True
                break
            self.board.push(move)
            pushed += 1
        for _ in range(pushed):
            self.board.pop()
        return risky

    def _square_on_screen(self, square):
        """Where a board square appears on screen, as (row, col) from the top
        left, which flips when you are playing black."""
        file, rank = chess.square_file(square), chess.square_rank(square)
        if self.flipped:
            return rank, 7 - file
        return 7 - rank, file

    def _confirm_promotion(self, move, board_img):
        """A promoted queen and a promoted knight look identical to the
        occupancy reader, so ask the piece reader what actually appeared.
        Without this an underpromotion is silently recorded as a queen."""
        if not move.promotion or board_img is None or self.reader is None:
            return move
        row, col = self._square_on_screen(move.to_square)
        symbol = self.reader.piece_at(board_img, row, col)
        if not symbol or symbol == ".":
            return move
        piece = chess.Piece.from_symbol(symbol)
        if piece.color == self.board.turn and piece.piece_type in (
                chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT):
            return chess.Move(move.from_square, move.to_square,
                              promotion=piece.piece_type)
        return move

    def _search_occupancy(self, occ):
        """The move, or pair of moves if a frame was missed, that produces
        exactly the occupancy now on screen.

        Refuses when more than one story fits. Two different pairs of moves can
        leave an identical light-and-dark picture: a capture and recapture on
        the same square hides the square entirely, and a pawn's double push
        followed by an en passant capture looks the same as a single push
        followed by an ordinary one. Guessing there writes a move that never
        happened into an otherwise perfect game.
        """
        return self._solve(occ, 2, colour_only=True)

    # -- the piece-level checker -------------------------------------
    # Four is where the search stops paying. Depth 4 recovers a four move
    # skip in about a fifth of a second; depth 5 costs seconds and recovers
    # nothing extra, because by then almost every gap has more than one
    # move order and gets refused anyway.
    MAX_CATCHUP = 4

    def check(self, board_img, depth=None):
        """Look at what is actually on every square and reconcile.

        Returns a short human-readable status, and is safe to call as often as
        you like. This is what recovers a game after missed moves, and what
        lets a game already in progress be picked up.
        """
        if not self.reader or not self.reader.ready:
            self.last_check = "no piece templates"
            return self.last_check

        rows, _ = self.reader.classify(board_img)
        if any("?" in row for row in rows):
            self.last_check = "board unclear"
            return self.last_check

        if self.board is None:
            self.last_check = self._cold_start(rows, board_img)
            return self.last_check

        if self.over:
            self.last_check = "game finished, leaving the record alone"
            return self.last_check

        if rows == grid_of(self.board, self.flipped):
            self._pending = None
            self.last_check = "position confirmed"
            return self.last_check

        caught = self._search_grid(rows, min(depth or self.MAX_CATCHUP,
                                             self.MAX_CATCHUP))
        if caught:
            self._apply(caught, board_img)
            self.last_check = "caught up %d missed move%s" % (
                len(caught), "" if len(caught) == 1 else "s")
            return self.last_check

        back = self._earlier_match(rows)
        if back is not None:
            self.game.moves = self.game.moves[:back]
            self._rebuild()
            self.last_check = "takeback, rewound to %d moves" % back
            return self.last_check

        self.last_check = self._resume_from(rows)
        return self.last_check

    # A single move rewrites at most four squares: castling moves a king and a
    # rook. So a position N squares wrong cannot be reached in fewer than N/4
    # moves, which is what makes the search below affordable.
    SQUARES_PER_MOVE = 4

    def _target_map(self, rows):
        """The screen reading, indexed by board square instead of by row and
        column, so it can be compared one square at a time. Works for both
        readings: piece letters from the piece reader, or W/B/. occupancy."""
        out = [None] * 64
        for r in range(8):
            for c in range(8):
                rank, file = (r, 7 - c) if self.flipped else (7 - r, c)
                out[chess.square(file, rank)] = rows[r][c]
        return out

    def _symbol_at(self, square, colour_only=False):
        piece = self.board.piece_at(square)
        if piece is None:
            return "."
        if colour_only:
            return "W" if piece.color else "B"
        return piece.symbol()

    def _wrong_squares(self, target, colour_only=False):
        return sum(1 for sq in chess.SQUARES
                   if self._symbol_at(sq, colour_only) != target[sq])

    def _touched(self, move):
        """Which squares a move rewrites. Must be asked before the move is
        played, because castling and en passant are only recognisable then."""
        squares = [move.from_square, move.to_square]
        if self.board.is_castling(move):
            rank = chess.square_rank(move.from_square)
            if chess.square_file(move.to_square) > chess.square_file(move.from_square):
                squares += [chess.square(7, rank), chess.square(5, rank)]
            else:
                squares += [chess.square(0, rank), chess.square(3, rank)]
        elif self.board.is_en_passant(move):
            squares.append(chess.square(chess.square_file(move.to_square),
                                        chess.square_rank(move.from_square)))
        return squares

    def _search_grid(self, rows, depth):
        """Shortest run of legal moves leading to exactly the pieces on screen.

        Shallowest first, so one missed move is never explained by a longer
        story than it needs. The run has to be the ONLY one of its length:
        three moves can transpose, so 1.e4 e5 2.Nf3 and 1.Nf3 e5 2.e4 arrive at
        the same picture, and writing down the wrong order would be worse than
        admitting the gap.

        Only the squares a move actually rewrites are re-examined, and a branch
        is abandoned as soon as too many squares are still wrong for the moves
        that remain. Without that this costs seconds and cannot run live.
        """
        return self._solve(rows, depth, colour_only=False)

    def _solve(self, rows, depth, colour_only):
        target = self._target_map(rows)
        wrong = self._wrong_squares(target, colour_only)
        if wrong == 0:
            return None
        for limit in range(1, depth + 1):
            if wrong > self.SQUARES_PER_MOVE * limit:
                continue               # too far away to be reached in this many
            found = self._walk(target, limit, wrong, [], colour_only=colour_only)
            # Two sequences that differ only in which piece a pawn promoted to
            # are the same answer as far as the search is concerned; the piece
            # reader decides that separately.
            shapes = {tuple((m.from_square, m.to_square) for m in seq)
                      for seq in found}
            if len(shapes) == 1:
                return found[0]
            if shapes:
                return None            # more than one story fits, so do not pick
        return None

    def _walk(self, target, k, wrong, prefix, limit=3, colour_only=False):
        """Every sequence of exactly k legal moves reaching the target squares.
        Stops early once `limit` have been found, since one alternative is
        already enough to make the answer ambiguous."""
        out = []
        for move in _ordered_moves(self.board):
            touched = self._touched(move)
            before = sum(1 for sq in touched
                         if self._symbol_at(sq, colour_only) != target[sq])
            self.board.push(move)
            after = sum(1 for sq in touched
                        if self._symbol_at(sq, colour_only) != target[sq])
            now = wrong + after - before
            if k == 1:
                if now == 0:
                    out.append(prefix + [move])
            elif now <= self.SQUARES_PER_MOVE * (k - 1):
                out.extend(self._walk(target, k - 1, now, prefix + [move],
                                      limit - len(out), colour_only))
            self.board.pop()
            if len(out) >= limit:
                break
        return out

    def _earlier_match(self, rows):
        """Number of moves after which the game looked like this, if it did.
        That is what a takeback looks like from the outside."""
        board = self.game.board_at_start()
        if grid_of(board, self.flipped) == rows:
            return 0
        for i, san in enumerate(self.game.moves, 1):
            board.push_san(san)
            if grid_of(board, self.flipped) == rows and i < len(self.game.moves):
                return i
        return None

    def _rebuild(self):
        board = self.game.board_at_start()
        for san in self.game.moves:
            board.push_san(san)
        self.board = board
        self.game.result, self.game.termination = "*", ""
        self.game.note_outcome(board)

    def _resume_from(self, rows):
        """The position no longer follows from what we recorded and we cannot
        explain the gap. Save what we have and start again from what is on
        screen, rather than write down moves that did not happen."""
        candidates = [b for b in
                      (board_from_grid(rows, self.flipped, chess.WHITE),
                       board_from_grid(rows, self.flipped, chess.BLACK))
                      if b is not None]
        if not candidates:
            return "board unclear"
        if len(candidates) > 1:
            self._pending = rows
            return "lost the thread, waiting for a move"

        self._close_current()
        self._begin(candidates[0], self.flipped, joined_late=True)
        return "lost the thread, restarted from the position on screen"

    def _cold_start(self, rows, board_img=None):
        """Join a game already in progress. Which way the board faces and whose
        turn it is cannot both be read from one picture, so any that the rules
        allow are kept and the next move decides between them."""
        if rows in (grid_of(chess.Board(), False), grid_of(chess.Board(), True)):
            return "waiting for the game to start"

        if self._pending is not None and self._pending != rows:
            resolved = self._resolve(self._pending, rows)
            if resolved:
                board, flipped, moves = resolved
                self._begin(board, flipped, joined_late=True)
                self._apply(moves, board_img)
                self._pending = None
                return "joined a game already in progress"

        options = [(b, f) for f in self._orientations()
                   for b in (board_from_grid(rows, f, chess.WHITE),
                             board_from_grid(rows, f, chess.BLACK))
                   if b is not None]
        if not options:
            return "board unclear"
        if len(options) == 1:
            board, flipped = options[0]
            self._begin(board, flipped, joined_late=True)
            return "joined a game already in progress"

        self._pending = rows
        return "found a game, waiting for a pawn move to tell which way up"

    def _orientations(self):
        if self.preferred_flipped is None:
            return (False, True)
        return (self.preferred_flipped,)

    def _resolve(self, before, after):
        """Find the one orientation, side to move and move sequence that turns
        the first position into the second. None while it stays ambiguous.

        A rotated board with the colours read the same way is itself a legal
        game, so a knight or queen move explains the screen equally well both
        ways up. A pawn move does not: pawns only go one way, so the first pawn
        move settles it. That is why this waits rather than guesses.
        """
        hits = []
        for flipped in self._orientations():
            for turn in (chess.WHITE, chess.BLACK):
                start = board_from_grid(before, flipped, turn)
                if start is None:
                    continue
                for moves in self._sequences(start, after, flipped, 2):
                    hits.append((board_from_grid(before, flipped, turn),
                                 flipped, moves))
                    if len(hits) > 1:
                        return None
        return hits[0] if len(hits) == 1 else None

    @staticmethod
    def _sequences(board, target, flipped, depth):
        """Every move sequence up to `depth` long reaching the target pieces."""
        out = []
        for first in _ordered_moves(board):
            board.push(first)
            if grid_of(board, flipped) == target:
                out.append([first])
            elif depth > 1:
                for second in _ordered_moves(board):
                    board.push(second)
                    if grid_of(board, flipped) == target:
                        out.append([first, second])
                    board.pop()
            board.pop()
        return out

    # -- game lifecycle ----------------------------------------------
    def _begin(self, board, flipped, joined_late=False):
        self.board = board
        self.flipped = flipped
        self.game = Game("black" if flipped else "white", self.directory,
                         start_fen=board.fen() if joined_late else None)
        self._last_unmatched = None
        self._pending = None
        if joined_late:
            self.game.note_outcome(board)

    def _close_current(self):
        if self.game is not None and self.game.moves:
            self.game.save()
            self.finished.append(self.game)

    def _start(self, flipped):
        self._close_current()
        self._begin(chess.Board(), flipped)
        return "newgame"

    def set_colour(self, colour):
        """Tell it which colour you are, instead of waiting for a pawn move to
        reveal which way the board is facing. None goes back to working it out.
        """
        self.preferred_flipped = None if colour not in ("white", "black")             else colour == "black"
        self._pending = None

    def learn_pieces(self, board_img):
        """Relearn the piece templates from a position we are certain about.

        Needs all twelve piece types on the board at once, so the caller uses
        it at the start of a game. A game joined part way through does not
        reach this, which is why relearn_pieces exists.
        """
        if self.reader is None or self.board is None:
            return False
        return self.reader.learn(board_img, self.board, self.flipped)

    def relearn_pieces(self, board_img):
        """Refit the templates mid game, when the board has changed size.

        Needs all twelve piece types, same as learn_pieces, which survives far
        longer than the starting position: over four master games it held for
        137 of 151 plies. Endgames are what it cannot do. Failing costs nothing
        though, because pieces.relearn only drops the templates in hand when
        they are measurably worse than the bundled sheet, so this is safe to
        call on spec, and it is the only route to learned templates for a game
        joined part way through.

        The trap is that learn() names every square out of self.board, so a
        picture that has moved on since teaches it pieces off squares they have
        already left. One ply of lag changed the templates on about half the
        positions in those four games and cost up to 8 squares of 64, as wrong
        pieces rather than as "?", and a resize is itself a common way to fall
        behind. Occupancy is the test because a ply always empties a square.
        self.stuck is not: it stays set after the screen agrees again, until
        check() clears it.
        """
        if self.reader is None or self.board is None:
            return False
        if read_occupancy(board_img) != occupancy_of(self.board, self.flipped):
            return False
        return self.reader.relearn(board_img, self.board, self.flipped)

    def ascii_board(self):
        return "" if self.board is None else str(self.board)
