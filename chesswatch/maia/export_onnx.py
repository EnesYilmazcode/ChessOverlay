"""How maia3-5m.onnx was made. Build time only: nothing at run time uses this.

Written for ChessWatch against CSSLab/maia3 at 1e13597, September 2026. It
changes the model before exporting it to ONNX, as described below.

    python export_onnx.py <clone of CSSLab/maia3> <maia3-5m.pt> [out.onnx]

Needs torch (2.7.1 was used) and onnx on top of what maia3 itself needs, which
is why it is a script someone runs once rather than something ChessWatch does.
The checkpoint comes from Hugging Face, UofTCSSLab/Maia3-5M, file maia3-5m.pt.

The model is loaded exactly the way maia3/uci.py loads the 5M preset, and
exported at batch 1 in float32, opset 17, with the TorchScript exporter.

One change is made to the model before export, and checked to change nothing:
torch 2.7.1's TorchScript exporter has no symbolic for aten::rms_norm at any
opset, so torch.nn.RMSNorm.forward is replaced by the same formula written in
basic ops, with the same default epsilon. The largest logit difference that
makes on a batch of random boards is printed before exporting.
"""

import os
import sys
import time

if len(sys.argv) < 3:
    sys.exit(__doc__)

MAIA3 = os.path.abspath(sys.argv[1])
CKPT = os.path.abspath(sys.argv[2])
OUT = os.path.abspath(sys.argv[3]) if len(sys.argv) > 3 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "maia3-5m.onnx")
sys.path.insert(0, MAIA3)

import torch  # noqa: E402

from maia3.uci import load_model, parse_args  # noqa: E402


def main():
    # The same path the maia3-5m launcher takes: preset config from the
    # registry, temperature 0, but the checkpoint given locally and on CPU.
    cfg = parse_args(["--temperature", "0", "--model", "maia3-5m",
                      "--checkpoint-path", CKPT, "--device", "cpu"])
    model = load_model(cfg)
    print("params", sum(p.numel() for p in model.parameters()))

    width = 12 * cfg.history + 1
    tokens = torch.zeros((1, 64, width), dtype=torch.float32)
    elos = torch.tensor([1500], dtype=torch.long)

    g = torch.Generator().manual_seed(0)
    boards = (torch.rand((4, 64, width), generator=g) > 0.9).float()
    ratings = torch.tensor([500, 1000, 1500, 2200])
    with torch.no_grad():
        before = model(boards, ratings, ratings)

    def rms_forward(self, x):
        eps = self.eps if self.eps is not None else torch.finfo(x.dtype).eps
        return x * torch.rsqrt(x.pow(2).mean(dim=-1, keepdim=True) + eps) * self.weight

    torch.nn.RMSNorm.forward = rms_forward
    with torch.no_grad():
        after = model(boards, ratings, ratings)
    print("rmsnorm rewrite, largest logit difference per output",
          [float((a - b).abs().max()) for a, b in zip(before, after)])

    t = time.perf_counter()
    torch.onnx.export(
        model,
        (tokens, elos, elos),
        OUT,
        input_names=["tokens", "self_elos", "oppo_elos"],
        output_names=["logits_move", "logits_value", "logits_ponder"],
        opset_version=17,
        do_constant_folding=True,
        dynamo=False,
    )
    print("wrote %s, %.1f MB, in %.1fs"
          % (OUT, os.path.getsize(OUT) / 2**20, time.perf_counter() - t))


if __name__ == "__main__":
    main()
