# ChessOverlay Ship Status

**Headline: baseline audit clean — 4/11 issues closed (PRs #13, #12 merged), 7 open, one WIP branch for #9, all suites green on main (87/87, 25/25, 18/18), zero drift from what the orchestrator reported.**

Last verified: 2026-09-01, by the board manager (independent `gh`/`git`/pytest audit, not taken on the orchestrator's word).

## Issues (11 total)

| # | Title | Status | Closed by |
|---|---|---|---|
| 1 | Board hunt burns ~80% of a CPU core whenever no board is on screen | OPEN | — |
| 2 | `grab()` builds a new mss instance on every screenshot | OPEN | — |
| 3 | `read_occupancy` counts 20k pixels in a Python loop, 5.1 ms/frame | CLOSED | [PR #13](https://github.com/EnesYilmazcode/ChessOverlay/pull/13) (merged) |
| 4 | Piece colour decided before shape (33/64 squares "wrong" on testdata/5.png) | CLOSED | [PR #12](https://github.com/EnesYilmazcode/ChessOverlay/pull/12) (merged) |
| 5 | No margin between best/second-best template match | CLOSED | [PR #12](https://github.com/EnesYilmazcode/ChessOverlay/pull/12) (merged) |
| 6 | One unreadable square disables the entire check pass | OPEN | — (wave 1) |
| 7 | Templates only relearned at game start, mid-game resize leaves them stale | CLOSED | [PR #12](https://github.com/EnesYilmazcode/ChessOverlay/pull/12) (merged) |
| 8 | Whose turn it is never confirmed against the screen | OPEN | — (wave 3) |
| 9 | Show best move continuously, for both sides | OPEN | — (wave 1; WIP on `coach-both-sides`, unverified) |
| 10 | Engine think time hardcoded to 0.30s | OPEN | — (wave 4) |
| 11 | Halve the UI: best move as hero, diagnostics behind a toggle | OPEN | — (wave 5, last — rewrites the window) |

## PRs

| # | Title | Branch | State | Closes |
|---|---|---|---|---|
| [#13](https://github.com/EnesYilmazcode/ChessOverlay/pull/13) | Read the 64 squares off the bytes instead of one pixel at a time | `perf-read-occupancy` | MERGED 2026-09-01 04:44 UTC | #3 |
| [#12](https://github.com/EnesYilmazcode/ChessOverlay/pull/12) | Say "?" when the pixels do not settle which piece is on a square | `piece-reading-accuracy` | MERGED 2026-09-01 12:09 UTC | #4, #5, #7 |

## Branches

| Branch | Head commit | Status |
|---|---|---|
| `main` | `618d9c3` (2026-09-01 11:19 -0400) | Clean, up to date with local checkout |
| `coach-both-sides` | `1c87f69` (2026-09-01 11:17 -0400) | 1 commit ahead of main: `WIP: arrow for both sides in a second reader-safe colour`. **Unverified WIP for #9.** No PR opened. |

No other remote branches exist (`git ls-remote --heads origin` returns exactly these two).

## Test suite numbers (measured by board manager, on `main` @ `618d9c3`, clean tree)

| Suite | Result |
|---|---|
| `selftest.py` | **87/87 passed** |
| `piecetest.py` | **25/25 passed** |
| `coachtest.py` | **18/18 passed** (with `STOCKFISH_PATH` set to the bmi2 engine) |

`settletest.py`, `overlaytest.py`, `livetest.py` — **not run**. They take over the live desktop; hard rule, never run them.

## Planned wave order (touch conflicts: chesswatch.py hit by #1/#8/#9/#10/#11, watcher.py hit by #6/#8)

1. **#6 and #9** (first)
2. **#1 + #2** (together)
3. **#8**
4. **#10**
5. **#11** (last — rewrites the window)

Status: no wave has landed yet. Only artifact so far is the WIP `coach-both-sides` branch for #9 (wave 1), unmerged, no PR. Nothing is out of order.

## Do not re-derive (counterintuitive, settled facts)

- **Issue #4 was FALSE.** Its "33 of 64 squares wrong on testdata/5.png" came from diffing a checkmate position against the standard starting position. `main`'s reader was correct on all 64 squares all along — verified in PR #12's own writeup (four independent ground-truth checks: eye, `read_occupancy` cross-check, `selftest.py`'s hardcoded occupancy, python-chess validation). Closed as done only because PR #12 made the change the issue asked for anyway (colour-as-vote instead of colour-as-gate).
- **Issue #3's own suggested fix (`Image.point()` + `resize((8,8), Image.BOX)`) was also wrong**, and was not used. PR #13 built it and found the 8-bit BOX average collapses adjacent pixel counts onto the same byte (`255/324 < 1` unit/pixel) — 35 of 400 crafted squares turned a win into a wrongly-scored tie. PR #13 shipped a different approach (a 256-entry translate table + `bytes.count`).

## Drift found

None. Every claim in the orchestrator's brief checked out against `gh`/`git` directly:
- #3/#4/#5/#7 closed, via PR #13 and PR #12 respectively, both merged — confirmed via `closingIssuesReferences` on both PRs, not just title-matching.
- Open set is exactly #1, #2, #6, #8, #9, #10, #11 — confirmed.
- Exactly one extra branch, `coach-both-sides`, exactly one WIP commit, whose message ("arrow for both sides in a second reader-safe colour") matches issue #9's ask — confirmed, no PR exists for it yet.
- Test suite pass counts match what PR #12's own body claims as the post-merge state (87/87, 25/25, 18/18) — confirmed by running them myself, not by trusting the PR text.

This section will be updated in place if a future wave report disagrees with what `gh`/`git`/the suites actually show.
