"""Performance Analytics Agent — top-level re-export.

The engine implementation lives in ``app/``. This module re-exports the public
agent so the package layout matches the codebase-structure guide.
"""

import sys
from pathlib import Path

_package_dir = str(Path(__file__).parent)
if _package_dir not in sys.path:
    sys.path.insert(0, _package_dir)

from app.agent import PerformanceAnalyticsAgent  # noqa: E402

__all__ = ["PerformanceAnalyticsAgent"]
