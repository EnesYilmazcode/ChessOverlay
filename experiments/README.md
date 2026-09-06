# The six things that were tried

Six approaches to telling chess pieces apart, each written without seeing the
others, all scored on `chesswatch/bench.py`. Kept because the issues cite them
and because four of the six are more useful as a record of what does not work
than as code.

Nothing here is imported by the program. They are entrants for the bench.

| file | idea | result |
| --- | --- | --- |
| `try_correlation.py` | each square normalised by its own brightness, matched by correlation | **won.** 83.7% of boards read whole against 22.7%, zero corrupted. #44 |
| `try_gradient.py` | four directional derivatives, 8x8 cells a square | second. 65.8% whole, zero corrupted. #39 |
| `try_blur.py` | measure the capture's blur off the board's own square edges and undo it | shipped, as PR #45. Zero wrong. #40 |
| `try_learned.py` | a small trained model over 31 piece sets | 0.25% wrong on unseen sets, but needs numpy and is over budget. #41 |
| `try_shape.py` | topology and silhouette descriptors | lost. The wins in it came from the segmentation, not the descriptors |
| `try_design.py` | question the architecture rather than the matcher | the most useful of the six. #42 |

## What was learned that outlived the code

- 85% of wrong answers were a white piece read as an empty square, never a black
  one. Blur erases a white piece's thin dark edge and smears its light body out
  of range; a black piece is a solid fill.
- Deciding whether a square is empty wants a different measurement from deciding
  what is on it. Two of the six found that separately, with no overlap between
  the populations either time.
- The arrow this program draws on the board is invisible to the reader by
  design, and that protection is a fact about brightness. Two of the six
  destroyed it without noticing. #43.
- Masking the arrow and filling the hole manufactures confidence: the wrong
  answers score higher than the right ones, because the score is grading the
  reconstruction. Ignore the covered pixels instead.
- The resolution argument three measurements disagreed about was an
  occupancy-threshold artifact, not a resolution curve.

## Dead ends, measured rather than argued

Topology, knight asymmetry, shape descriptors on a fixed segmentation, a
template-free classifier, HOG block normalisation, alignment search, and the
theory that the last-move highlight causes fabrications. All in #42.
