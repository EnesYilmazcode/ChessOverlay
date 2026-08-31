# ChessOverlay

Two halves of one idea, for anyone building chess teaching tools. `chesswatch`
watches a chess board on your screen, reads it, and writes the game to disk.
Tick **best move** and it also runs Stockfish on the position it is reading and
says, in words, what to play. `holochess` is a full board you play against the
engine, with the best move drawn as a translucent arrow.

Nothing signs in, nothing touches an account, and nothing leaves the machine.
It works off pixels, so it does not care which site or app is showing the board.

```
  your screen                   this repo                        what you get
  -----------                   ---------                        ------------

  a chess board   ------>  chesswatch/                   ----->  games/*.pgn
  (pixels, nothing else)   reads 64 squares, then asks           games/*.json
                           the rules which legal move
                           explains the change
                                     |
                                     |  the live position
                                     v
                           coach.py + Stockfish          ----->  your move  Nf3
                                                                 knight: g1 to f3
                                                                 +0.4
```

The one thing still missing is drawing on the watched board itself. The advice
is text in the ChessWatch window, and `holochess` draws its arrow only inside
its own window. Reading and coaching are joined; painting back onto the screen
you are reading is not.

## What is in here

| Folder | What it is | State |
| --- | --- | --- |
| [`chesswatch/`](chesswatch/) | Screen recorder. Finds the board, reads it, saves PGN + JSON. | Finished. 76 headless checks plus two on-screen tests. |
| [`chesswatch/coach.py`](chesswatch/coach.py) | Stockfish on the position being watched, in plain words. | Works. 18 checks. Off by default. |
| [`holochess/`](holochess/) | Local board, Stockfish 18, best move as a hologram arrow. | Works. 17 checks. |

Each folder has its own README with the long version.

## Run the recorder

```
cd chesswatch
pip install -r requirements.txt
python chesswatch.py
```

It starts watching by itself and finds the board on its own. There is nothing to
configure. Leave it running before you start a game and it numbers the moves
from 1; join part way through and it says so in the file, because nothing in a
picture of a board tells you how many moves came before.

Games land in `chesswatch/games/` as a matched pair:

```
2026-08-30_190621.pgn     open in any chess program
2026-08-30_190621.json    move by move, tagged me vs opponent
```

## Ask it what to play

Tick **best move**. Stockfish is started the first time you do, not before, and
the label under the board reads something like:

```
your move  Nf3  (knight: g1 to f3)  +0.4
```

It names the piece and both squares rather than only the notation, says what a
capture takes and when a move gives check, and counts a forced mate in moves.
That is aimed at a player who has not learned to read `Nxe5+` yet.

The engine runs on its own thread with a 300 ms budget, so the reader never
waits for it, and an answer to a position that has already been played past is
thrown away rather than shown against the wrong board. The switch is remembered
in `config.json`.

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

## Why read the board and not the move list

The first version read the move list with OCR and it failed completely. Chess.com
can be set to figurine notation, where the piece is a little icon rather than a
letter, so `Qf7+` reads as `f7+` and every move except a pawn push is rejected.

Reading the board has no such problem, because no piece is ever named. The
reader only sees that a dark piece left d8 and a dark piece arrived on h4, and
only one legal move does that, so the move is `Qh4`. Piece identity comes out of
the rules. Board themes, piece sets and notation style stop mattering.

Three things that took real work, all covered in
[`chesswatch/README.md`](chesswatch/README.md):

- **Animation.** Chess.com slides a piece to its destination, and mid slide the
  board shows a clean, still, legal move that never happened. `e3` sits on screen
  for about a tenth of a second during `e2-e4`. Counting agreeing reads does not
  help, because every read inside that window agrees. Only a move whose
  destination lies between its origin and a longer legal move by the same piece
  can be faked that way, so those have to hold for over a second and everything
  else is believed after a quarter of one.
- **Refusing to guess.** Three missed moves can transpose. A double push then an
  en passant capture leaves the same picture as a single push then an ordinary
  one. In those cases it records nothing rather than a plausible fiction.
- **A finished game is read only.** Clicking back through Game Review puts an old
  position on screen, which used to read as a takeback and overwrite the finished
  file with a fragment.

## Where the engine can and cannot draw

The arrow in `holochess` is a child widget of its own window rather than an
always on top overlay, and `holochess/shot.py` scans the whole desktop after
drawing one and fails the test if a single hologram coloured pixel turns up
outside the app. An earlier always on top version was caught painting its arrow
onto a chess board sitting behind it, which is where that test came from.

The coach in `chesswatch` is text in the ChessWatch window for the same reason.
It is off until you tick it on.

Worth saying once, since the reader works on any site: engine help during a live
rated game on someone else's platform is cheating there, whatever this code
happens to allow.

## Tests

```
cd chesswatch
python selftest.py      76 headless checks, including real screenshots
python coachtest.py     18 checks on the engine wrapper and its label
python settletest.py    move animation, driven off a real clock
python livetest.py      paints whole games on your desktop and records them

cd holochess
python smoke_test.py    engine, moves, hints, undo, flip
python shot.py          proves the arrow cannot escape the window
```

`livetest.py` and `shot.py` take over the screen while they run.

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
