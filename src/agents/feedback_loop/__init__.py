"""Feedback Loop System engine."""
import sys
from pathlib import Path

_package_dir = str(Path(__file__).parent)
if _package_dir not in sys.path:
    sys.path.insert(0, _package_dir)

from .agent import FeedbackLoopAgent  # noqa: E402

__all__ = ["FeedbackLoopAgent"]
