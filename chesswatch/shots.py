"""Where the test screenshots live.

The fixtures in `testdata/` are real chess.com screenshots with everything
outside the board blacked out. Point CHESSWATCH_TESTDATA at a folder of your
own `1.png`, `2.png`, `4.png`, `5.png` and `6.png` to run the same checks
against your board theme. 6.png is a second piece set rather than a second
board theme, and banktest.py is the only thing that reads it.
"""

import os

DIR = os.environ.get("CHESSWATCH_TESTDATA") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "testdata")


def shot(name):
    """Path to a reference screenshot. May not exist; callers say so."""
    return os.path.join(DIR, "%s.png" % name)
