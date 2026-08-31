# HoloChess

Local chess against Stockfish 18, with a translucent hologram arrow that shows
the best move when you ask for it. Everything runs on this machine. Nothing
talks to any chess website.

## Run it

    pip install -r requirements.txt
    python holochess.py

Stockfish is not in the repository: the Windows build is 114 MB, above
GitHub's file size limit, and it is GPL licensed separately from this code.
Get it from [stockfishchess.org/download](https://stockfishchess.org/download/)
and either unpack it into `engine/stockfish/` or point `STOCKFISH_PATH` at the
binary. `stockfish` on your `PATH` is found too. Without it the app still opens
and the board still works, and the status line says the engine is missing.

## Keys

| Key | Does |
| --- | --- |
| `H` | Show the best move as a hologram arrow |
| `Esc` | Clear the arrow |
| `F` | Flip the board |
| `Ctrl+Z` | Take back your last move |
| `Ctrl+N` | New game |

Tick **Keep it on every turn** to have the arrow appear automatically each time
it is your move.

## Settings

**Opponent strength** is Stockfish's own `UCI_Elo`, 1320 to 3190. The hint is
always computed at full strength no matter where the slider sits, so you can
play a weak opponent and still get a perfect answer when you ask.

The engine gets all but four of your cores and a 2 GB hash. A move takes
350 ms, a hint 300 ms.

## Why the arrow is a child window

The hologram is a child widget of the app window, not a top-level always-on-top
window.

The first version was always-on-top. The screenshot test caught it painting its
arrow directly onto a chess.com board that happened to be open behind it. An
always-on-top window floats above every other application by definition, and
binding it to window focus did not fix it, because Windows would not hand the
foreground to the app. Making it a child of the board widget means it is clipped
to HoloChess and cannot render over anything else. `shot.py` scans the whole
desktop for hologram-cyan pixels outside the app window and fails if it finds
any.

## Files

```
holochess.py     the app
engine/          where Stockfish goes; not in the repository
make_icon.py     regenerates holochess.ico
smoke_test.py    headless: engine, moves, hints, undo, flip  (17 checks)
shot.py          on-screen: renders the arrow, proves it cannot escape the window
```

## Running the tests

```
python smoke_test.py
python shot.py
```

`shot.py` writes `shot_a_app.png` (the app with the arrow up) and
`shot_b_desktop.png` (the whole desktop, which must contain no arrow outside
the window).

## A desktop shortcut, on Windows

```powershell
$here = (Resolve-Path .).Path
$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'HoloChess.lnk'))
$s.TargetPath = (Get-Command pythonw).Source
$s.Arguments = '"' + (Join-Path $here 'holochess.py') + '"'
$s.WorkingDirectory = $here
$s.IconLocation = (Join-Path $here 'holochess.ico')
$s.Save()
```

`pythonw` rather than `python` keeps the console window from sitting behind the
app.
