r"""Free game review page: Stockfish rates every move, then one HTML file to step through.

    python gamereview.py                      # newest games\*.pgn from ChessWatch
    python gamereview.py game.pgn --me black
    python gamereview.py --moves "1. d4 e5 2. dxe5 f6" --white Erllo --black daniart3

Ratings come from review.classify (ChessWatch estimates, not Chess.com's).
"""
import argparse
import html
import io
import json
import math
import os
import pathlib
import sys
import webbrowser

import chess
import chess.engine
import chess.pgn
import chess.svg

import coach
import review

HERE = pathlib.Path(__file__).resolve().parent
DEPTH = 18
ORDER = ["brilliant", "great", "best", "excellent", "good", "book", "inaccuracy",
         "mistake", "miss", "blunder", "forced"]
EXTRA = {"miss": ("X", "Miss", "#e06c75"), "book": ("B", "Book", "#b08d6a")}


def badge(kind):
    if kind in EXTRA:
        return EXTRA[kind]
    sym, label, color, _ = review.BADGES[kind]
    return sym, label, color


def win(score):
    return review.expectation(score) * 100


def fmt(score):
    """White-POV score as +1.3 / -0.4 / M3 / -M2."""
    if score.is_mate():
        m = score.mate()
        return ("M%d" % m) if m > 0 else ("-M%d" % -m)
    return "%+.1f" % (score.score() / 100)


def bar_value(score):
    """White-POV score clipped to [-1, 1] for the eval bar and graph."""
    if score.is_mate():
        return 1.0 if score.mate() > 0 else -1.0
    return max(-1.0, min(1.0, 2 / (1 + math.exp(-score.score() / 400)) - 1))


def move_accuracy(drop):
    # Lichess's per-move accuracy curve on win-percent loss.
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * drop) - 3.1669))


def load_game(args):
    if args.moves:
        game = chess.pgn.read_game(io.StringIO(args.moves))
        game.headers["White"] = args.white or "White"
        game.headers["Black"] = args.black or "Black"
        return game, "pasted moves"
    path = pathlib.Path(args.pgn) if args.pgn else max(
        (HERE / "games").glob("*.pgn"), key=os.path.getmtime)
    with open(path, encoding="utf-8") as fh:
        game = chess.pgn.read_game(fh)
    if args.white:
        game.headers["White"] = args.white
    if args.black:
        game.headers["Black"] = args.black
    return game, path.name


def analyse_game(game, progress=True):
    path = coach.find_engine()
    plies = []
    board = game.board()
    moves = list(game.mainline_moves())
    if not moves:
        raise SystemExit("No moves found.")
    with chess.engine.SimpleEngine.popen_uci(str(path), **coach.POPEN_FLAGS) as engine:
        engine.configure({"Threads": max(1, (os.cpu_count() or 2) - 1), "Hash": 256})
        limit = chess.engine.Limit(depth=DEPTH)
        for i, move in enumerate(moves):
            if progress:
                print("\r  analysing move %d/%d" % (i + 1, len(moves)), end="", flush=True)
            top = engine.analyse(board, limit, multipv=min(2, board.legal_moves.count()))
            best_move = top[0]["pv"][0]
            played = next((l for l in top if l["pv"][0] == move), None)
            if played is None:
                played = engine.analyse(board, limit, root_moves=[move])
            pov = lambda line: line["score"].pov(board.turn)
            rating = review.classify(board, move, pov(top[0]), pov(played),
                                     pov(top[1]) if len(top) > 1 else None,
                                     move == best_move)
            drop = max(0.0, win(pov(top[0])) - win(pov(played)))
            best_line = board.variation_san(top[0]["pv"][:5])
            entry = {
                "san": board.san(move), "uci": move.uci(), "color": "w" if board.turn else "b",
                "num": board.fullmove_number, "kind": rating["category"],
                "best_san": board.san(best_move), "best_uci": best_move.uci(),
                "best_line": best_line, "drop": round(drop, 1),
                "acc": move_accuracy(drop), "is_best": move == best_move,
                "best_eval": fmt(top[0]["score"].white()),
            }
            board.push(move)
            if board.is_checkmate():
                entry["eval"] = "1-0" if board.turn == chess.BLACK else "0-1"
                entry["bar"] = 1.0 if board.turn == chess.BLACK else -1.0
            elif board.is_game_over():
                entry["eval"], entry["bar"] = "½-½", 0.0
            else:
                white = played["score"].white()
                entry["eval"], entry["bar"] = fmt(white), bar_value(white)
            plies.append(entry)
    if progress:
        print()
    # Miss: the opponent just erred badly and you did not punish it.
    for prev, cur in zip(plies, plies[1:]):
        if prev["kind"] in ("mistake", "blunder") and cur["kind"] in ("inaccuracy", "mistake"):
            cur["kind"] = "miss"
    return plies


def board_svg(board, ply, flipped):
    arrows, fill = [], {}
    if ply is not None:
        mv = chess.Move.from_uci(ply["uci"])
        _, _, color = badge(ply["kind"])
        fill = {mv.from_square: color + "55", mv.to_square: color + "99"}
        if not ply["is_best"] and ply["kind"] not in ("forced",):
            b = chess.Move.from_uci(ply["best_uci"])
            arrows.append(chess.svg.Arrow(b.from_square, b.to_square, color="#4caf50cc"))
    return chess.svg.board(board, size=560, coordinates=True, flipped=flipped,
                           fill=fill, arrows=arrows,
                           colors={"square light": "#eeeed2", "square dark": "#7a9bb8",
                                   "margin": "#1e2227", "coord": "#9aa4ad"},
                           check=board.king(board.turn) if board.is_check() else None)


def square_xy(square, flipped):
    f, r = chess.square_file(square), chess.square_rank(square)
    if flipped:
        f, r = 7 - f, 7 - r
    return f, 7 - r


def summarize(plies, color):
    mine = [p for p in plies if p["color"] == color]
    counts = {k: sum(1 for p in mine if p["kind"] == k) for k in ORDER}
    acc = sum(p["acc"] for p in mine) / len(mine) if mine else 0
    return counts, acc


def build_html(game, plies, source, me):
    flipped = me == "black"
    board = game.board()
    frames = [{"svg": board_svg(board, None, flipped), "bar": bar_value(chess.engine.Cp(20)),
               "eval": "+0.2", "badge": None}]
    for p in plies:
        board.push(chess.Move.from_uci(p["uci"]))
        sym, label, color = badge(p["kind"])
        x, y = square_xy(chess.Move.from_uci(p["uci"]).to_square, flipped)
        frames.append({"svg": board_svg(board, p, flipped), "bar": p["bar"], "eval": p["eval"],
                       "badge": {"sym": sym, "color": color, "x": x, "y": y}})
    W, B = game.headers.get("White", "White"), game.headers.get("Black", "Black")
    sw, aw = summarize(plies, "w")
    sb, ab = summarize(plies, "b")
    def chips(counts):
        out = ""
        for k in ORDER:
            if counts[k]:
                sym, label, color = badge(k)
                out += ('<span class="chip" title="%s"><span class="b" style="background:%s">%s</span>%d</span>'
                        % (label, color, html.escape(sym), counts[k]))
        return out
    rows = ('<div class="pl"><span class="nm">&#9675; __WHITE__</span><b class="ac">%.1f</b>%s</div>'
            '<div class="pl"><span class="nm">&#9679; __BLACK__</span><b class="ac">%.1f</b>%s</div>'
            % (aw, chips(sw), ab, chips(sb)))
    data = {"frames": frames, "plies": [
        {**p, "label": badge(p["kind"])[1], "sym": badge(p["kind"])[0], "colorhex": badge(p["kind"])[2]}
        for p in plies]}
    result = game.headers.get("Result", "*")
    title = "%s vs %s" % (W, B)
    return TEMPLATE.replace("__ROWS__", rows).replace("__TITLE__", html.escape(title)) \
        .replace("__WHITE__", html.escape(W)).replace("__BLACK__", html.escape(B)) \
        .replace("__AW__", "%.1f" % aw).replace("__AB__", "%.1f" % ab) \
        .replace("__RESULT__", html.escape(result)) \
        .replace("__SOURCE__", html.escape(source)).replace("__DEPTH__", str(DEPTH)) \
        .replace("__FLIP__", "true" if flipped else "false") \
        .replace("__DATA__", json.dumps(data).replace("</", "<\\/"))


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Review: __TITLE__</title>
<style>
:root{--bg:#16191d;--panel:#1f2328;--line:#2d333a;--text:#e6e9ec;--muted:#9aa4ad;--bh:calc(100vh - 24px)}
*{box-sizing:border-box}
html,body{height:100%;overflow:hidden}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.35 system-ui,Segoe UI,sans-serif}
.wrap{display:flex;gap:12px;padding:12px;height:100vh;justify-content:center}
.evalbar{width:22px;height:var(--bh);background:#2b2f34;border-radius:4px;position:relative;overflow:hidden;flex:none}
.evalbar .w{position:absolute;left:0;right:0;bottom:0;background:#f2f2f2;transition:height .25s}
.evalbar .t{position:absolute;left:0;right:0;font-size:10px;font-weight:700;text-align:center}
.boardbox{position:relative;width:var(--bh);height:var(--bh);flex:none}
.boardbox svg{width:100%;height:100%;display:block}
.pb{position:absolute;width:24px;height:24px;margin:-12px 0 0 -12px;border-radius:50%;color:#fff;font-weight:800;font-size:12px;
 display:flex;align-items:center;justify-content:center;box-shadow:0 1px 4px #0008;border:2px solid #fff;pointer-events:none}
.right{width:440px;flex:none;display:flex;flex-direction:column;gap:8px;height:var(--bh)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 10px}
.hd{display:flex;justify-content:space-between;font-weight:700;margin-bottom:4px}
.muted{color:var(--muted);font-size:12px}
.pl{display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:2px 0}
.nm{width:84px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.ac{width:38px;font-size:15px}
.chip{display:inline-flex;align-items:center;gap:3px;font-weight:700;font-size:13px}
.b{display:inline-block;min-width:20px;padding:0 4px;border-radius:9px;color:#fff;font-weight:800;font-size:11px;text-align:center;line-height:18px}
#explain{min-height:84px}#explain .big{font-size:15px;font-weight:700;margin-bottom:2px}
#graph{width:100%;height:56px;display:block;cursor:pointer}
.moves{flex:1;min-height:0;overflow:auto;display:grid;grid-template-columns:30px 1fr 1fr;gap:1px 4px;align-content:start;padding:6px 8px}
.moves .num{color:var(--muted);padding:2px 0;font-size:12px}
.mv{padding:2px 5px;border-radius:4px;cursor:pointer;display:flex;gap:5px;align-items:center}
.mv:hover{background:#2b3138}.mv.on{background:#3a4450}
.nav{display:flex;gap:6px}.nav button{flex:1;padding:6px;background:#2b3138;color:var(--text);border:1px solid var(--line);border-radius:6px;font-size:15px;cursor:pointer}
.nav button:hover{background:#36404a}
</style></head><body>
<div class="wrap">
 <div class="evalbar"><div class="w" id="barw"></div><div class="t" id="bart"></div></div>
 <div class="boardbox" id="boardbox"></div>
 <div class="right">
  <div class="card"><div class="hd"><span>__TITLE__</span><span class="muted">__RESULT__ &middot; accuracy</span></div>__ROWS__</div>
  <div class="card" id="explain"></div>
  <div class="card" style="padding:4px"><svg id="graph" viewBox="0 0 1000 110" preserveAspectRatio="none"></svg></div>
  <div class="card moves" id="moves"></div>
  <div class="nav"><button onclick="go(0)">&#x23EE;</button><button onclick="go(cur-1)">&#x25C0;</button>
   <button onclick="go(cur+1)">&#x25B6;</button><button onclick="go(D.frames.length-1)">&#x23ED;</button></div>
  <div class="muted">Stockfish depth __DEPTH__ &middot; estimates, not Chess.com's &middot; &larr; &rarr; keys</div>
 </div>
</div>
<script>
const D=__DATA__, FLIP=__FLIP__; let cur=0;
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const mv=document.getElementById('moves');
D.plies.forEach((p,i)=>{
 if(p.color==='w'){const n=document.createElement('div');n.className='num';n.textContent=p.num+'.';mv.appendChild(n);}
 else if(i===0){mv.insertAdjacentHTML('beforeend','<div class="num">'+p.num+'...</div><div></div>');}
 const d=document.createElement('div');d.className='mv';d.id='m'+(i+1);
 d.innerHTML='<span class="b" style="background:'+p.colorhex+'">'+esc(p.sym)+'</span>'+esc(p.san);
 d.onclick=()=>go(i+1);mv.appendChild(d);
});
// eval graph
(function(){const g=document.getElementById('graph'),n=D.frames.length-1||1;
 let pts=D.frames.map((f,i)=>[i/n*1000,55-f.bar*50]);
 let area='M0,110 '+pts.map(p=>'L'+p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ')+' L1000,110 Z';
 let s='<rect width="1000" height="110" fill="#2b2f34"/><path d="'+area+'" fill="#e8e8e8"/><line x1="0" y1="55" x2="1000" y2="55" stroke="#777" stroke-dasharray="4 4"/>';
 D.plies.forEach((p,i)=>{if(['brilliant','great','mistake','miss','blunder'].includes(p.kind)){const q=pts[i+1];
  s+='<circle cx="'+q[0]+'" cy="'+q[1]+'" r="6" fill="'+p.colorhex+'" stroke="#fff" stroke-width="1.5"/>';}});
 s+='<line id="cursor" x1="0" y1="0" x2="0" y2="110" stroke="#ffcc00" stroke-width="2"/>';
 g.innerHTML=s;g.onclick=e=>{const r=g.getBoundingClientRect();go(Math.round((e.clientX-r.left)/r.width*n));};})();
function go(i){
 i=Math.max(0,Math.min(D.frames.length-1,i));cur=i;const f=D.frames[i];
 const bb=document.getElementById('boardbox');let h=f.svg;
 if(f.badge){const sq=45/390*100,off=15/390*100;const x=off+(f.badge.x+0.9)*sq,y=off+(f.badge.y+0.1)*sq;
  h+='<div class="pb" style="left:'+x+'%;top:'+y+'%;background:'+f.badge.color+'">'+esc(f.badge.sym)+'</div>';}
 bb.innerHTML=h;
 const w=(f.bar+1)/2*100, wb=document.getElementById('barw');
 wb.style.height=(FLIP?100-w:w)+'%';wb.style.background=FLIP?'#2b2f34':'#f2f2f2';
 document.querySelector('.evalbar').style.background=FLIP?'#f2f2f2':'#2b2f34';
 const t=document.getElementById('bart');t.textContent=f.eval;
 const whiteUp=f.bar>=0, atBottom=FLIP?!whiteUp:whiteUp;
 t.style.bottom=atBottom?'4px':'';t.style.top=atBottom?'':'4px';t.style.color=whiteUp?'#222':'#eee';
 document.querySelectorAll('.mv.on').forEach(e=>e.classList.remove('on'));
 const el=document.getElementById('m'+i);if(el){el.classList.add('on');el.scrollIntoView({block:'nearest'});}
 const n=D.frames.length-1||1;const c=document.getElementById('cursor');c.setAttribute('x1',i/n*1000);c.setAttribute('x2',i/n*1000);
 const ex=document.getElementById('explain');
 if(i===0){ex.innerHTML='<div class="big">Start</div><div class="muted">Use the arrow keys or click a move.</div>';return;}
 const p=D.plies[i-1];
 let s='<div class="big"><span class="b" style="background:'+p.colorhex+'">'+esc(p.sym)+'</span> '+p.num+(p.color==='w'?'. ':'... ')+esc(p.san)+' is '+(/^[aeiou]/i.test(p.label)?'an ':'a ')+p.label.toLowerCase()+' move</div>';
 s+='<div>Eval after: <b>'+esc(p.eval)+'</b>';
 if(!p.is_best&&p.kind!=='forced') s+=' &nbsp;·&nbsp; Best was <b>'+esc(p.best_san)+'</b> ('+esc(p.best_eval)+'), win chance lost '+p.drop+'%';
 s+='</div><div class="muted">Engine line: '+esc(p.best_line)+'</div>';
 ex.innerHTML=s;
}
document.addEventListener('keydown',e=>{if(e.key==='ArrowRight')go(cur+1);else if(e.key==='ArrowLeft')go(cur-1);
 else if(e.key==='Home')go(0);else if(e.key==='End')go(D.frames.length-1);else return;e.preventDefault();});
go(0);
</script></body></html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pgn", nargs="?", help="PGN file (default: newest games\\*.pgn)")
    ap.add_argument("--moves", help="SAN move text instead of a file")
    ap.add_argument("--white"), ap.add_argument("--black")
    ap.add_argument("--me", choices=["white", "black"], default="white", help="board orientation")
    ap.add_argument("--out", help="output HTML path")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()
    game, source = load_game(args)
    plies = analyse_game(game)
    out = pathlib.Path(args.out) if args.out else HERE / "reviews" / (
        "review-%s.html" % pathlib.Path(source).stem.replace(" ", "-"))
    out.parent.mkdir(exist_ok=True)
    out.write_text(build_html(game, plies, source, args.me), encoding="utf-8")
    print(out)
    for p in plies:
        print("%3d%s %-8s %s" % (p["num"], "." if p["color"] == "w" else "...", p["san"], p["kind"]))
    if not args.no_open:
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
