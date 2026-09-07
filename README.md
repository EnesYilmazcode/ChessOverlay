# ChessOverlay

Two halves of one idea, for anyone building chess teaching tools.

**`chesswatch`** watches a chess board on your screen, reads it, and writes the
game to disk as PGN and JSON. It runs Stockfish on the position it is reading
and says, in words, what to play. Tick **Arrow** and it draws that move on the
board itself, over whatever program is showing it, in one colour for your move
and another for your opponent's.

**`holochess`** is a full board you play against the engine, with the same arrow
inside its own window.

It works off pixels alone, so it does not care which site, app or board theme is
showing the game. Nothing signs in, nothing touches an account, and nothing
leaves the machine.

![the arrow over a middlegame board](docs/arrow.png)

That is a real position twenty-two plies in, not an opening: the arrow is worth
looking at when the board is busy enough that the move is not obvious.
Regenerate it with `python mkshot.py` from `chesswatch/`.

## How it works

### The one decision everything else follows from

**Read the board, not the move list.**

The first version read the move list with OCR and it failed completely.
Chess.com can be set to figurine notation, where the piece is a little icon
rather than a letter, so `Qf7+` reads as `f7+` and every move except a pawn push
is rejected.

Reading the board has no such problem, because no piece is ever named. The
reader only sees that a dark piece left d8 and a dark piece arrived on h4, and
only one legal move does that, so the move is `Qh4`. **Piece identity comes out
of the rules, not out of the pixels.** Board themes, piece sets and notation
style stop mattering, and so does the language the site is in.

That single choice is why the reader can be as crude as it is, and it sets up
everything below.

### The path a frame takes

A frame goes through six stages. These names are used for the rest of this file.

| Stage | What happens | Where |
| --- | --- | --- |
| **find** | Hunt the whole desktop for something that looks like a board, and keep the rectangle. | `find_board_on_screen` → `watcher.find_board` |
| **grab** | Screenshot just that rectangle, ~8 times a second. | `chesswatch.grab` (mss) |
| **settle** | If the picture changed, wait for it to hold still, because a piece mid-slide is a legal position that never happened. | `Worker._read_settled` |
| **read** | Classify all 64 squares as white / black / empty. | `watcher.read_occupancy` |
| **explain** | Find the one legal move that turns the position we had into the one we see. | `BoardTracker.feed` |
| **record** | Append the move, write PGN + JSON. | `watcher.Game.save` |

Only **explain** knows any chess. Everything before it is pixels, and everything
after it is file writing.

Running alongside those, on a timer, is a seventh stage: **check**, the slow
piece-level pass that names the actual piece on each square
(`BoardTracker.check`). More on why there are two readers below.

### Three threads and two queues

The shape of the program is set by one rule: **the reader must never wait for
anything.** Not for the engine, not for the screen, not for you. So the work is
split across three threads that only ever talk through queues.

```
  MAIN THREAD (Tk)                WORKER THREAD                ENGINE THREAD
  ----------------                -------------                -------------
                                  every 120 ms:
  App._drain()   <--- frame q --- find / grab / settle
  move list, board,               read / explain / record
  status labels                          |
       |                                 | the live position (FEN)
       |                                 v
       +-------------------------- ask(fen) ---------->  Stockfish
       |                                                 searching
  App._drain_coach() <--- answer q --------------------  read while it
  "your move Nf3"                                        is still thinking
  knight: g1 to f3  +0.4
       |
       v
  overlay.py  --->  a click-through arrow on the real board
```

**Worker → frame queue.** The worker owns the screen and the game state. It
puts one dict per tick on the queue: the move list, the FEN, the board
rectangle, whether it is locked on, the result. Tk drains that and redraws. The
worker never touches a widget and the GUI never touches a screenshot, which is
the whole reason the test suites can run headless.

**Coach → answer queue.** `ask(fen)` is fire-and-forget. Stockfish is read
*while it is still searching*, so a first answer is on the window in about a
hundredth of a second and improves from there. Every answer carries a `final`
flag and the FEN it belongs to:

- not final yet → shown with a trailing `...`
- for a position already played past → **dropped**, never shown against the
  wrong board

Think time is bounded (0.3, 1 or 2.5 seconds) rather than open-ended on purpose.
A search left running while you decide would hold a whole core for as long as
you took. Because the first answer arrives at once either way, a longer think
does not make you wait — it only searches deeper on a board you are still
sitting in front of.

### Two readers, because the cheap one cannot do everything

This is the central split in the project, and the reason for both `watcher.py`
and `pieces.py`.

| | Fast reader | Slow reader |
| --- | --- | --- |
| **File** | `watcher.read_occupancy` | `pieces.PieceReader` |
| **Answers** | white piece / black piece / empty | which of the twelve pieces |
| **How** | grey the square, count pixels brighter than 244 or darker than 70 | shape-match against templates, normalised so contrast cannot matter |
| **Cost** | cheap enough for every frame | far too slow for every frame |
| **Runs** | ~8 times a second | on a timer, when the fast reader is stuck, and on the button |
| **Used for** | following a game move by move | starting cold, checking the fast reader, bridging missed moves |

The fast reader is enough to follow a game because **explain** supplies the
missing information out of the rules. It is not enough to read a position cold —
a board you just walked in on — because nothing in "a dark piece is on d8" says
whether that is a queen or a rook. That is the slow reader's job.

The slow reader learns its templates from your own screen the moment a position
it is sure of appears, and caches them, so the next game starts where the last
one left off. Failing that it falls back to the bundled `pieces.png`, and
**Setup > Board > Pieces** (`enroll.py`) lets you cut a set out of your own
screen by hand.

Two more readers are finished and tested but **not yet called by the app** —
they live behind their own test suites and command lines:

- `position.py` takes the slow reader's per-square scores and picks all 64
  squares as **one legal position** rather than 64 independent guesses, solved
  as a min-cost flow whose capacities are the counting rules a real position
  obeys: one king a side, at most eight pawns, every extra queen paid for out of
  the pawn budget. A square that is a coin flip between a rook and a bishop is
  often settled outright by the rest of the board.
- `piecebank.py` answers the joined-late case. A game walked in on never shows
  all twelve pieces, so there is nothing to learn from; instead the bank holds a
  sheet per piece set and says which set this board is drawn in. Choosing a set
  is a much easier question than identifying a piece — it only needs *some*
  template to cover the pixels well — so two pieces on an otherwise empty board
  are enough.

### The four invariants

Four things this program is not allowed to do. Each one is held by a test rather
than by care, and the last two are there because the program was caught doing
exactly what they now forbid.

**1. The arrow cannot corrupt a recording.** This is the sharp one: the recorder
is reading the same pixels the arrow paints on.

`read_occupancy` counts only pixels brighter than 244 or darker than 70 —
everything between is already discarded, which is how the last-move highlight,
the coordinate labels and the check marker get ignored. Both arrow colours are
chosen to land in that gap: cyan greys to 165, violet to 123, and blended at 85
per cent over pure white *or* pure black each stays inside the band **on its own
account rather than on the other's**. The reader cannot see either of them.

Coverage can still change what a square reads as — a white pawn with the shaft
painted down its file loses enough bright pixels to come back black. That
position matches no legal move, so **explain** ignores the frame and waits,
exactly as it already does for a piece mid-animation. It cannot write down a
move that did not happen.

`overlaytest.py` measures this rather than claiming it: sixteen arrows across
the crowded ranks and onto pieces, at 664px and again at 240px, in both colours,
every one confirmed present, none changing a single square, every pixel any of
them touched confirmed between the two cutoffs. The confirmed-present check is
the load-bearing one — without it an overlay that drew nothing would pass
everything else — so it runs once per colour and diffs the two captures of the
same move against each other, and a colour that never got painted cannot hide
behind the one that did.

**2. Never guess a move.** Three missed moves can transpose: a double push then
an en passant capture leaves the same picture as a single push then an ordinary
one. In those cases it records nothing rather than a plausible fiction.

**3. A finished game is read-only.** Chess.com drops you into Game Review the
moment a game ends, and clicking back through it puts an old position on screen.
That used to read as a takeback and overwrite the finished file with a fragment.

**4. The engine may not paint outside its own window.** The arrow in `holochess`
is a child widget of its own window rather than an always-on-top overlay, and
`holochess/shot.py` scans the whole desktop after drawing one and fails if a
single hologram-coloured pixel turns up outside the app. An earlier
always-on-top version was caught painting its arrow onto a chess board sitting
behind it, which is where that test came from.

The coach in `chesswatch` *does* draw on the board being watched — that is the
point of it — and it is off until you tick it on. Worth saying once, since the
reader works on any site: engine help during a live rated game on someone else's
platform is cheating there, whatever this code happens to allow.

### One more thing that took real work: animation

Chess.com slides a piece to its destination, and mid-slide the board shows a
clean, still, **legal** move that never happened. `e3` sits on screen for about
a tenth of a second during `e2-e4`.

Counting agreeing reads does not help, because every read inside that window
agrees. What does help is that only a move whose destination lies *between* its
origin and a longer legal move by the same piece can be faked this way. So
**settle** holds those for over a second and believes everything else after a
quarter of one. `settletest.py` drives that off a real clock.

### The module map

`chesswatch.py` is the only thing that knows about all the parts. It builds a
`pieces.PieceReader` and hands it to `watcher.BoardTracker`, so `watcher.py`
never imports the piece reader and can be tested without one.

```
  chesswatch.py ......... Tk app, the capture Worker thread, config, arrow sync
    |
    |  builds and wires together:
    |
    +-- watcher.py ...... find the board, read occupancy, explain the move,
    |                     hold the game, write PGN + JSON.  No GUI in here.
    +-- pieces.py ....... which piece is on a square.  Injected into
    |                     BoardTracker as its `reader`.
    +-- enroll.py ....... cut a piece set out of a screenshot, by hand
    +-- coach.py ........ Stockfish on its own thread, answers on a queue
    +-- overlay.py ...... the click-through arrow on the real board

  not wired in yet, each with its own tests and command line:
    position.py ......... all 64 squares as one legal position
    piecebank.py ........ which piece set this board is drawn in

  holochess/holochess.py ... standalone board vs the engine, own arrow
```

| Component | Owns | State |
| --- | --- | --- |
| [`chesswatch/chesswatch.py`](chesswatch/) | The window, the capture thread, the queues, the wiring. | Finished. |
| [`chesswatch/watcher.py`](chesswatch/watcher.py) | Board-finding, occupancy, move inference, game files. No GUI on purpose, so it tests headless. | Finished. 87 headless checks + 2 on-screen. |
| [`chesswatch/pieces.py`](chesswatch/pieces.py) | Which piece is on a square, by normalised shape matching. | Works. |
| [`chesswatch/coach.py`](chesswatch/coach.py) | Stockfish on its own thread, read mid-search. | Works. 52 checks. On by default. |
| [`chesswatch/overlay.py`](chesswatch/overlay.py) | The click-through arrow, in a colour the reader is blind to. | Works. 21 checks, headless by default. |
| [`chesswatch/position.py`](chesswatch/position.py) | All 64 squares as one legal position, min-cost flow. | Works. 29 checks. Nothing calls it yet. |
| [`chesswatch/piecebank.py`](chesswatch/piecebank.py) | Choosing the piece set when learning is impossible. | Works. Command line only so far. |
| [`holochess/`](holochess/) | Local board, Stockfish 18, hint arrow confined to its window. | Works. 17 checks. |

Each half has its own README with the long version: [`chesswatch/README.md`](chesswatch/README.md) goes square by square through the reader, the piece checker and the highlight, and [`holochess/README.md`](holochess/README.md) covers the standalone board.

## Run the recorder

```
cd chesswatch
pip install -r requirements.txt
python chesswatch.py
```

It starts watching by itself and **find** locates the board on its own. There is
nothing to configure. If the board is not chess.com green, drag a box around it
once with **Setup > Board > Pick** and everything else carries on as normal.

Leave it running before you start a game and it numbers the moves from 1; join
part way through and it says so in the file, because nothing in a picture of a
board tells you how many moves came before.

Games land in `chesswatch/games/` as a matched pair:

```
2026-08-30_190621.pgn     open in any chess program
2026-08-30_190621.json    move by move, tagged me vs opponent
```

## Ask it what to play

Coaching is on, so Stockfish is started at the first launch and the line above
the moves reads something like:

```
your move  Nf3
knight: g1 to f3   +0.4
```

It names the piece and both squares rather than only the notation, says what a
capture takes and when a move gives check, and counts a forced mate in moves.
That is aimed at a player who has not learned to read `Nxe5+` yet.

A machine with no Stockfish on it says so once, in small text under the move
list, and carries on as a recorder. Untick **Coach** under **Setup** to run that
way on purpose, and it is remembered.

**Setup > Think** sets how long the engine gets on one position: 0.3, 1 or 2.5
seconds. Both switches and the think time are remembered in `config.json`. See
[Three threads and two queues](#three-threads-and-two-queues) for why a longer
think never makes you wait.

Tick **Arrow** as well and the move is drawn on the board itself, over whatever
program is showing it. The window is click-through, so it does not get between
you and the game. There is an arrow for every position, not only for your own
turn, because the engine's answer to your opponent's position is what they are
threatening. Cyan is your move, violet is theirs — and neither is visible to the
recorder, which is [invariant 1](#the-four-invariants).

You supply Stockfish, the same binary the other half uses. See below.

## Run the engine half

```
cd holochess
pip install -r requirements.txt
python holochess.py
```

Stockfish is not in the repository. The Windows build is 114 MB, over GitHub's
file size limit, and it is GPL licensed separately from this code. Get it from
[stockfishchess.org](https://stockfishchess.org/download/) and unpack it into
`holochess/engine/stockfish/`, or point `STOCKFISH_PATH` at your own copy.

Press `H` for the arrow. Opponent strength is Stockfish's own `UCI_Elo`, and the
hint is always computed at full strength no matter where that slider sits.

## Tests

```
cd chesswatch
python selftest.py     116 headless checks, including real screenshots
python coachtest.py     53 checks on the engine wrapper and its label
python overlaytest.py   21 checks that the arrows cannot corrupt a reading
python settletest.py    move animation, driven off a real clock
python livetest.py      plays whole games past the real capture worker

cd holochess
python smoke_test.py    engine, moves, hints, undo, flip
python shot.py          proves the arrow cannot escape the window
```

None of the chesswatch tests put anything on screen or screenshot your desktop.
They render the board into memory and point **grab** at that. `coachtest.py`
does build a real Tk app, because the label it checks lives in one, but its
window stays withdrawn and its capture is pointed at a blank image.

Add `--on-screen` to `livetest.py` or `overlaytest.py` to run against the real
desktop instead, in a window the size of the board plus a margin. That is the
only way to exercise mss, DPI scaling, coordinates on a second monitor and
click-through — by default the arrows are modelled in PIL, which settles the
colours and the geometry and costs no screen space. `shot.py` still takes over
the screen.

The screenshots the tests read are in `chesswatch/testdata/`. They are real
chess.com windows with everything outside the board blacked out, so no account
name ships with them.

## Needs

Python 3, and `pip install -r requirements.txt` in whichever half you are
running. The recorder needs mss, pillow and chess. The engine half needs PyQt6,
chess, and a Stockfish binary, which the coach then shares. No OCR anywhere, and
no Tesseract.

Written for Windows. The recorder calls `SetProcessDpiAwareness` and guards
itself with a named mutex, both Windows specific, and the board reading itself
is plain pixels and would port.

## License

MIT, see [LICENSE](LICENSE). Stockfish is separate and GPL, which is the other
reason it is not vendored here.
