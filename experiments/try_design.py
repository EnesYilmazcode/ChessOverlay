"""Architecture experiments. Nothing here ships; it only measures.

Every number printed by this file comes from bench.py's corpus and bench.py's
ground truth. Right, wrong and unknown are always reported separately.

Subcommands are at the bottom of the file.
"""

import collections
import functools
import os
import sys
import time

import chess
from PIL import Image

import bench
import pieces as P
import setgen
import watcher as W

# ------------------------------------------------------------------ caching
# setgen.fonts() re-renders twelve glyphs for every font on the machine, and
# bench calls it once per corpus and once per teach. Memoised here so a dozen
# experiments cost what one does. Nothing about the results changes.

_orig_fonts = setgen.fonts
_orig_real = bench._real_sets


@functools.lru_cache(maxsize=None)
def _fonts_cached(limit=None):
    return tuple(_orig_fonts(limit))


@functools.lru_cache(maxsize=None)
def _real_cached():
    return _orig_real()


setgen.fonts = lambda limit=None: list(_fonts_cached(limit))
bench._real_sets = lambda: dict(_real_cached())

_READABLE = {}


def _readable_cached(path, size=824):
    if path not in _READABLE:
        _READABLE[path] = setgen._readable_orig(path, size)
    return _READABLE[path]


setgen._readable_orig = setgen.readable
setgen.readable = _readable_cached


# ------------------------------------------------------------------ corpus

def set_names():
    real = bench._real_sets()
    fonts = [f for f in setgen.fonts() if setgen.readable(f)]
    return sorted(real) + [os.path.basename(f).split(".")[0] for f in fonts]


def render_one(name, board, flipped, size):
    """One board picture, exactly the way bench.corpus makes it."""
    real = bench._real_sets()
    if name in real:
        img = real[name].render(board, flipped)
        if img.size[0] != size:
            img = img.resize((size, size), Image.LANCZOS)
        return img
    fonts = [f for f in setgen.fonts() if setgen.readable(f)]
    font = next(f for f in fonts if os.path.basename(f).startswith(name))
    return setgen.render(board, font, size, flipped)


class Tally:
    """Per-square outcomes, kept so they can be cut along any axis afterwards."""

    def __init__(self):
        self.rows = []          # (name, flipped, pos, kind, size, right, wrong, unk)
        self.confusion = collections.Counter()

    def add(self, key, got_rows, want_rows):
        r = w = u = 0
        for i in range(8):
            for j in range(8):
                got, want = got_rows[i][j], want_rows[i][j]
                if got == "?":
                    u += 1
                    self.confusion[(want, "?")] += 1
                elif got == want:
                    r += 1
                else:
                    w += 1
                    self.confusion[(want, got)] += 1
        self.rows.append(key + (r, w, u))
        return r, w, u

    def totals(self):
        r = sum(x[5] for x in self.rows)
        w = sum(x[6] for x in self.rows)
        u = sum(x[7] for x in self.rows)
        return r, w, u

    def line(self, label):
        r, w, u = self.totals()
        n = max(r + w + u, 1)
        return ("%-34s %6d sq   right %6.2f%%   WRONG %5.2f%%   unknown %6.2f%%"
                % (label, n, 100.0 * r / n, 100.0 * w / n, 100.0 * u / n))

    def cut(self, idx):
        """right/wrong/unknown grouped by one axis of the key."""
        out = collections.defaultdict(lambda: [0, 0, 0])
        for row in self.rows:
            cell = out[row[idx]]
            cell[0] += row[5]; cell[1] += row[6]; cell[2] += row[7]
        return dict(out)


AXES = ("set", "side", "position", "variant", "size")


def show_cuts(t, axes=(0, 3, 4, 2, 1)):
    for idx in axes:
        print("\n  by %s" % AXES[idx])
        for key, (r, w, u) in sorted(cut_sorted(t.cut(idx))):
            n = max(r + w + u, 1)
            print("    %-12s right %6.2f%%   WRONG %5.2f%%   unknown %6.2f%%"
                  % (key, 100.0 * r / n, 100.0 * w / n, 100.0 * u / n))


def cut_sorted(d):
    return [(k, v) for k, v in sorted(d.items(), key=lambda kv: str(kv[0]))]


# --------------------------------------------------------------- the runner

def run(make, teach_with=None, quick=True, tally=None, sizes=None,
        variants=None):
    """Score one entrant over bench's corpus.

    teach_with(name) -> the set name to teach this entrant from, or None to
    skip teaching entirely. Default is to teach from the set being read, which
    is what bench.score does.
    """
    t = tally or Tally()
    taught = {}
    kw = {"quick": quick}
    if sizes:
        kw["sizes"] = sizes
    if variants:
        kw["variants"] = variants
    for name, flipped, pos, kind, size, img, board in bench.corpus(**kw):
        key = (name, flipped)
        if key not in taught:
            e = make()
            if teach_with is not None:
                src = teach_with(name)
                if src is not None:
                    bench.teach(e, src, flipped)
            taught[key] = e
        entrant = taught[key]
        want = W.grid_of(board, flipped)
        rows = entrant.classify(img)
        if isinstance(rows, tuple):
            rows = rows[0]
        t.add((name, "black" if flipped else "white", pos, kind, size),
              rows, want)
    return t


def teach_self(name):
    return name


def teach_other(name):
    names = set_names()
    return names[(names.index(name) + 1) % len(names)]


class Bundled:
    """The reader with teaching switched off. Never leaves the shipped sheet."""

    def __init__(self):
        self.r = P.PieceReader()

    def learn(self, board_img, board, flipped=False):
        return False

    def classify(self, board_img):
        return self.r.classify(board_img)


class Taught:
    """The reader exactly as it ships."""

    def __init__(self):
        self.r = P.PieceReader()

    def learn(self, board_img, board, flipped=False):
        return self.r.learn(board_img, board, flipped)

    def classify(self, board_img):
        return self.r.classify(board_img)


# ------------------------------------------------------------ experiment 1

def exp_teaching(quick=True):
    print("=" * 78)
    print("1. DOES TEACHING HELP OR HURT")
    print("=" * 78)
    runs = [
        ("taught from the set being read", Taught, teach_self),
        ("never taught, bundled sheet only", Bundled, None),
        ("taught from a DIFFERENT set", Taught, teach_other),
    ]
    out = {}
    for label, make, tw in runs:
        started = time.time()
        t = run(make, tw, quick=quick)
        out[label] = t
        print(t.line(label) + "   [%.0fs]" % (time.time() - started))
    for label, t in out.items():
        print("\n--- %s" % label)
        show_cuts(t, axes=(0, 3))
    return out




# ----------------------------------------------------------- experiment 1b

def _per_square(make, teach_with, quick=True):
    """Every square's outcome, keyed so two runs can be compared square by
    square. Outcome is 'right', 'wrong' or 'unknown'."""
    out = {}
    taught = {}
    for name, flipped, pos, kind, size, img, board in bench.corpus(quick=quick):
        key = (name, flipped)
        if key not in taught:
            e = make()
            if teach_with is not None:
                src = teach_with(name)
                if src is not None:
                    taught[key] = (e, bench.teach(e, src, flipped))
                else:
                    taught[key] = (e, None)
            else:
                taught[key] = (e, None)
        entrant, learned = taught[key]
        want = W.grid_of(board, flipped)
        rows = entrant.classify(img)
        if isinstance(rows, tuple):
            rows = rows[0]
        for r in range(8):
            for c in range(8):
                got = rows[r][c]
                res = ("unknown" if got == "?"
                       else "right" if got == want[r][c] else "wrong")
                out[(name, flipped, pos, kind, size, r, c)] = res
    return out, {k: v[1] for k, v in taught.items()}


def exp_teachmatrix(quick=True):
    print("=" * 78)
    print("1b. CROSS-TEACHING MATRIX: taught from set A, reading set B")
    print("=" * 78)
    names = set_names()
    grid = {}
    # bundled row
    t = run(Bundled, None, quick=quick)
    for k, (r, w, u) in cut_sorted(t.cut(0)):
        grid[("<none>", k)] = (r, w, u)
    for src in names:
        t = run(Taught, lambda _n, s=src: s, quick=quick)
        for k, (r, w, u) in cut_sorted(t.cut(0)):
            grid[(src, k)] = (r, w, u)
    print("\n  rows = set taught from, cols = set read.  right%% / WRONG%%")
    print("  %-10s" % "taught" + "".join("%-18s" % n for n in names))
    for src in ["<none>"] + names:
        line = "  %-10s" % src
        for dst in names:
            r, w, u = grid[(src, dst)]
            n = max(r + w + u, 1)
            line += "%-18s" % ("%.1f / %.2f" % (100.0 * r / n, 100.0 * w / n))
        print(line)
    return grid


def exp_paired(quick=True):
    print("=" * 78)
    print("1c. SQUARE BY SQUARE: what teaching changes, and in which direction")
    print("=" * 78)
    base, _ = _per_square(Bundled, None, quick=quick)
    good, learned = _per_square(Taught, teach_self, quick=quick)
    bad, _ = _per_square(Taught, teach_other, quick=quick)
    print("\n  learn() returned all-twelve for: %s"
          % sorted(k for k, v in learned.items() if v))
    for label, other in (("teaching from the same set", good),
                         ("teaching from a different set", bad)):
        move = collections.Counter()
        for k, was in base.items():
            move[(was, other[k])] += 1
        print("\n  %s, against never teaching:" % label)
        for (was, now), n in sorted(move.items(), key=lambda kv: -kv[1]):
            flag = ""
            if was in ("right",) and now == "wrong":
                flag = "   <-- teaching CREATED a wrong answer"
            elif was == "wrong" and now == "right":
                flag = "   <-- teaching FIXED a wrong answer"
            elif was == "unknown" and now == "wrong":
                flag = "   <-- teaching turned a safe blank into a wrong answer"
            print("    %-8s -> %-8s %6d%s" % (was, now, n, flag))
        # where did the new wrongs land
        newly = [k for k, was in base.items()
                 if was != "wrong" and other[k] == "wrong"]
        if newly:
            byset = collections.Counter((k[0], k[3]) for k in newly)
            print("    new wrongs by (set, variant):",
                  dict(sorted(byset.items())))



# ------------------------------------------------------------ experiment 4

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "try_design_full.json")


def full_tally(rebuild=False):
    """The shipped reader over the WHOLE corpus: 4 sizes, 8 variants, both
    sides, 4 positions, 4 sets. Cached to disk because it is a few minutes."""
    import json
    t = Tally()
    if not rebuild and os.path.exists(CACHE):
        body = json.load(open(CACHE))
        t.rows = [tuple(r) for r in body["rows"]]
        t.confusion = collections.Counter(
            {tuple(k.split("|")): v for k, v in body["confusion"].items()})
        return t
    started = time.time()
    t = run(Taught, teach_self, quick=False)
    print("  full corpus took %.0fs" % (time.time() - started))
    json.dump({"rows": t.rows,
               "confusion": {"|".join(k): v for k, v in t.confusion.items()}},
              open(CACHE, "w"))
    return t


def _pct(sel):
    r = sum(x[5] for x in sel)
    w = sum(x[6] for x in sel)
    u = sum(x[7] for x in sel)
    n = max(r + w + u, 1)
    return 100.0 * r / n, 100.0 * w / n, 100.0 * u / n


def exp_attribute(quick=True):
    print("=" * 78)
    print("4. WHERE THE LOSS ACTUALLY IS: set vs size vs sharpness vs position")
    print("=" * 78)
    t = full_tally()
    print()
    print(t.line("whole corpus, shipped reader"))
    show_cuts(t, axes=(0, 4, 3, 2, 1))

    r, w, u = t.totals()
    n = r + w + u
    print()
    print("  spread each axis owns (best bucket minus worst, in right-points)")
    for idx in (0, 4, 3, 2, 1):
        cells = t.cut(idx)
        rate = {k: 100.0 * v[0] / max(sum(v), 1) for k, v in cells.items()}
        best = max(rate, key=rate.get)
        worst = min(rate, key=rate.get)
        print("    %-9s best %-9s %6.2f%%   worst %-9s %6.2f%%   spread %6.2f"
              % (AXES[idx], best, rate[best], worst, rate[worst],
                 rate[best] - rate[worst]))

    print()
    print("  right%, set (down) against variant (across), all sizes")
    sets = sorted({row[0] for row in t.rows})
    kinds = sorted({row[3] for row in t.rows})
    print("    %-10s" % "" + "".join("%-11s" % k for k in kinds))
    for s_ in sets:
        line = "    %-10s" % s_
        for k in kinds:
            line += "%-11s" % ("%.1f" % _pct([x for x in t.rows
                                              if x[0] == s_ and x[3] == k])[0])
        print(line)

    print()
    print("  right%, set (down) against size (across), all variants")
    sizes = sorted({row[4] for row in t.rows})
    print("    %-10s" % "" + "".join("%-11s" % z for z in sizes))
    for s_ in sets:
        line = "    %-10s" % s_
        for z in sizes:
            line += "%-11s" % ("%.1f" % _pct([x for x in t.rows
                                              if x[0] == s_ and x[4] == z])[0])
        print(line)

    print()
    print("  WRONG%, set (down) against variant (across)")
    print("    %-10s" % "" + "".join("%-11s" % k for k in kinds))
    for s_ in sets:
        line = "    %-10s" % s_
        for k in kinds:
            line += "%-11s" % ("%.2f" % _pct([x for x in t.rows
                                              if x[0] == s_ and x[3] == k])[1])
        print(line)

    print()
    print("  the wrong answers, most common first (truth -> said)")
    for (want, got), c in t.confusion.most_common(24):
        if got == "?":
            continue
        print("    %s -> %s   %d" % (want, got, c))
    return t


# ------------------------------------------------------------ experiment 3

def exp_board(quick=True):
    print("=" * 78)
    print("3. ALL 64 OR NOTHING: how often does one square block a whole board")
    print("=" * 78)
    t = full_tally()
    nb = len(t.rows)
    perfect = [x for x in t.rows if x[6] == 0 and x[7] == 0]
    clean_blank = [x for x in t.rows if x[6] == 0 and x[7] > 0]
    has_wrong = [x for x in t.rows if x[6] > 0]
    wrong_no_blank = [x for x in t.rows if x[6] > 0 and x[7] == 0]
    print()
    print("  %d boards" % nb)
    print("    %5d (%5.1f%%) all 64 right          check() accepts, correctly"
          % (len(perfect), 100.0 * len(perfect) / nb))
    print("    %5d (%5.1f%%) 0 wrong, >=1 blank    check() REFUSES a board that was otherwise perfect"
          % (len(clean_blank), 100.0 * len(clean_blank) / nb))
    print("    %5d (%5.1f%%) >=1 wrong             a fabricated piece is on the board somewhere"
          % (len(has_wrong), 100.0 * len(has_wrong) / nb))
    print("    %5d (%5.1f%%) >=1 wrong, 0 blank    check() ACCEPTS a fabricated board TODAY"
          % (len(wrong_no_blank), 100.0 * len(wrong_no_blank) / nb))

    print()
    print("  refused boards by blank count (0 wrong), and what relaxing buys")
    hist = collections.Counter(x[7] for x in clean_blank)
    running = 0
    for k in sorted(hist)[:16]:
        running += hist[k]
        print("    %2d blank  %5d boards   relaxing to <=%-2d recovers %5.1f%% of all boards"
              % (k, hist[k], k, 100.0 * running / nb))

    print()
    print("  and what relaxing COSTS: P(board also holds a wrong piece | k blank)")
    by_k = collections.defaultdict(lambda: [0, 0])
    for x in t.rows:
        cell = by_k[x[7]]
        cell[0] += 1
        if x[6] > 0:
            cell[1] += 1
    for k in sorted(by_k)[:16]:
        tot, bad = by_k[k]
        print("    %2d blank  %5d boards   %5.1f%% of them ALSO hold a wrong piece"
              % (k, tot, 100.0 * bad / max(tot, 1)))

    print()
    print("  cumulative: accept every board with <= k blanks, 8 sharp variants only")
    sharp = [x for x in t.rows if x[3] not in ("blur",)]
    for k in (0, 1, 2, 3, 5, 8):
        sel = [x for x in sharp if x[7] <= k]
        bad = [x for x in sel if x[6] > 0]
        print("    <=%2d blank   accepts %5d of %5d boards (%5.1f%%)   of those, %4d (%5.2f%%) hold a wrong piece"
              % (k, len(sel), len(sharp), 100.0 * len(sel) / len(sharp),
                 len(bad), 100.0 * len(bad) / max(len(sel), 1)))
    print()
    print("  same, blurred captures only")
    soft = [x for x in t.rows if x[3] == "blur"]
    for k in (0, 1, 2, 3, 5, 8):
        sel = [x for x in soft if x[7] <= k]
        bad = [x for x in sel if x[6] > 0]
        print("    <=%2d blank   accepts %5d of %5d boards (%5.1f%%)   of those, %4d (%5.2f%%) hold a wrong piece"
              % (k, len(sel), len(soft), 100.0 * len(sel) / len(soft),
                 len(bad), 100.0 * len(bad) / max(len(sel), 1)))
    return t



# ------------------------------------------------------------ experiment 2/5
# The detail run keeps what the reader actually said, not just the counts, so
# a whole-board rule can be tried on top of it afterwards.

DETAIL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "try_design_detail.json")


def detail(rebuild=False):
    import json
    if not rebuild and os.path.exists(DETAIL):
        return json.load(open(DETAIL))
    out = []
    taught = {}
    started = time.time()
    for name, flipped, pos, kind, size, img, board in bench.corpus(quick=False):
        key = (name, flipped)
        if key not in taught:
            e = P.PieceReader()
            bench.teach(e, name, flipped)
            taught[key] = e
        rows, weakest = taught[key].classify(img)
        out.append({"set": name, "flipped": flipped, "pos": pos, "kind": kind,
                    "size": size,
                    "got": ["".join(r) for r in rows],
                    "want": ["".join(r) for r in W.grid_of(board, flipped)],
                    "weakest": weakest,
                    "fen": board.fen()})
    print("  detail run took %.0fs" % (time.time() - started))
    json.dump(out, open(DETAIL, "w"))
    return out


def _counts(rec):
    r = w = u = 0
    for gi, wi in zip(rec["got"], rec["want"]):
        for g, x in zip(gi, wi):
            if g == "?":
                u += 1
            elif g == x:
                r += 1
            else:
                w += 1
    return r, w, u


def _legal(rec):
    """Is what the reader said a position the rules of chess allow at all?"""
    rows = [list(r) for r in rec["got"]]
    if any("?" in r for r in rows):
        return None
    for turn in (chess.WHITE, chess.BLACK):
        if W.board_from_grid(rows, rec["flipped"], turn) is not None:
            return True
    return False


def exp_legality(quick=True):
    print("=" * 78)
    print("2a/3. IS THE WHOLE-BOARD READING EVEN A LEGAL CHESS POSITION")
    print("=" * 78)
    recs = detail()
    zero_blank = [r for r in recs if _counts(r)[2] == 0]
    good = [r for r in zero_blank if _counts(r)[1] == 0]
    bad = [r for r in zero_blank if _counts(r)[1] > 0]
    print()
    print("  of %d boards, %d come back with no unreadable square at all"
          % (len(recs), len(zero_blank)))
    print("    %d of those are entirely correct" % len(good))
    print("    %d hold at least one fabricated piece and check() accepts them today"
          % len(bad))
    gl = sum(1 for r in good if _legal(r))
    bl = sum(1 for r in bad if _legal(r))
    print()
    print("  adding 'and it must be a legal chess position':")
    print("    correct boards still accepted   %4d of %4d  (%5.1f%%)"
          % (gl, len(good), 100.0 * gl / max(len(good), 1)))
    print("    fabricated boards still accepted %4d of %4d  (%5.1f%%)  -- %d caught"
          % (bl, len(bad), 100.0 * bl / max(len(bad), 1), len(bad) - bl))
    print()
    print("  the fabricated boards legality did NOT catch:")
    for r in bad:
        if _legal(r):
            rr, ww, uu = _counts(r)
            diff = [(i, j, r["want"][i][j], r["got"][i][j])
                    for i in range(8) for j in range(8)
                    if r["want"][i][j] != r["got"][i][j]]
            print("    %-9s %-5s %-11s %-9s %4dpx  %d wrong: %s"
                  % (r["set"], "black" if r["flipped"] else "white", r["pos"],
                     r["kind"], r["size"], ww,
                     " ".join("%s->%s" % (a, b) for _, _, a, b in diff[:6])))

    print()
    print("  and how the weakest match score separates them")
    for label, sel in (("correct, 0 blank", good), ("fabricated, 0 blank", bad)):
        ws = sorted(r["weakest"] for r in sel)
        if not ws:
            continue
        print("    %-22s n=%3d  min %.3f  p10 %.3f  median %.3f  max %.3f"
              % (label, len(ws), ws[0], ws[len(ws) // 10], ws[len(ws) // 2],
                 ws[-1]))
    # can any single threshold on weakest separate them?
    best = None
    for cut in [x / 200.0 for x in range(0, 200)]:
        kept = sum(1 for r in good if r["weakest"] >= cut)
        let = sum(1 for r in bad if r["weakest"] >= cut)
        if best is None or (let, -kept) < (best[1], -best[2]):
            best = (cut, let, kept)
    print("    best single cut on weakest: >= %.3f keeps %d/%d correct boards "
          "and still lets %d/%d fabricated boards through"
          % (best[0], best[2], len(good), best[1], len(bad)))
    return recs


def exp_errors(quick=True):
    print("=" * 78)
    print("4b. WHAT KIND OF MISTAKE IS IT: colour, type, or presence")
    print("=" * 78)
    t = full_tally()
    kinds = collections.Counter()
    for (want, got), c in t.confusion.items():
        if got == "?":
            kinds["unreadable"] += c
        elif want == ".":
            kinds["empty square given a piece"] += c
        elif got == ".":
            kinds["piece read as an empty square"] += c
        elif want.lower() == got.lower():
            kinds["right piece, WRONG COLOUR"] += c
        else:
            kinds["wrong piece type"] += c
    total_bad = sum(v for k, v in kinds.items() if k != "unreadable")
    print()
    print("  %d wrong answers in the whole corpus" % total_bad)
    for k, v in kinds.most_common():
        if k == "unreadable":
            continue
        print("    %-32s %6d   %5.1f%% of all wrong answers"
              % (k, v, 100.0 * v / max(total_bad, 1)))
    print()
    print("  %d unreadable squares, by what was really there" % kinds["unreadable"])
    blanks = collections.Counter({w: c for (w, g), c in t.confusion.items()
                                  if g == "?"})
    for w, c in blanks.most_common():
        print("    %s  %6d" % (w, c))
    return t


def exp_lookup(quick=True):
    print("=" * 78)
    print("5. IS IT A LOOKUP? are the same piece's pixels identical every time")
    print("=" * 78)
    names = set_names()
    for name in names:
        for size in (824, 400):
            a = render_one(name, bench.board_of("opening"), False, size)
            b = render_one(name, bench.board_of("developed"), False, size)
            # a1..h1 in both: pieces that have not moved between the two
            same = diff = 0
            ga = W.grid_of(bench.board_of("opening"), False)
            gb = W.grid_of(bench.board_of("developed"), False)
            step = size // 8
            for r in range(8):
                for c in range(8):
                    if ga[r][c] == "." or ga[r][c] != gb[r][c]:
                        continue
                    box = (c * step, r * step, (c + 1) * step, (r + 1) * step)
                    if a.crop(box).tobytes() == b.crop(box).tobytes():
                        same += 1
                    else:
                        diff += 1
            print("  %-9s %4dpx   %3d squares hold the same piece in both "
                  "positions: %3d pixel-identical, %3d not"
                  % (name, size, same + diff, same, diff))

    print()
    print("  the same piece across the two square colours, and across sizes")
    for name in names:
        big = render_one(name, chess.Board(), False, 824)
        small = render_one(name, chess.Board(), False, 400)
        step = 824 // 8
        a = big.crop((0, 0, step, step))          # a8, dark rook, black
        b = big.crop((step, 0, 2 * step, step))   # b8, light knight
        c = big.crop((2 * step, 0, 3 * step, step))
        # a8 and c8 are both rooks? no: a8 rook, c8 bishop. compare a8 vs h8.
        h = big.crop((7 * step, 0, 8 * step, step))   # h8 rook, light square
        print("  %-9s a8 rook vs h8 rook (dark square vs light): %s"
              % (name, "identical" if a.tobytes() == h.tobytes() else "DIFFERENT"))
        s2 = small.resize((824, 824), Image.LANCZOS)
        print("  %-9s the same rook at 824px vs upscaled from 400px: %s"
              % (name, "identical"
                 if big.crop((0, 0, step, step)).tobytes()
                 == s2.crop((0, 0, step, step)).tobytes() else "DIFFERENT"))
        del b, c



# ------------------------------------------------------------ experiment 2b/3
# A game followed move by move, which is what the program actually does, rather
# than a stack of unrelated boards read cold.

GAME = ["d4", "Nf6", "c4", "e6", "Nc3", "Bb4", "e3", "O-O", "Bd3", "d5",
        "Nf3", "c5", "O-O", "Nc6", "a3", "Bxc3", "bxc3", "dxc4", "Bxc4", "Qc7"]


def game_plies():
    b = chess.Board()
    out = [b.copy()]
    for san in GAME:
        b.push_san(san)
        out.append(b.copy())
    return out


def _wildcard_match(grid, read):
    """Does this candidate position agree with the reading everywhere the
    reading actually said something? '?' matches anything; a named piece must
    match exactly. Nothing is ever invented: the candidate comes from playing
    legal moves, so every square of it is a position the rules produced."""
    for i in range(8):
        for j in range(8):
            if read[i][j] != "?" and read[i][j] != grid[i][j]:
                return False
    return True


_CAND = {}


def _candidates(board, flipped, depth=2):
    """Every position reachable from here in 0..depth legal moves, as the grid
    it would put on screen. Cached, because the same handful of believed
    positions is asked about once per set, size and capture quality."""
    key = (board.board_fen(), board.turn, flipped, depth)
    if key in _CAND:
        return _CAND[key]
    seen = {board.board_fen(): board.copy()}
    frontier = [board.copy()]
    for _ in range(depth):
        nxt = []
        for b in frontier:
            for mv in b.legal_moves:
                nb = b.copy()
                nb.push(mv)
                if nb.board_fen() in seen:
                    continue
                seen[nb.board_fen()] = nb
                nxt.append(nb)
        frontier = nxt
    out = [(b, W.grid_of(b, flipped)) for b in seen.values()]
    _CAND[key] = out
    return out


def _changed_squares(prev_img, img, size, tol=6.0):
    """Which squares' pixels moved between two frames. Differenced whole and
    measured per square, because the same thing done pixel by pixel in Python
    costs more than the read it is meant to save."""
    from PIL import ImageChops
    step = size // 8
    d = ImageChops.difference(prev_img.convert("L"), img.convert("L"))
    out = []
    for r in range(8):
        for c in range(8):
            box = (c * step, r * step, (c + 1) * step, (r + 1) * step)
            if d.crop(box).resize((1, 1), Image.BOX).get_flattened_data()[0] > tol:
                out.append((r, c))
    return out


def exp_follow(quick=True):
    print("=" * 78)
    print("2b/3. FOLLOWING A GAME: all-64 refusal vs a legality-filled read")
    print("=" * 78)
    plies = game_plies()
    sets = set_names()
    sizes = (824, 400)
    kinds = ("plain", "small", "blur")
    stats = collections.defaultdict(lambda: collections.Counter())
    changed_hist = collections.Counter()
    for name in sets:
        for flipped in (False, True):
            reader = P.PieceReader()
            bench.teach(reader, name, flipped)
            for size in sizes:
                for kind in kinds:
                    tag = (kind,)
                    imgs = [setgen.variants(render_one(name, b, flipped, size),
                                            kind) for b in plies]
                    # how much of the board actually moves between frames
                    for i in range(1, len(imgs)):
                        changed_hist[len(_changed_squares(
                            imgs[i - 1], imgs[i], size))] += 1

                    # --- rule A: what ships. All 64 or nothing.
                    believed = plies[0]
                    for i in range(1, len(plies)):
                        rows, _ = reader.classify(
                            imgs[i], W.grid_of(believed, flipped))
                        s = stats[("A all-64", kind)]
                        if any("?" in r for r in rows):
                            s["refused"] += 1
                            continue
                        cands = [b for b, g in
                                 _candidates(believed, flipped, 2)
                                 if g == rows]
                        if len(cands) == 1:
                            believed = cands[0]
                            s["followed" if believed.board_fen()
                              == plies[i].board_fen() else "FABRICATED"] += 1
                        else:
                            s["refused"] += 1
                        if believed.board_fen() != plies[i].board_fen():
                            believed = plies[i]      # re-sync so one slip does
                                                     # not poison the rest
                    # --- rule B: '?' is a wildcard, and the answer has to be
                    # the ONE legal continuation that fits the squares we read.
                    believed = plies[0]
                    for i in range(1, len(plies)):
                        rows, _ = reader.classify(
                            imgs[i], W.grid_of(believed, flipped))
                        s = stats[("B wildcard", kind)]
                        cands = [b for b, g in
                                 _candidates(believed, flipped, 2)
                                 if _wildcard_match(g, rows)]
                        if len(cands) == 1:
                            believed = cands[0]
                            s["followed" if believed.board_fen()
                              == plies[i].board_fen() else "FABRICATED"] += 1
                        else:
                            s["refused"] += 1
                        if believed.board_fen() != plies[i].board_fen():
                            believed = plies[i]
                    # --- rule C: the tempting one. Fill any '?' from what we
                    # already believe, if there are few enough of them.
                    for limit in (3,):
                        believed = plies[0]
                        for i in range(1, len(plies)):
                            rows, _ = reader.classify(
                                imgs[i], W.grid_of(believed, flipped))
                            s = stats[("C fill<=%d" % limit, kind)]
                            blanks = sum(r.count("?") for r in rows)
                            if blanks > limit:
                                s["refused"] += 1
                                continue
                            held = W.grid_of(believed, flipped)
                            rows = [[held[i2][j] if rows[i2][j] == "?"
                                     else rows[i2][j] for j in range(8)]
                                    for i2 in range(8)]
                            cands = [b for b, g in
                                     _candidates(believed, flipped, 2)
                                     if g == rows]
                            if len(cands) == 1:
                                believed = cands[0]
                                s["followed" if believed.board_fen()
                                  == plies[i].board_fen() else "FABRICATED"] += 1
                            else:
                                s["refused"] += 1
                            if believed.board_fen() != plies[i].board_fen():
                                believed = plies[i]
    print()
    print("  %d plies followed per rule per capture quality, over 4 sets, both"
          " sides, 824px and 400px" % (len(plies) - 1))
    print()
    print("  %-14s %-9s %8s %9s %12s" % ("rule", "capture", "followed",
                                         "refused", "FABRICATED"))
    for (rule, kind), s in sorted(stats.items()):
        n = sum(s.values())
        print("  %-14s %-9s %7d%%  %8d%%  %11d%%   (n=%d, %d fabricated)"
              % (rule, kind, round(100.0 * s["followed"] / n),
                 round(100.0 * s["refused"] / n),
                 round(100.0 * s["FABRICATED"] / n), n, s["FABRICATED"]))
    print()
    print("  totals")
    for rule in sorted({k[0] for k in stats}):
        s = collections.Counter()
        for (r, k), v in stats.items():
            if r == rule:
                s.update(v)
        n = sum(s.values())
        print("    %-14s followed %5.1f%%   refused %5.1f%%   FABRICATED %5.2f%% (%d moves)"
              % (rule, 100.0 * s["followed"] / n, 100.0 * s["refused"] / n,
                 100.0 * s["FABRICATED"] / n, s["FABRICATED"]))

    print()
    print("  how many of the 64 squares actually change between two frames")
    tot = sum(changed_hist.values())
    run = 0
    for k in sorted(changed_hist):
        run += changed_hist[k]
        print("    %2d squares changed   %5d frames  (%5.1f%% cumulative)"
              % (k, changed_hist[k], 100.0 * run / tot))
        if run / tot > 0.995:
            break



# ------------------------------------------------------------ experiment 5b
# The smallest reader I can write that still has the same interface. It exists
# to price the shipped reader's machinery, not to replace it.
#
# One idea only: shrink the square to a fixed grid, subtract its own mean and
# divide by its own spread, and correlate against templates treated the same
# way. Brightness and contrast cancel by construction, and size cancels because
# everything is compared at one grid. No levels, no ink layers, no descriptors,
# no Euler number, no top-edge profile, no trust signature.

import math

GRID = 32


def _patch(square_img, grid=GRID):
    """A square as a zero-mean unit-spread vector of grid*grid numbers."""
    px = list(square_img.convert("L").resize((grid, grid),
                                             Image.BILINEAR).get_flattened_data())
    n = float(len(px))
    mean = sum(px) / n
    dev = [v - mean for v in px]
    norm = math.sqrt(sum(d * d for d in dev))
    if norm < 1e-6:
        return None, mean, 0.0
    return [d / norm for d in dev], mean, norm / math.sqrt(n)


def _corr(a, b):
    return sum(x * y for x, y in zip(a, b))


class Tiny:
    """learn/classify in about sixty lines."""

    FLOOR = 0.55        # below this correlation, say nothing
    MARGIN = 0.04       # and the runner-up of another piece type must be this
                        # far behind
    FLAT = 4.0          # a square whose pixels barely move is an empty one

    def __init__(self, floor=None, margin=None):
        self.templates = {}          # (symbol, light) -> vector
        self.ready = False
        if floor is not None:
            self.FLOOR = floor
        if margin is not None:
            self.MARGIN = margin

    def learn(self, board_img, board, flipped=False):
        grid = W.grid_of(board, flipped)
        size = board_img.size[0]
        step = size / 8.0
        found = {}
        for r in range(8):
            for c in range(8):
                symbol = grid[r][c]
                if symbol == ".":
                    continue
                box = (int(c * step), int(r * step),
                       int((c + 1) * step), int((r + 1) * step))
                vec, _, spread = _patch(board_img.crop(box))
                if vec is None:
                    continue
                key = (symbol, (r + c) % 2 == 0)
                found.setdefault(key, vec)
        if not found:
            return False
        self.templates = found
        self.ready = True
        return len({k[0] for k in found}) == 12

    def classify(self, board_img):
        rows = [["."] * 8 for _ in range(8)]
        if not self.ready:
            return rows
        size = board_img.size[0]
        step = size / 8.0
        for r in range(8):
            for c in range(8):
                box = (int(c * step), int(r * step),
                       int((c + 1) * step), int((r + 1) * step))
                vec, _, spread = _patch(board_img.crop(box))
                if vec is None or spread < self.FLAT:
                    continue            # flat square, so empty
                light = (r + c) % 2 == 0
                scored = {}
                for (symbol, tlight), tvec in self.templates.items():
                    s = _corr(vec, tvec)
                    # a template cut from the other square colour still counts,
                    # it is just usually worse, so take the best of the two
                    if s > scored.get(symbol, -2.0):
                        scored[symbol] = s
                if not scored:
                    continue
                ranked = sorted(((v, k) for k, v in scored.items()),
                                reverse=True)
                best_s, best = ranked[0]
                rival = next((v for v, k in ranked[1:]
                              if k.lower() != best.lower()), -1.0)
                if best_s < self.FLOOR or best_s - rival < self.MARGIN:
                    rows[r][c] = "?"
                else:
                    rows[r][c] = best
        return rows


def exp_tiny(quick=True):
    print("=" * 78)
    print("5b. PRICING THE MACHINERY: sixty lines of correlation vs the reader")
    print("=" * 78)
    t = run(Taught, teach_self, quick=quick)
    print()
    print(t.line("pieces.py as it ships"))
    for floor, margin in ((0.55, 0.04), (0.65, 0.06), (0.75, 0.10)):
        tt = run(lambda f=floor, m=margin: Tiny(f, m), teach_self, quick=quick)
        print(tt.line("Tiny, floor %.2f margin %.2f" % (floor, margin)))
    print()
    print("--- pieces.py")
    show_cuts(t, axes=(0, 3))
    tt = run(lambda: Tiny(0.65, 0.06), teach_self, quick=quick)
    print()
    print("--- Tiny at 0.65 / 0.06")
    show_cuts(tt, axes=(0, 3))
    return t, tt


def exp_tinyfull(quick=False):
    print("=" * 78)
    print("5c. THE SAME TWO OVER THE WHOLE CORPUS")
    print("=" * 78)
    t = full_tally()
    print()
    print(t.line("pieces.py as it ships"))
    for floor, margin in ((0.65, 0.06), (0.72, 0.08)):
        started = time.time()
        tt = run(lambda f=floor, m=margin: Tiny(f, m), teach_self, quick=False)
        print(tt.line("Tiny, floor %.2f margin %.2f" % (floor, margin))
              + "  [%.0fs]" % (time.time() - started))
        print()
        print("--- Tiny %.2f / %.2f" % (floor, margin))
        show_cuts(tt, axes=(0, 4, 3))
        # board level, for the all-64 rule
        nb = len(tt.rows)
        perfect = sum(1 for x in tt.rows if x[6] == 0 and x[7] == 0)
        wrongfree = sum(1 for x in tt.rows if x[6] == 0)
        accepted_bad = sum(1 for x in tt.rows if x[6] > 0 and x[7] == 0)
        print()
        print("    boards: %d all-64-right (%.1f%%), %d hold no wrong piece "
              "(%.1f%%), %d accepted by check() while fabricating (%.1f%%)"
              % (perfect, 100.0 * perfect / nb, wrongfree,
                 100.0 * wrongfree / nb, accepted_bad,
                 100.0 * accepted_bad / nb))
    return t



# ------------------------------------------------------------ experiment 6

def _detail_of(make, teach_with, quick=False):
    """Boards and what an entrant said about them, kept whole."""
    out = []
    taught = {}
    for name, flipped, pos, kind, size, img, board in bench.corpus(quick=quick):
        key = (name, flipped)
        if key not in taught:
            e = make()
            if teach_with is not None:
                bench.teach(e, teach_with(name), flipped)
            taught[key] = e
        rows = taught[key].classify(img)
        if isinstance(rows, tuple):
            rows = rows[0]
        out.append({"set": name, "flipped": flipped, "pos": pos, "kind": kind,
                    "size": size, "got": ["".join(r) for r in rows],
                    "want": ["".join(r) for r in W.grid_of(board, flipped)]})
    return out


def exp_guard(quick=False):
    print("=" * 78)
    print("3b/6. DOES THE LEGALITY GUARD STILL HOLD FOR A DIFFERENT MATCHER")
    print("=" * 78)
    for label, recs in (("pieces.py", detail()),
                        ("Tiny 0.65/0.06",
                         _detail_of(lambda: Tiny(0.65, 0.06), teach_self))):
        zero = [r for r in recs if _counts(r)[2] == 0]
        good = [r for r in zero if _counts(r)[1] == 0]
        bad = [r for r in zero if _counts(r)[1] > 0]
        gl = sum(1 for r in good if _legal(r))
        bl = sum(1 for r in bad if _legal(r))
        print()
        print("  %s" % label)
        print("    %4d boards read with no blank square at all" % len(zero))
        print("    %4d entirely correct, %4d holding a fabricated piece"
              % (len(good), len(bad)))
        print("    is-it-a-legal-position keeps %d/%d correct (%.1f%%) and "
              "catches %d/%d fabricated (%.1f%%)"
              % (gl, len(good), 100.0 * gl / max(len(good), 1),
                 len(bad) - bl, len(bad),
                 100.0 * (len(bad) - bl) / max(len(bad), 1)))
        if bl:
            print("    still through:")
            for r in bad:
                if _legal(r):
                    diff = [(r["want"][i][j], r["got"][i][j])
                            for i in range(8) for j in range(8)
                            if r["want"][i][j] != r["got"][i][j]]
                    print("      %-9s %-5s %-11s %-9s %4dpx  %s"
                          % (r["set"], "black" if r["flipped"] else "white",
                             r["pos"], r["kind"], r["size"],
                             " ".join("%s->%s" % d for d in diff[:8])))


def exp_tinyteach(quick=True):
    print("=" * 78)
    print("1d. IS A CORRELATION MATCHER POISONED BY THE WRONG SET TOO")
    print("=" * 78)
    print()
    for label, tw in (("taught from the set being read", teach_self),
                      ("taught from a DIFFERENT set", teach_other)):
        t = run(lambda: Tiny(0.65, 0.06), tw, quick=quick)
        print(t.line("Tiny, " + label))
        show_cuts(t, axes=(0,))
    print()
    print("  (Tiny has no bundled sheet at all: with nothing taught it reads")
    print("   every square as '.', so 'never taught' is not a condition it has.)")


def exp_sizecarry(quick=True):
    print("=" * 78)
    print("1e. TAUGHT AT ONE SIZE, READ AT ANOTHER")
    print("=" * 78)
    names = set_names()
    print()
    print("  right% / WRONG%, taught at (down) read at (across), pieces.py")
    for make, label in ((Taught, "pieces.py"), (lambda: Tiny(0.65, 0.06), "Tiny")):
        print()
        print("  %s" % label)
        print("    %-8s" % "learn" + "".join("%-16s" % s for s in (824, 560, 400, 280)))
        for learn_px in (824, 560, 400, 280):
            line = "    %-8s" % learn_px
            for read_px in (824, 560, 400, 280):
                r = w = u = 0
                for name in names:
                    for flipped in (False, True):
                        e = make()
                        e.learn(render_one(name, chess.Board(), flipped, learn_px),
                                chess.Board(), flipped)
                        for pos in ("developed", "endgame"):
                            b = bench.board_of(pos)
                            img = render_one(name, b, flipped, read_px)
                            rows = e.classify(img)
                            if isinstance(rows, tuple):
                                rows = rows[0]
                            want = W.grid_of(b, flipped)
                            for i in range(8):
                                for j in range(8):
                                    g = rows[i][j]
                                    if g == "?":
                                        u += 1
                                    elif g == want[i][j]:
                                        r += 1
                                    else:
                                        w += 1
                n = max(r + w + u, 1)
                line += "%-16s" % ("%.1f / %.2f" % (100.0 * r / n, 100.0 * w / n))
            print(line)



# ------------------------------------------------------------ experiment 6b

def exp_ownfail(quick=True):
    print("=" * 78)
    print("6. THE OWNER'S FAILURE: taught by hand, still 'board unclear'")
    print("=" * 78)
    print()
    print("  Teach each set from its own opening at 824px, then read that same")
    print("  set back. Nothing foreign, nothing resized, nothing blurred.")
    print()
    for label, make in (("pieces.py", Taught), ("Tiny", lambda: Tiny(0.65, 0.06))):
        print("  --- %s" % label)
        for name in set_names():
            for size in (824, 560, 400, 280):
                e = make()
                e.learn(render_one(name, chess.Board(), False, 824),
                        chess.Board(), False)
                worst = None
                for pos in bench.POSITIONS:
                    b = bench.board_of(pos)
                    img = render_one(name, b, False, size)
                    rows = e.classify(img)
                    if isinstance(rows, tuple):
                        rows = rows[0]
                    want = W.grid_of(b, False)
                    blank = [(r, c, want[r][c]) for r in range(8) for c in range(8)
                             if rows[r][c] == "?"]
                    bad = [(r, c, want[r][c], rows[r][c]) for r in range(8)
                           for c in range(8)
                           if rows[r][c] != "?" and rows[r][c] != want[r][c]]
                    if worst is None or len(blank) + len(bad) > worst[1]:
                        worst = (pos, len(blank) + len(bad), blank, bad)
                pos, _, blank, bad = worst
                files = "abcdefgh"
                where = ", ".join("%s%d=%s" % (files[c], 8 - r, s)
                                  for r, c, s in blank[:8])
                print("    %-9s taught 824 read %4d  worst position %-11s "
                      "%2d unreadable %2d wrong   %s"
                      % (name, size, pos, len(blank), len(bad), where))
        print()


def exp_depth(quick=True):
    print("=" * 78)
    print("3c. HOW FAR THE WILDCARD RULE CAN SEARCH BEFORE IT STOPS BEING SAFE")
    print("=" * 78)
    plies = game_plies()[:13]
    sets = ("flat", "seguisym")
    out = collections.defaultdict(lambda: collections.Counter())
    for name in sets:
        reader = P.PieceReader()
        bench.teach(reader, name, False)
        for kind in ("plain", "small", "blur"):
            imgs = [setgen.variants(render_one(name, b, False, 824), kind)
                    for b in plies]
            believed = plies[0]
            reads = []
            for i in range(1, len(plies)):
                rows, _ = reader.classify(imgs[i], W.grid_of(believed, False))
                reads.append((believed, rows, plies[i]))
                believed = plies[i]
            for depth in (1, 2, 3):
                for bel, rows, truth in reads:
                    cands = [b for b, g in _candidates(bel, False, depth)
                             if _wildcard_match(g, rows)]
                    s = out[(kind, depth)]
                    if len(cands) == 0:
                        s["nothing fits"] += 1
                    elif len(cands) > 1:
                        s["ambiguous"] += 1
                    elif cands[0].board_fen() == truth.board_fen():
                        s["followed"] += 1
                    else:
                        s["FABRICATED"] += 1
    print()
    print("  %-9s %-6s %10s %13s %11s %12s" %
          ("capture", "depth", "followed", "nothing fits", "ambiguous",
           "FABRICATED"))
    for (kind, depth), s in sorted(out.items()):
        n = sum(s.values())
        print("  %-9s %-6d %9d%% %12d%% %10d%% %11d%%   (%d fabricated of %d)"
              % (kind, depth, round(100.0 * s["followed"] / n),
                 round(100.0 * s["nothing fits"] / n),
                 round(100.0 * s["ambiguous"] / n),
                 round(100.0 * s["FABRICATED"] / n), s["FABRICATED"], n))
    print()
    print("  'nothing fits' is the safe refusal: the reader named a piece that")
    print("  no legal continuation puts there, so the read is corrupt and the")
    print("  rule says so. 'ambiguous' is the honest one: too much of the board")
    print("  went unread to tell two continuations apart.")



# ------------------------------------------------------------ experiment 6c
# Two things every real chess.com board has that bench.py's corpus does not:
# the last-move highlight and the rank and file labels. Both live in the pixels
# the reader measures, and neither is any of the eight variants.


def _wash(square, wash=(255, 255, 51)):
    return tuple((v + w + 1) // 2 for v, w in zip(square, wash))


def with_highlight(img, squares_rc, size):
    """Paint the last-move wash over two squares, the way the board does."""
    from PIL import Image as I
    out = img.copy()
    step = size // 8
    for r, c in squares_rc:
        box = (c * step, r * step, (c + 1) * step, (r + 1) * step)
        patch = out.crop(box)
        wash = I.new("RGB", patch.size, (255, 255, 51))
        out.paste(I.blend(patch, wash, 0.5), box)
    return out


def with_labels(img, size, flipped=False):
    """Rank digits down one outer file and file letters along one outer rank,
    drawn in the opposite square colour the way a real board draws them."""
    from PIL import ImageDraw, ImageFont
    out = img.copy()
    pen = ImageDraw.Draw(out)
    step = size // 8
    try:
        face = ImageFont.truetype("arialbd.ttf", max(9, int(step * 0.18)))
    except Exception:
        face = ImageFont.load_default()
    files = "abcdefgh" if not flipped else "hgfedcba"
    ranks = "87654321" if not flipped else "12345678"
    for r in range(8):
        colour = setgen.DARK if (r + 0) % 2 == 0 else setgen.LIGHT
        pen.text((2, r * step + 2), ranks[r], font=face, fill=colour)
    for c in range(8):
        colour = setgen.DARK if (7 + c) % 2 == 0 else setgen.LIGHT
        pen.text((c * step + step - int(step * 0.16), 8 * step - int(step * 0.22)),
                 files[c], font=face, fill=colour)
    return out


def exp_screen(quick=True):
    print("=" * 78)
    print("6c. WHAT A REAL BOARD HAS THAT THE CORPUS DOES NOT")
    print("=" * 78)
    lit = [(6, 4), (4, 4)]          # e2 and e4, a last move
    print()
    print("  %-9s %-6s %-22s %8s %7s %9s"
          % ("set", "size", "board", "right", "wrong", "unreadable"))
    for label, make in (("pieces.py", Taught), ("Tiny 0.65/0.06",
                                                lambda: Tiny(0.65, 0.06))):
        print("  --- %s" % label)
        agg = collections.Counter()
        for name in set_names():
            for size in (824, 560):
                e = make()
                e.learn(render_one(name, chess.Board(), False, 824),
                        chess.Board(), False)
                for what in ("clean", "last-move highlight", "coordinates",
                             "highlight + coordinates"):
                    r = w = u = 0
                    for pos in bench.POSITIONS:
                        b = bench.board_of(pos)
                        img = render_one(name, b, False, size)
                        if "highlight" in what:
                            img = with_highlight(img, lit, size)
                        if "coordinates" in what:
                            img = with_labels(img, size)
                        rows = e.classify(img)
                        if isinstance(rows, tuple):
                            rows = rows[0]
                        want = W.grid_of(b, False)
                        for i in range(8):
                            for j in range(8):
                                g = rows[i][j]
                                if g == "?":
                                    u += 1
                                elif g == want[i][j]:
                                    r += 1
                                else:
                                    w += 1
                    n = r + w + u
                    agg[(what, "r")] += r
                    agg[(what, "w")] += w
                    agg[(what, "u")] += u
                    if size == 824 and name in ("chesscom", "seguisym"):
                        print("  %-9s %-6d %-22s %7.1f%% %6.2f%% %8.2f%%"
                              % (name, size, what, 100.0 * r / n,
                                 100.0 * w / n, 100.0 * u / n))
        print("  %-9s %-6s %-22s" % ("ALL", "824+560", ""))
        for what in ("clean", "last-move highlight", "coordinates",
                     "highlight + coordinates"):
            r, w, u = agg[(what, "r")], agg[(what, "w")], agg[(what, "u")]
            n = max(r + w + u, 1)
            print("  %-9s %-6s %-22s %7.1f%% %6.2f%% %8.2f%%"
                  % ("", "", what, 100.0 * r / n, 100.0 * w / n, 100.0 * u / n))
        print()


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "teaching"
    quick = "--full" not in sys.argv
    globals()["exp_" + which](quick=quick)
