"""Can a learned classifier read a piece set nobody trained on?

The reader in the tree is a template matcher enrolled on the set in front of
it. This asks the other question: train one model on many piece sets and hand
it a set it has never seen.

Training labels are free, so the corpus is 31 piece sets rendered at random
sizes, board themes and degradations, 8320 squares each. Twenty-two of them
train the held-out model, five more are kept back to pick the abstention
threshold, and the four the bench actually scores are seen by nothing but the
ceiling model. There is no routing and no set identification, so a bench set
cannot leak in.

What bench.py says, over the full 65,536 square sweep:

  reader in the tree                  right 86.48   WRONG 2.74   unknown 10.78
  ceiling CNN, bench sets in training right 99.87   WRONG 0.13   unknown  0.00
  held-out CNN, class head alone      right 93.22   WRONG 6.78   unknown  0.00
  held-out CNN, enrolled prototypes   right 99.12   WRONG 0.88   unknown  0.00
  the same, refusing below 0.05       right 98.12   WRONG 0.25   unknown  1.63

The middle two lines are the whole result. The same weights, on the same
picture, differ by a factor of eight in wrong answers depending on whether the
last layer is asked to name the piece or only to describe it.

  The 13-way head does not survive a new set. It is 99.98% right on the sets it
  trained on and 92.4% right on five it has not seen, and its softmax cannot be
  thresholded out of trouble: at a margin of 0.9999 it still gets 1.94% of
  squares wrong while refusing 38% of them. On seguisym it is confidently wrong
  about one square in seven.

  The layer below it does survive. Take the 64 numbers before the head as a
  description of a square, average them per piece over the starting position
  the program already enrolls, and match a square to the nearest description:
  0.88% wrong with nothing refused, 0.25% wrong refusing 1.6%. The gap between
  the best prototype and the runner up is a usable confidence, unlike the
  softmax.

So a learned model does generalise here, but not as a classifier. It
generalises as a way of measuring how alike two squares are, which still needs
the enrollment the program already does.

Inference is numpy only; torch fits the weights and is not imported at bench
time. The shipped file is 66 KB of int8 weights, which scores the same as
float32 to within 0.01 of a percentage point, and a board costs 64 ms.

Without numpy none of that is available. Plain Python manages 15.4 million
multiply-adds a second here, and the convolutions are 154 million a board, so
the same model would take ten seconds. What does fit is a single hidden layer
over an 8x8 average of the square, 4557 weights and 87 ms a board in pure
Python, and it gets 11.55% of squares wrong. That is the price of the
dependency, stated as a number: numpy or 11.55% wrong.
"""

import os
import random
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SCRATCH = os.path.dirname(os.path.dirname(HERE))

# Where the training piece sets live. Not in the repository: 23 of them are
# lichess's own SVG sets rasterised to PNG, the rest are fonts carrying the
# Unicode chess block. Point TRY_LEARNED_ASSETS at a directory holding
# <set-name>/{wK,wQ,...,bP}.png to retrain.
ASSETS = os.environ.get("TRY_LEARNED_ASSETS") or os.path.join(SCRATCH, "pngsets")
EXTRA_FONTS = os.environ.get("TRY_LEARNED_FONTS") or os.path.join(
    SCRATCH, "extrafonts")

CLASSES = "." + "KQRBNPkqrbnp"          # 13 of them, empty first
IDX = {c: i for i, c in enumerate(CLASSES)}

N = 32                                   # a square, resampled, in pixels
CH = 2                                   # see features()

WHITE_GLYPHS = {"K": "♔", "Q": "♕", "R": "♖",
                "B": "♗", "N": "♘", "P": "♙"}
BLACK_GLYPHS = {"k": "♚", "q": "♛", "r": "♜",
                "b": "♝", "n": "♞", "p": "♟"}

THEMES = [                               # light, dark, as real boards use
    ((235, 236, 208), (115, 149, 82)),   # chess.com green
    ((240, 217, 181), (181, 136, 99)),   # lichess brown
    ((222, 227, 230), (140, 162, 173)),  # blue grey
    ((238, 238, 210), (118, 150, 86)),   # lichess green
    ((206, 208, 211), (137, 139, 143)),  # grey
    ((247, 243, 236), (188, 168, 149)),  # bone
    ((219, 227, 245), (140, 155, 190)),  # ice
    ((234, 226, 183), (167, 129, 92)),   # wood
]


# ---------------------------------------------------------------- features

def features(board_img, n=N):
    """64 squares as (CH, n, n) floats, in screen order, row 0 at the top.

    The whole board is resampled once to 8n and then sliced, rather than
    cropping and resampling 64 times, because it is the same picture and one
    LANCZOS pass instead of 64 is most of the per-board cost.

    Both channels are measured against the board's own two square colours, so a
    brown board, a green one and a board somebody dimmed all reduce to the same
    numbers. ch0 is how far a pixel is from the colour its own square is
    painted, which is what says something is standing there. ch1 is how far it
    is from halfway between the two square colours, which is what says whether
    the piece is a light one or a dark one without having to know which colour
    square it happens to be on.
    """
    g = np.asarray(board_img.convert("L").resize((8 * n, 8 * n), Image.LANCZOS),
                   dtype=np.float32)
    sq = g.reshape(8, n, 8, n).transpose(0, 2, 1, 3).reshape(64, n, n)

    # The outer frame of each square. Piece art almost never reaches a corner,
    # and over 32 squares of one colour the median survives the sets where it
    # does.
    ring = np.ones((n, n), bool)
    b = max(2, n // 8)
    ring[b:-b, b:-b] = False
    parity = np.array([((i // 8) + (i % 8)) % 2 == 0 for i in range(64)])
    lit = sq[parity][:, ring]
    drk = sq[~parity][:, ring]
    light = float(np.median(lit)) if lit.size else 200.0
    dark = float(np.median(drk)) if drk.size else 120.0
    span = max(abs(light - dark), 40.0)
    mid = 0.5 * (light + dark)

    base = np.where(parity, light, dark).astype(np.float32)[:, None, None]
    out = np.stack([(sq - base) / span, (sq - mid) / span], axis=1)
    return np.clip(out, -4.0, 4.0, out=out)


# ---------------------------------------------------------------- renderers

def png_sets(root=None):
    root = root or ASSETS
    if not os.path.isdir(root):
        return []
    want = [c + p for c in "wb" for p in "KQRBNP"]
    return [name for name in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, name))
            and all(os.path.exists(os.path.join(root, name, w + ".png"))
                    for w in want)]


class PngSet:
    """A piece set held as twelve RGBA sprites."""

    KEY = {"K": "wK", "Q": "wQ", "R": "wR", "B": "wB", "N": "wN", "P": "wP",
           "k": "bK", "q": "bQ", "r": "bR", "b": "bB", "n": "bN", "p": "bP"}

    def __init__(self, name, root=None):
        self.name = name
        d = os.path.join(root or ASSETS, name)
        self.art = {sym: Image.open(os.path.join(d, tag + ".png")).convert("RGBA")
                    for sym, tag in self.KEY.items()}
        self._cache = {}

    def _fit(self, sym, px):
        key = (sym, px)
        if key not in self._cache:
            if len(self._cache) > 300:
                self._cache.clear()
            self._cache[key] = self.art[sym].resize((px, px), Image.LANCZOS)
        return self._cache[key]

    def render(self, board, size, flipped=False, theme=None, scale=0.92):
        import chess
        light, dark = theme or THEMES[0]
        step = size // 8
        img = Image.new("RGB", (step * 8, step * 8))
        pen = ImageDraw.Draw(img)
        for r in range(8):
            for c in range(8):
                pen.rectangle([c * step, r * step, (c + 1) * step - 1,
                               (r + 1) * step - 1],
                              fill=light if (r + c) % 2 == 0 else dark)
        px = max(6, int(step * scale))
        off = (step - px) // 2
        ranks = range(8) if flipped else range(7, -1, -1)
        for row, rank in enumerate(ranks):
            files = range(7, -1, -1) if flipped else range(8)
            for col, file in enumerate(files):
                piece = board.piece_at(chess.square(file, rank))
                if not piece:
                    continue
                art = self._fit(piece.symbol(), px)
                img.paste(art, (col * step + off, row * step + off), art)
        return img


class FontSet:
    """A piece set drawn from a font's chess block.

    setgen.py does this too and this is deliberately not a call into it: the
    body and edge colours here are variable, because a real set is rarely a
    pure white body inside a pure black edge and a model trained only on that
    learns the wrong thing about what a light piece looks like.
    """

    def __init__(self, name, path):
        self.name = name
        self.path = path
        self._faces = {}

    def _face(self, px):
        if px not in self._faces:
            if len(self._faces) > 40:
                self._faces.clear()
            self._faces[px] = ImageFont.truetype(self.path, px)
        return self._faces[px]

    def render(self, board, size, flipped=False, theme=None, scale=0.78,
               body=(255, 255, 255), edge=(0, 0, 0), dark_body=(0, 0, 0)):
        import chess
        light, dark = theme or THEMES[0]
        step = size // 8
        img = Image.new("RGB", (step * 8, step * 8))
        pen = ImageDraw.Draw(img)
        for r in range(8):
            for c in range(8):
                pen.rectangle([c * step, r * step, (c + 1) * step - 1,
                               (r + 1) * step - 1],
                              fill=light if (r + c) % 2 == 0 else dark)
        face = self._face(max(6, int(step * scale)))
        ranks = range(8) if flipped else range(7, -1, -1)
        for row, rank in enumerate(ranks):
            files = range(7, -1, -1) if flipped else range(8)
            for col, file in enumerate(files):
                piece = board.piece_at(chess.square(file, rank))
                if not piece:
                    continue
                sym = piece.symbol()
                solid = BLACK_GLYPHS[sym.lower()]
                outline = WHITE_GLYPHS[sym.upper()]
                box = face.getbbox(solid)
                gw, gh = box[2] - box[0], box[3] - box[1]
                if gw <= 0 or gh <= 0:
                    continue
                x = col * step + (step - gw) // 2 - box[0]
                y = row * step + (step - gh) // 2 - box[1]
                if piece.color:
                    pen.text((x, y), solid, font=face, fill=body)
                    pen.text((x, y), outline, font=face, fill=edge)
                else:
                    pen.text((x, y), solid, font=face, fill=dark_body)
                    pen.text((x, y), outline, font=face, fill=edge)
        return img


def font_sets(root=None):
    """Every font in the extra-font directory that draws all twelve pieces."""
    root = root or EXTRA_FONTS
    out = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        if not name.lower().endswith((".ttf", ".ttc", ".otf")):
            continue
        path = os.path.join(root, name)
        try:
            face = ImageFont.truetype(path, 64)
            inks = []
            for g in list(WHITE_GLYPHS.values()) + list(BLACK_GLYPHS.values()):
                im = Image.new("L", (96, 96), 0)
                ImageDraw.Draw(im).text((12, 0), g, font=face, fill=255)
                inks.append(frozenset(i for i, v
                                      in enumerate(im.get_flattened_data())
                                      if v > 128))
        except Exception:
            continue
        if any(len(k) < 200 for k in inks) or len(set(inks)) < 12:
            continue
        out.append(FontSet(os.path.splitext(name)[0], path))
    return out


def bench_sets():
    """The four sets bench.py scores, as renderers. Only the ceiling model is
    ever allowed to see these."""
    import chess
    import setgen
    import watcher as W
    from fakeboard import Renderer

    class _Real:
        def __init__(self, name, r):
            self.name, self.r = name, r

        def render(self, board, size, flipped=False, theme=None, scale=None):
            img = self.r.render(board, flipped)
            return img if img.size[0] == size else img.resize((size, size),
                                                              Image.LANCZOS)

    class _Font(FontSet):
        def render(self, board, size, flipped=False, theme=None, scale=0.78,
                   **kw):
            return setgen.render(board, self.path, size, flipped)

    out = []
    ref = chess.Board()
    for san in ("e4", "c5", "d4", "e6"):
        ref.push_san(san)
    for name, shot, b in (("chesscom", "testdata/1.png", ref),
                          ("flat", "testdata/6.png", chess.Board())):
        p = os.path.join(HERE, shot)
        if os.path.exists(p):
            rect = W.find_board(Image.open(p).convert("RGB"))
            out.append(_Real(name, Renderer(p, rect, board=b)))
    for f in setgen.fonts():
        if setgen.readable(f):
            out.append(_Font(os.path.basename(f).split(".")[0], f))
    return out


# ---------------------------------------------------------------- positions

def random_positions(n, rng):
    """Positions to draw. Half are real games played out to a random depth so
    the piece mix is the one a board actually holds; half are sprinkles, which
    is the only way to see a board with eight queens on it and get enough of
    the rare classes to train on."""
    import chess
    out = []
    for i in range(n):
        if i % 2 == 0:
            b = chess.Board()
            for _ in range(rng.randrange(0, 70)):
                moves = list(b.legal_moves)
                if not moves:
                    break
                b.push(rng.choice(moves))
            out.append(b)
        else:
            b = chess.Board(None)
            squares = rng.sample(range(64), rng.randrange(4, 33))
            syms = "KQRBNPkqrbnp"
            for s in squares:
                b.set_piece_at(s, chess.Piece.from_symbol(rng.choice(syms)))
            out.append(b)
    return out


# ---------------------------------------------------------------- augment

def augment(img, rng):
    """What a screenshot does to a board that a renderer does not.

    One axis at a time on top of a resize, because a soup of eight filters
    trains a model on a picture no screen ever produced.
    """
    kind = rng.choice(["plain", "blur", "blur", "thin", "heavy", "small",
                       "small", "dim", "bright", "contrast", "soft", "noise"])
    if kind == "blur":
        img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.4, 2.0)))
    elif kind == "thin":
        img = img.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    elif kind == "heavy":
        img = img.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    elif kind == "small":
        w = img.size[0]
        f = rng.uniform(0.5, 0.85)
        img = img.resize((max(64, int(w * f)),) * 2, Image.LANCZOS).resize(
            (w, w), Image.LANCZOS)
    elif kind == "dim":
        img = ImageEnhance.Brightness(img).enhance(rng.uniform(0.62, 0.9))
    elif kind == "bright":
        img = ImageEnhance.Brightness(img).enhance(rng.uniform(1.05, 1.25))
    elif kind == "contrast":
        img = ImageEnhance.Contrast(img).enhance(rng.uniform(0.75, 1.4))
    elif kind == "soft":
        img = img.filter(ImageFilter.SMOOTH)
    elif kind == "noise":
        a = np.asarray(img, np.float32)
        a += np.random.normal(0, rng.uniform(2, 9), a.shape).astype(np.float32)
        img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    return img


def make_pack(renderer, boards, rng, sizes=(200, 280, 320, 400, 560, 704, 824,
                                            1000, 1200)):
    """Squares and labels for one piece set."""
    import watcher as W
    X, y = [], []
    for board in boards:
        size = rng.choice(sizes)
        flipped = rng.random() < 0.5
        theme = rng.choice(THEMES)
        scale = rng.uniform(0.72, 1.0)
        kw = {}
        if type(renderer) is FontSet:
            # A font gives one shape per piece and nothing else, so the colours
            # have to be varied here or the model learns that a light piece is
            # exactly 255 white inside exactly 0 black.
            g = rng.randrange(215, 256)
            scale = rng.uniform(0.62, 0.86)
            kw = {"body": (g, g - rng.randrange(0, 12), g - rng.randrange(0, 20)),
                  "edge": (rng.randrange(0, 70),) * 3,
                  "dark_body": (rng.randrange(0, 60),) * 3}
        try:
            img = renderer.render(board, size, flipped, theme=theme,
                                  scale=scale, **kw)
        except Exception:
            continue
        img = augment(img, rng)
        grid = W.grid_of(board, flipped)
        X.append(features(img))
        y.append(np.array([IDX[grid[r][c]] for r in range(8) for c in range(8)],
                          np.int64))
    if not X:
        return None
    return np.concatenate(X), np.concatenate(y)


def build_data(out_dir, per_set=90, seed=7, include_bench=False):
    """Render the training corpus once and cache it, one file per piece set."""
    os.makedirs(out_dir, exist_ok=True)
    rng = random.Random(seed)
    sets = [(n, PngSet(n)) for n in png_sets()] + \
           [(f.name, f) for f in font_sets()]
    if include_bench:
        sets += [(b.name + "@bench", b) for b in bench_sets()]
    for name, renderer in sets:
        path = os.path.join(out_dir, name.replace("@", "_") + ".npz")
        if os.path.exists(path):
            print("  have", name)
            continue
        t = time.time()
        pack = make_pack(renderer, random_positions(per_set, rng), rng)
        if pack is None:
            print("  FAILED", name)
            continue
        X, y = pack
        np.savez_compressed(path, X=X.astype(np.float16), y=y, name=name)
        print("  %-28s %6d squares  %.1fs" % (name, len(y), time.time() - t))
    return out_dir


# ---------------------------------------------------------------- the model

class Net:
    """A small convolutional classifier, run forward in numpy.

    conv 2->16, conv 16->32, conv 32->48, each 3x3 with a 2x2 max pool after
    it, then 768 -> 64 -> 13. Fitted in torch, stored as arrays, and evaluated
    here so nothing but numpy is needed to read a board.
    """

    def __init__(self, w):
        self.w = {k: np.asarray(v, np.float32) for k, v in w.items()}
        # Everything below runs channels-last. A 3x3 convolution is then nine
        # plain matrix multiplies over a shifted view, which is a tenth of the
        # cost of building an im2col matrix: that matrix is 144 floats per
        # output pixel and the allocation alone was most of the time.
        self.k = {n: self.w[n + "w"].transpose(2, 3, 1, 0).copy()
                  for n in ("c1", "c2", "c3")}

    def _conv(self, x, name):
        """x is (n, h, w, cin); returns relu(conv3x3(x)) as (n, h, w, cout)."""
        k = self.k[name]                     # (3, 3, cin, cout)
        n, h, wd, c = x.shape
        p = np.zeros((n, h + 2, wd + 2, c), np.float32)
        p[:, 1:-1, 1:-1, :] = x
        out = np.zeros((n, h, wd, k.shape[3]), np.float32)
        for i in range(3):
            for j in range(3):
                out += p[:, i:i + h, j:j + wd, :] @ k[i, j]
        out += self.w[name + "b"]
        return np.maximum(out, 0, out=out)

    @staticmethod
    def _pool(x):
        n, h, w, c = x.shape
        x = x[:, :h - h % 2, :w - w % 2, :]
        return x.reshape(n, h // 2, 2, w // 2, 2, c).max(axis=(2, 4))

    def hidden(self, x):
        """The last hidden layer. x is (n, CH, N, N), as features() hands it
        over. Kept separate from logits() because the prototype reader wants
        this and the class scores from one pass, and running the convolutions
        twice for them was half the time a board cost."""
        x = np.ascontiguousarray(x.transpose(0, 2, 3, 1))
        x = self._pool(self._conv(x, "c1"))
        x = self._pool(self._conv(x, "c2"))
        x = self._pool(self._conv(x, "c3"))
        # torch flattens (n, c, h, w) in that order, so put the channel back
        # in front before flattening or the fully connected layer is fed a
        # permutation of what it was fitted on.
        x = x.transpose(0, 3, 1, 2).reshape(x.shape[0], -1)
        return np.maximum(x @ self.w["f1w"].T + self.w["f1b"], 0)

    def head(self, h):
        return h @ self.w["f2w"].T + self.w["f2b"]

    def logits(self, x):
        return self.head(self.hidden(x))

    @property
    def params(self):
        return int(sum(v.size for v in self.w.values()))


def softmax(z):
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


# ---------------------------------------------------------------- entrant

WEIGHTS = {}


def load(path):
    if path not in WEIGHTS:
        with np.load(path) as z:
            WEIGHTS[path] = Net({k: z[k] for k in z.files if k != "meta"})
    return WEIGHTS[path]


class LearnedReader:
    """The bench entrant. Reads a board with no enrollment at all: learn() is
    accepted and thrown away, which is the whole point."""

    def __init__(self, weights, floor=0.0, use_margin=True):
        self.net = load(weights)
        self.floor = floor
        self.use_margin = use_margin

    def learn(self, board_img, board, flipped=False):
        return True

    def confidence(self, p):
        top = np.sort(p, axis=1)
        return (top[:, -1] - top[:, -2]) if self.use_margin else top[:, -1]

    def classify(self, board_img):
        p = softmax(self.net.logits(features(board_img)))
        best = p.argmax(axis=1)
        conf = self.confidence(p)
        rows = []
        for r in range(8):
            row = []
            for c in range(8):
                i = r * 8 + c
                row.append("?" if conf[i] < self.floor else CLASSES[best[i]])
            rows.append(row)
        return rows


def factory(weights=None, floor=0.0):
    """What bench.score() wants: a callable that makes a fresh entrant."""
    weights = weights or os.environ.get("TRY_LEARNED_WEIGHTS") or \
        os.path.join(HERE, "learned_heldout.npz")
    floor = float(os.environ.get("TRY_LEARNED_FLOOR", floor))
    return lambda: LearnedReader(weights, floor)


def heldout():
    return factory(os.path.join(HERE, "learned_heldout.npz"))


def ceiling():
    return factory(os.path.join(HERE, "learned_ceiling.npz"))


# ---------------------------------------------------------------- fitting

TORCH_SHAPES = [("c1w", (16, CH, 3, 3)), ("c1b", (16,)),
                ("c2w", (32, 16, 3, 3)), ("c2b", (32,)),
                ("c3w", (48, 32, 3, 3)), ("c3b", (48,)),
                ("f1w", (64, 48 * 4 * 4)), ("f1b", (64,)),
                ("f2w", (13, 64)), ("f2b", (13,))]


def _torch_net():
    import torch.nn as nn
    return nn.Sequential(
        nn.Conv2d(CH, 16, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
        nn.Conv2d(16, 32, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
        nn.Conv2d(32, 48, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
        nn.Flatten(), nn.Linear(48 * 4 * 4, 64), nn.ReLU(), nn.Linear(64, 13))


def _export(model):
    names = ["c1w", "c1b", "c2w", "c2b", "c3w", "c3b", "f1w", "f1b",
             "f2w", "f2b"]
    vals = [p.detach().cpu().numpy().astype(np.float32)
            for p in model.parameters()]
    return dict(zip(names, vals))


def load_packs(data_dir, names):
    """Held as float16. 180k squares of 2x32x32 is 1.5GB in float32 and half
    of that is the whole training set sitting in memory twice, so the cast to
    float32 happens a batch at a time instead."""
    X, y, who = [], [], []
    for n in names:
        p = os.path.join(data_dir, n + ".npz")
        with np.load(p) as z:
            X.append(z["X"])
            y.append(z["y"])
            who += [n] * len(z["y"])
    return np.concatenate(X), np.concatenate(y), np.array(who)


def jitter_batch(x, gen):
    """Shake a batch of squares about, in feature space.

    Rendering variation is already in the corpus; this is on top of it, and it
    exists because the training loss falls to nothing after two epochs while
    the accuracy on sets the model has not seen stops moving. A shift of a
    pixel or two, a change of gain, an offset and a bite taken out of the
    square are the four things that differ between one set's rook and another's
    that are not shape.
    """
    import torch
    n = x.shape[0]
    dx, dy = int(gen.integers(-2, 3)), int(gen.integers(-2, 3))
    if dx or dy:
        x = torch.roll(x, shifts=(dy, dx), dims=(2, 3))
    gain = torch.from_numpy(
        gen.uniform(0.82, 1.18, (n, 1, 1, 1)).astype(np.float32))
    bias = torch.from_numpy(
        gen.uniform(-0.12, 0.12, (n, 1, 1, 1)).astype(np.float32))
    x = x * gain + bias
    if gen.random() < 0.35:                # a bite out of the square
        h = int(gen.integers(4, 11))
        r0 = int(gen.integers(0, N - h))
        c0 = int(gen.integers(0, N - h))
        x[:, :, r0:r0 + h, c0:c0 + h] = 0.0
    return x


def fit(data_dir, train_names, out_path, epochs=14, seed=0, val_names=(),
        jitter=False, decay=0.0, smooth=0.0):
    import torch
    import torch.nn as nn
    torch.manual_seed(seed)
    # Four, not every core. Measured on this box: one batch takes 0.43s on one
    # thread, 0.17s on four and 9.9s on eight, which is a sixty-fold cliff, not
    # a slowdown. A 2x32x32 convolution is too small to feed that many workers
    # and they end up fighting over it.
    torch.set_num_threads(int(os.environ.get("TORCH_THREADS", 4)))
    X, y, _ = load_packs(data_dir, train_names)
    print("train %d squares over %d sets" % (len(y), len(train_names)))

    # Empty squares are two thirds of any board, so they are weighted down
    # rather than thrown away: the model still has to be right about them, it
    # just must not win by saying "empty".
    counts = np.bincount(y, minlength=13).astype(np.float32)
    weight = (counts.sum() / np.maximum(counts, 1)) ** 0.5
    weight /= weight.mean()

    model = _torch_net()
    opt = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=decay)
    lossf = nn.CrossEntropyLoss(weight=torch.tensor(weight),
                                label_smoothing=smooth)
    gen = np.random.default_rng(seed + 1)
    Xt = torch.from_numpy(X)          # float16, cast per batch
    yt = torch.from_numpy(y)
    n = len(y)
    bs = 512
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, epochs * (n // bs + 1))
    for ep in range(epochs):
        model.train()
        perm = torch.randperm(n)
        tot = 0.0
        for i in range(0, n, bs):
            idx = perm[i:i + bs]
            opt.zero_grad()
            xb = Xt[idx].float()
            if jitter:
                xb = jitter_batch(xb, gen)
            out = model(xb)
            loss = lossf(out, yt[idx])
            loss.backward()
            opt.step()
            sched.step()
            tot += float(loss) * len(idx)
        msg = "epoch %2d  loss %.4f" % (ep + 1, tot / n)
        if val_names:
            msg += "   " + val_report(model, data_dir, val_names)
        print(msg, flush=True)
    w = _export(model)
    np.savez_compressed(out_path, **w)
    print("wrote %s  %d params  %d bytes on disk"
          % (out_path, sum(v.size for v in w.values()),
             os.path.getsize(out_path)))
    return w


def val_report(model, data_dir, names):
    import torch
    model.eval()
    X, y, _ = load_packs(data_dir, names)
    Xt = torch.from_numpy(X)
    out = []
    with torch.no_grad():
        for i in range(0, len(y), 2048):
            out.append(torch.softmax(model(Xt[i:i + 2048].float()), dim=1).numpy())
    p = np.concatenate(out)
    best = p.argmax(1)
    top = np.sort(p, axis=1)
    margin = top[:, -1] - top[:, -2]
    acc = (best == y).mean()
    # what a margin floor buys, on sets this model never trained on
    out = "val acc %.4f" % acc
    for f in (0.9, 0.99):
        keep = margin >= f
        wrong = ((best != y) & keep).mean()
        out += "  @%.2f wrong %.3f%% unk %.1f%%" % (
            f, 100 * wrong, 100 * (~keep).mean())
    return out


# ------------------------------------------------- the dependency-free arm

POOL = 4                                  # 32 -> 8 per side
SMALL = (N // POOL) ** 2 * CH             # 128 numbers a square


def small_features(x):
    """A square reduced to something a pure-Python program could carry.

    Average pooled 4x4, so 32x32x2 becomes 8x8x2. That is 128 numbers, which
    is small enough that a matrix multiply against it is a few hundred thousand
    multiplies for a whole board and can be written in a loop.
    """
    n = x.shape[0]
    p = x.reshape(n, CH, N // POOL, POOL, N // POOL, POOL).mean(axis=(3, 5))
    return p.reshape(n, -1)


class Shallow:
    """One hidden layer over the pooled square, run forward in numpy here and
    in plain Python by tiny_forward()."""

    def __init__(self, w):
        self.w = {k: np.asarray(v, np.float32) for k, v in w.items()}

    def logits(self, x):
        z = small_features(x)
        h = np.maximum(z @ self.w["h1w"].T + self.w["h1b"], 0)
        return h @ self.w["h2w"].T + self.w["h2b"]

    @property
    def params(self):
        return int(sum(v.size for v in self.w.values()))


def _torch_shallow(hidden=32):
    import torch.nn as nn

    class S(nn.Module):
        def __init__(self):
            super().__init__()
            self.h1 = nn.Linear(SMALL, hidden)
            self.h2 = nn.Linear(hidden, 13)

        def forward(self, x):
            import torch
            n = x.shape[0]
            z = x.reshape(n, CH, N // POOL, POOL, N // POOL, POOL).mean(
                dim=(3, 5)).reshape(n, -1)
            return self.h2(torch.relu(self.h1(z)))

    return S()


def fit_shallow(data_dir, train_names, out_path, epochs=25, seed=0,
                val_names=(), hidden=32):
    import torch
    import torch.nn as nn
    torch.manual_seed(seed)
    # Four, not every core. Measured on this box: one batch takes 0.43s on one
    # thread, 0.17s on four and 9.9s on eight, which is a sixty-fold cliff, not
    # a slowdown. A 2x32x32 convolution is too small to feed that many workers
    # and they end up fighting over it.
    torch.set_num_threads(int(os.environ.get("TORCH_THREADS", 4)))
    X, y, _ = load_packs(data_dir, train_names)
    counts = np.bincount(y, minlength=13).astype(np.float32)
    weight = (counts.sum() / np.maximum(counts, 1)) ** 0.5
    weight /= weight.mean()
    model = _torch_shallow(hidden)
    opt = torch.optim.Adam(model.parameters(), lr=4e-3)
    lossf = nn.CrossEntropyLoss(weight=torch.tensor(weight))
    Xt, yt = torch.from_numpy(X), torch.from_numpy(y)
    n, bs = len(y), 512
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, epochs * (n // bs + 1))
    for ep in range(epochs):
        perm = torch.randperm(n)
        tot = 0.0
        for i in range(0, n, bs):
            idx = perm[i:i + bs]
            opt.zero_grad()
            loss = lossf(model(Xt[idx].float()), yt[idx])
            loss.backward()
            opt.step()
            sched.step()
            tot += float(loss) * len(idx)
        if ep % 5 == 4 or ep == epochs - 1:
            msg = "epoch %2d  loss %.4f" % (ep + 1, tot / n)
            if val_names:
                msg += "   " + val_report(model, data_dir, val_names)
            print(msg, flush=True)
    w = {"h1w": model.h1.weight.detach().numpy(),
         "h1b": model.h1.bias.detach().numpy(),
         "h2w": model.h2.weight.detach().numpy(),
         "h2b": model.h2.bias.detach().numpy()}
    np.savez_compressed(out_path, **w)
    print("wrote %s  %d params  %d bytes"
          % (out_path, sum(v.size for v in w.values()),
             os.path.getsize(out_path)))
    return w


class ShallowReader(LearnedReader):
    def __init__(self, weights, floor=0.0):
        if weights not in WEIGHTS:
            with np.load(weights) as z:
                WEIGHTS[weights] = Shallow({k: z[k] for k in z.files})
        self.net = WEIGHTS[weights]
        self.floor = floor
        self.use_margin = True


def shallow_factory(weights, floor=0.0):
    return lambda: ShallowReader(weights, floor)


# ------------------------------------------------- using the enrollment

class EnrolledReader(LearnedReader):
    """The same net, but allowed to look at the starting position first.

    This is what would actually ship. chesswatch already learns the set at the
    start of a game, so a model that has never seen the set still gets 64
    labelled squares of it for free. The net's last hidden layer is used as a
    description of a square and each piece keeps the average description of
    itself in this set; a square is then read by which description it is
    closest to, which needs no piece to have been in the training data at all.

    The class head is still there. Where the two agree the answer is kept;
    where they disagree the square is refused, which is the cheap half of the
    calibration.
    """

    def __init__(self, weights, floor=0.0, mix=0.5, gate=True):
        LearnedReader.__init__(self, weights, floor)
        self.mix = mix
        self.gate = gate
        self.proto = None
        self.last = None

    @staticmethod
    def _unit(h):
        return h / np.maximum(np.linalg.norm(h, axis=1, keepdims=True), 1e-6)

    def _embed(self, x):
        return self._unit(self.net.hidden(x))

    def learn(self, board_img, board, flipped=False):
        import watcher as W
        grid = W.grid_of(board, flipped)
        h = self._embed(features(board_img))
        proto = np.zeros((13, h.shape[1]), np.float32)
        seen = np.zeros(13, np.int32)
        for i in range(64):
            k = IDX[grid[i // 8][i % 8]]
            proto[k] += h[i]
            seen[k] += 1
        self.seen = seen > 0
        norm = np.linalg.norm(proto, axis=1, keepdims=True)
        self.proto = proto / np.maximum(norm, 1e-6)
        return bool(self.seen.sum() == 13)

    def classify(self, board_img):
        x = features(board_img)
        raw = self.net.hidden(x)
        p = softmax(self.net.head(raw))
        if self.proto is None:
            best = p.argmax(axis=1)
            conf = self.confidence(p)
            return [["?" if conf[r * 8 + c] < self.floor
                     else CLASSES[best[r * 8 + c]] for c in range(8)]
                    for r in range(8)]
        h = self._unit(raw)
        sim = h @ self.proto.T
        sim[:, ~self.seen] = -1.0
        # The gap between the best prototype and the runner up, in cosine, is
        # the confidence that actually means something here. A softmax over
        # twelve prototypes saturates: everything a set-specific template
        # matcher is sure about comes out at 1.0, so a floor on it is either
        # no floor at all or a floor that refuses the board.
        top = np.sort(sim, axis=1)
        self.last = top[:, -1] - top[:, -2]
        # A cosine similarity is not a probability, so it is turned into one
        # with the same softmax the head uses. The temperature is the only
        # fitted number here and it was picked on the validation sets.
        q = softmax(sim * 12.0)
        both = (1.0 - self.mix) * p + self.mix * q
        best = both.argmax(axis=1)
        agree = (p.argmax(axis=1) == q.argmax(axis=1))
        conf = self.last if self.mix >= 1.0 else self.confidence(both)
        rows = []
        for r in range(8):
            row = []
            for c in range(8):
                i = r * 8 + c
                bad = conf[i] < self.floor or (self.gate and not agree[i])
                row.append("?" if bad else CLASSES[best[i]])
            rows.append(row)
        return rows


def enrolled_factory(weights, floor=0.0, mix=0.5, gate=True):
    return lambda: EnrolledReader(weights, floor, mix, gate)


# ------------------------------------------------- what shipping would cost

RING8 = [i for i in range(64) if i // 8 in (0, 7) or i % 8 in (0, 7)]


def tiny_features(board_img):
    """features() and small_features() again, without numpy.

    Pillow is already a dependency of the program, so it does the two resizes:
    LANCZOS down to 256 is the same resample features() does, and BOX from 256
    to 64 is exactly the 4x4 average small_features() takes. Everything after
    that is arithmetic over 4096 integers and runs in plain Python.
    """
    im = board_img.convert("L").resize((256, 256), Image.LANCZOS).resize(
        (64, 64), Image.BOX)
    px = list(im.getdata())
    sq = []
    for r in range(8):
        for c in range(8):
            blk = []
            for y in range(8):
                o = (r * 8 + y) * 64 + c * 8
                blk.extend(px[o:o + 8])
            sq.append(blk)
    lit, drk = [], []
    for i, blk in enumerate(sq):
        (lit if ((i // 8) + (i % 8)) % 2 == 0 else drk).extend(
            blk[j] for j in RING8)
    lit.sort()
    drk.sort()
    light = lit[len(lit) // 2]
    dark = drk[len(drk) // 2]
    span = max(abs(light - dark), 40.0)
    mid = 0.5 * (light + dark)
    out = []
    for i, blk in enumerate(sq):
        base = light if ((i // 8) + (i % 8)) % 2 == 0 else dark
        v = [max(-4.0, min(4.0, (p - base) / span)) for p in blk]
        v += [max(-4.0, min(4.0, (p - mid) / span)) for p in blk]
        out.append(v)
    return out


def tiny_forward(feats, w):
    """One hidden layer, in a loop, over 64 squares."""
    h1w, h1b, h2w, h2b = w
    out = []
    for v in feats:
        h = []
        for row, b in zip(h1w, h1b):
            s = b
            for a, k in zip(v, row):
                s += a * k
            h.append(s if s > 0.0 else 0.0)
        z = []
        for row, b in zip(h2w, h2b):
            s = b
            for a, k in zip(h, row):
                s += a * k
            z.append(s)
        out.append(z)
    return out


def tiny_weights(path, bits=8):
    """The shallow weights as plain lists, optionally quantised.

    Quantised per output row: one scale each, which is the cheapest scheme that
    keeps a row with small weights from being flattened by a row with large
    ones."""
    with np.load(path) as z:
        w = {k: z[k].astype(np.float32) for k in z.files}
    out = []
    for k in ("h1w", "h1b", "h2w", "h2b"):
        a = w[k]
        if bits and a.ndim == 2:
            scale = np.abs(a).max(axis=1, keepdims=True) / (2 ** (bits - 1) - 1)
            scale = np.maximum(scale, 1e-9)
            a = np.round(a / scale) * scale
        out.append(a.tolist())
    return out


class TinyReader:
    """A bench entrant that touches numpy nowhere. Same weights as
    ShallowReader, so the two can be compared square by square."""

    def __init__(self, weights, floor=0.0, bits=8):
        key = ("tiny", weights, bits)
        if key not in WEIGHTS:
            WEIGHTS[key] = tiny_weights(weights, bits)
        self.w = WEIGHTS[key]
        self.floor = floor

    def learn(self, board_img, board, flipped=False):
        return True

    def classify(self, board_img):
        z = tiny_forward(tiny_features(board_img), self.w)
        rows = []
        for r in range(8):
            row = []
            for c in range(8):
                v = z[r * 8 + c]
                m = max(v)
                e = [2.718281828459045 ** (a - m) for a in v]
                s = sum(e)
                p = sorted(a / s for a in e)
                row.append("?" if (p[-1] - p[-2]) < self.floor
                           else CLASSES[v.index(m)])
            rows.append(row)
        return rows


def tiny_factory(weights, floor=0.0, bits=8):
    return lambda: TinyReader(weights, floor, bits)


def quantise(src, dst, bits=8):
    """Store the CNN as int8 plus one scale per output channel.

    What this measures is the size of the file that would have to ship, not the
    speed: the weights are turned back into floats when they are loaded, so
    inference is unchanged. Biases stay in float32, they are 141 numbers.
    """
    lim = 2 ** (bits - 1) - 1
    out = {}
    with np.load(src) as z:
        for k in z.files:
            a = z[k].astype(np.float32)
            if a.ndim == 1:
                out[k] = a
                continue
            flat = a.reshape(a.shape[0], -1)
            scale = np.maximum(np.abs(flat).max(axis=1), 1e-12) / lim
            out[k] = np.clip(np.round(flat / scale[:, None]), -lim - 1,
                             lim).astype(np.int8).reshape(a.shape)
            out[k + "_s"] = scale.astype(np.float32)
    np.savez_compressed(dst, **out)
    return os.path.getsize(dst)


def load_quantised(path):
    with np.load(path) as z:
        w = {}
        for k in z.files:
            if k.endswith("_s"):
                continue
            a = z[k]
            if a.dtype == np.int8:
                s = z[k + "_s"]
                a = a.reshape(a.shape[0], -1).astype(np.float32) * s[:, None]
                a = a.reshape(z[k].shape)
            w[k] = a.astype(np.float32)
    return Net(w)


def quantised_factory(path, floor=0.0, mix=1.0, gate=False):
    if ("q", path) not in WEIGHTS:
        WEIGHTS[("q", path)] = load_quantised(path)
    net = WEIGHTS[("q", path)]

    def make():
        e = EnrolledReader.__new__(EnrolledReader)
        e.net = net
        e.floor = floor
        e.use_margin = True
        e.mix = mix
        e.gate = gate
        e.proto = None
        e.last = None
        return e
    return make


# ---------------------------------------------------------------- the entrant

def entrant(weights=None, floor=0.05):
    """The one bench.score() should be handed.

    Prototypes from the enrollment over a held-out model's features, refusing
    a square where the best prototype beats the second by less than `floor` in
    cosine. That threshold was picked on five piece sets that are neither in
    the training corpus nor in the bench.
    """
    weights = weights or os.path.join(HERE, "learned_heldout_int8.npz")
    if weights.endswith("_int8.npz"):
        return quantised_factory(weights, floor)
    return enrolled_factory(weights, floor, mix=1.0, gate=False)


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "bench"
    if what == "bench":
        import bench
        rest = [a for a in sys.argv[2:] if not a.startswith("--")]
        floor = float(rest[0]) if rest else 0.05
        bench.score(entrant(floor=floor), quick="--quick" in sys.argv)
    elif what == "quantise":
        print(quantise(sys.argv[2], sys.argv[3]), "bytes")
    elif what == "time":
        import chess
        img = PngSet(png_sets()[0]).render(chess.Board(), 824)
        e = entrant()()
        e.learn(img, chess.Board(), False)
        e.classify(img)
        t = time.perf_counter()
        for _ in range(20):
            e.classify(img)
        print("%.1f ms for 64 squares" % ((time.perf_counter() - t) / 20 * 1000))
    else:
        print(__doc__)
