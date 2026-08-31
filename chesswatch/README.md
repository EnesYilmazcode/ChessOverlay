# ChessWatch

Watches the chess.com board on your screen and saves every game to your own disk
as PGN and JSON. Desktop app, not a browser extension. It never touches your
account and nothing leaves the machine.

## Run it

    run.bat

Or `python chesswatch.py`. It starts watching by itself and finds the board on
its own. There is nothing to set up.

Recording starts from the opening position, so start a new game rather than
joining one halfway through.

## What you get

Every game lands in `games\` as a matched pair:

    games\2026-08-30_190621.pgn     open in any chess program
    games\2026-08-30_190621.json    move by move, tagged me vs opponent

The JSON is the one for studying:

```json
{ "ply": 6, "number": 4, "side": "white", "san": "O-O", "by": "me" }
```

The file also carries `my_color`, `result`, `termination` and `outcome`, so you
can filter later for the games you lost as black.

Files are written as you play, not at the end, so closing the browser mid-game
loses nothing. Starting a new game closes out the old one automatically.

## How it works

It reads the **board**, not the move list.

```
   chess.com on screen
          |
          |  find the board by its two square colours,
          |  then prove it is really a checkerboard
          v
   +--------------+     each square -> white piece / black piece / empty
   | 64 squares   | --> empty squares score 0.000, pieces score 0.15-0.43,
   +--------------+     so the margin is huge and themes do not matter
          |
          v
   +--------------+     which legal move turns the previous position
   | python-chess | --> into the one now on screen? That is the move.
   +--------------+
          |
          v
   games\*.pgn + *.json
```

Three things fall out of doing it this way:

**Piece names come from the rules, not from pixels.** The reader only sees that
a dark piece left d8 and a dark piece arrived on h4. Only one legal move does
that, so the move is `Qh4`. Nothing has to recognise a queen. This is why
figurine notation in the move list, board themes and piece sets are all
irrelevant.

**It waits out the animation.** chess.com slides a piece to its destination over
a couple of hundred milliseconds, and part way through, the piece is sitting on
a square in between. For `e2-e4` the board reads as a clean, still `pawn on e3`
for about a tenth of a second, and `e3` is itself a legal move. A rook sliding
h3 to a3 passes over g3, f3, e3 and d3. Castling reads as `Kf1` on the way.

So the screen genuinely shows a legal move that never happened, and counting
agreeing reads cannot help, because every read inside that window agrees with
the others. Two things separate a real move from a piece in flight:

- Only a move whose destination lies **between** its origin and a longer legal
  move by the same piece can be faked this way. Those are marked as suspect and
  have to hold still for over a second before they are believed.
- Everything else, which is most of a game, is believed after a quarter of a
  second.

A recorder can afford the delay. Being wrong is what it cannot afford.

**A frame it cannot read is a frame it ignores.** The position on screen has to
match a legal successor exactly, all 64 squares. A piece mid-animation, a piece
being dragged, or a dialog over the board matches nothing, so it waits instead
of guessing. If it misses a frame entirely and two moves go by, it searches two
moves deep and catches up.

**Promotions are read, not assumed.** A promoted queen and a promoted knight
leave an identical white/black picture, so the piece reader is asked what
actually appeared on the promotion square. All four promotions come out right;
before this an underpromotion was silently written down as a queen.

**The result comes off the board.** Checkmate, stalemate, insufficient material
and repetition are read from the position itself. Resignations and timeouts
leave no trace on the board, so those games are saved with `Result "*"`.

Your colour comes from which way the board is facing, since chess.com always
puts you at the bottom.

## The piece checker

Everything above only ever asks "white piece, black piece, or empty?". That is
fast, but it cannot tell you what is on a square it has lost track of. So there
is a second, slower reader that identifies the actual piece on all 64 squares,
and it runs every few seconds, whenever the fast reader has been stuck for a
while, and whenever you press **check the pieces now**.

It works the same way as everything else here: a square is reduced to a mask of
its very bright and very dark pixels, which is the piece and nothing else, since
board colours, highlights, the check marker and the coordinate labels all fall
between the two cutoffs. That mask is matched against the twelve piece shapes.
Colour is settled first, so each match is a 1-of-6 choice.

The templates ship in `pieces.png`, and are relearned from your own screen every
time a game starts from the opening position, where what sits on every square is
already known. The status line says which set is in use.

The checker does four things:

- **Confirms** the game so far really is what is on screen.
- **Catches up** when moves were missed, up to three of them.
- **Rewinds** a takeback.
- **Joins a game already in progress**, which the fast reader cannot do, because
  it has no way to know what an unfamiliar piece is.

### If it joins part way through

Nothing in a picture of a board says how many moves were played before you
started watching. So a game picked up part way through is counted from where
watching began, and both the app and the saved files say so: the move list is
prefixed `+1, +2, ...`, the PGN carries the position it started from plus a note
in the Annotator header, and the JSON has `joined_in_progress` and a `numbering`
field spelling it out.

If you want move numbers that line up with chess.com, leave ChessWatch running
before you start the game. Then it sees the opening position, numbers from 1,
and relearns the piece shapes from your own screen at the same time.

**A finished game is read-only.** chess.com opens Game Review the moment a game
ends, and clicking back through it puts an earlier position on screen. That used
to read as a takeback and overwrite the finished file with a fragment. Games
that end off the board, by resignation or timeout, cannot be detected as
finished at all, so on top of that a saved game is never allowed to get shorter
on disk. A real takeback diverges rather than truncating, and is still recorded.

It will not guess. Several cases where it deliberately stops:

*Three missed moves that could have happened in either order.* 1.e4 e5 2.Nf3 and
1.Nf3 e5 2.e4 leave exactly the same picture. Two missed moves can never
transpose, because the colours alternate, so those are always safe to fill in.
Three can, and rather than write down a plausible order that did not happen, it
says it lost the thread and starts a fresh record from the position on screen.

*Two stories that leave the same picture.* A pawn's double push followed by an
en passant capture leaves exactly the same board as a single push followed by an
ordinary one. A capture and a recapture on the same square hide that square
completely. If a frame is missed at that moment, both readings fit, and neither
can ever be told apart afterwards, so it records nothing rather than a coin
flip.

*Joining a game before it knows which way the board faces.* A board rotated
half a turn is itself a legal game, so a knight or queen move explains the
screen equally well both ways up. A pawn move does not, because pawns only move
one way, so the first pawn move settles it, usually within seconds. If you would
rather not wait, set **I play** to white or black and any move will do.

## Things it copes with

**A small browser window.** The move list is never read, so it does not matter
whether that panel is on screen. Only the board has to be visible. Reading is
correct down to a board of about 130 pixels, which is far smaller than anything
you would actually play on. A normal small window is around 660 pixels.

**Two monitors.** It scans both and reports the board in absolute desktop
coordinates, so the board can sit on either screen.

**Moving or resizing the window mid-game.** If the board stops being where it
was, it notices within a couple of seconds and hunts for it again, then carries
on with the same game.

**A busy wallpaper.** Board detection is checked against a photo background with
desktop icons and browser chrome around it.

Only one copy may run at a time. Two copies watching the same screen write two
separate files for the same game, so a second launch says so and exits.

## If it does not pick up the board

The status line tells you whether it has found a board and whether it has locked
onto a game. Tick **show board** to see the position it is reading, which should
match your screen exactly.

- "looking for a chess board" means nothing on screen matched. Check the board
  is not covered by another window.
- Stuck on "waiting for a pawn move to tell which way up" means it has found a
  game in progress and needs one pawn move before it can tell which way the
  board is facing. Setting **I play** removes the wait.
- Use **pick board manually** to drag a box around the board corner to corner.
  That choice is remembered in `config.json`.

Detection is tuned for chess.com's default green board. A different board theme
needs `LIGHT_SQUARE` and `DARK_SQUARE` in `watcher.py` changed to match.

## Checking it still works

    python selftest.py      76 checks, including real screenshots
    python settletest.py    move animation, with the screen on a clock
    python livetest.py      full loop against the real screen

`livetest.py` cuts real chess.com piece sprites out of a screenshot, paints
whole games onto your actual desktop, and runs the real capture worker against
them. It plays an 18 move game with castling on both sides, a knight sacrifice
and a queen trade, then a second game from black's side ending in checkmate, and
checks every move, both colours, the result, and the files on disk. The
second game is deliberately a small board on the second monitor, and a third
run skips three moves with no frames in between to make the checker recover
them.

`settletest.py` replaces the screen with a clock-driven script, so the
animation has a real duration rather than a frame count. It covers pawn pushes,
a sliding rook, a bishop crossing three legal squares, castling, and a bot
replying while your own move is still moving, at animation speeds from 200ms to
900ms.

`fakeboard.py` is the renderer those tests use. It cuts its piece sprites out
of a real screenshot, and checks itself before the tests trust it.

The screenshots the tests read live in `testdata\`. They are real chess.com
windows with everything outside the board blacked out, so they carry no account
name. To run the same checks against your own board theme, point
`CHESSWATCH_TESTDATA` at a folder holding your own `1.png`, `2.png`, `4.png`
and `5.png`, or pass paths to `selftest.py` on the command line.

## Needs

- Python 3
- `pip install -r requirements.txt` (mss, pillow, chess)

No OCR, no Tesseract. An earlier version read the move list text and broke on
figurine notation, where the piece is an icon rather than a letter.

`make_templates.py` regenerates `pieces.png` from a screenshot, if the board
ever changes appearance.
