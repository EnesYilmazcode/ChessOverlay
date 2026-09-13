# maia

The red arrow in ChessWatch names the worse move a player of a chosen rating is
most likely to play. This folder is the part that knows what players of a given
rating play: **Maia-3 5M**, a model trained on human games by the Computational
Social Science Lab at the University of Toronto, run with onnxruntime.

**It is a separate program, under a different licence from the rest of this
repository.** Everything in this folder is AGPL-3.0, see [LICENSE](LICENSE).
ChessWatch, which is MIT, starts `engine.py` as a child process and exchanges
lines of JSON with it over a pipe, the same way it starts and talks to
Stockfish. No file outside this folder imports anything in it, and
`maiatest.py` checks that.

## What is here

| File | What it is |
| --- | --- |
| `engine.py` | The program. Reads `{"fen": ..., "elo": 800}` a line at a time and answers with every legal move and its probability, most likely first. The docstring has the whole protocol. |
| `maia3-5m.onnx` | The model, 21.2 MB, float32. |
| `export_onnx.py` | How the model file was made. Build time only, and the only thing here that needs torch. |
| `LICENSE` | The GNU Affero General Public License, version 3. |

`engine.py` needs onnxruntime, numpy (which onnxruntime installs) and
python-chess, all in `chesswatch/requirements.txt`. It uses two threads.

## Where it comes from

- Code: [CSSLab/maia3](https://github.com/CSSLab/maia3) at commit
  `1e13597c42d4858b7cfd7cfdae01e297263364b2`, AGPL-3.0. The board encoding and
  the move decoding in `engine.py` are ported from its `dataset.py`, `utils.py`
  and `uci.py`.
- Weights: [UofTCSSLab/Maia3-5M](https://huggingface.co/UofTCSSLab/Maia3-5M) on
  Hugging Face, file `maia3-5m.pt` at commit
  `b6559de2398d7140b985f28fd2c19fb5e47ddabe` (sha256
  `ba14208b2992d85502f5fb501934abf6aaaeb355e9f3fdf90e326911f562524f`). The model
  card defers to the code repository for the licence.
- Paper: Monroe, Eilender, Chalmers, Tang and Anderson, *Chessformer: A Unified
  Architecture for Chess Modeling*, ICLR 2026,
  [arXiv:2605.19091](https://arxiv.org/abs/2605.19091).

## How the model file was made

    python export_onnx.py <clone of CSSLab/maia3> <maia3-5m.pt>

with torch 2.7.1 and onnx 1.22.0. The script loads the checkpoint exactly the
way `maia3/uci.py` loads the 5M preset and exports it at batch 1, float32,
opset 17. It makes one change first, because that torch's exporter cannot
translate `RMSNorm`: the layer is rewritten in basic operations with the same
epsilon, and the script prints how far that moves the output before it
exports, which for this checkpoint is 0.0 on every output. The result has sha256
`56ee3efbbfcded26c82856b6f518040a46e9fa726e79785748dc3aac9ea579e0`, and running
the script again produces the same bytes.

It was then checked against the original. Over 90 positions sampled from
recorded games, at ratings 500, 1000 and 1500, `maia3`'s own UCI engine running
the torch checkpoint and this file under onnxruntime agree on the most likely
move in all 270 cases, and no move's probability differs by more than 0.00001.
`maiatest.py` keeps 18 of those answers, and fails if they stop matching.

## What it does not do yet

The model can look at the seven positions before the current one, and players
do play differently depending on how they got somewhere. ChessWatch only hands
it the board on screen, which `engine.py` repeats into all eight slots, the
same thing `maia3`'s own engine does when it is not given the moves. Over those
270 answers the most likely move is the same either way 86% of the time.
Passing the real moves in is a change to make on both sides of the pipe.
