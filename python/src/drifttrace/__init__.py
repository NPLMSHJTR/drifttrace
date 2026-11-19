"""drifttrace: metric anomaly detection with labelled-incident scoring."""

from __future__ import annotations

__version__ = "0.1.0"

from .evaluate import evaluate  # noqa: E402
from .gen import generate, write_truth  # noqa: E402

__all__ = ["__version__", "evaluate", "generate", "write_truth"]
