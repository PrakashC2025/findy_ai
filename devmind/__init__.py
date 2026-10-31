"""DevMind — free, local-first coding intelligence.

The package is intentionally small at import time: nothing here starts a
process, opens a socket, or reads the user's source tree.  Heavy work lives in
:mod:`devmind.hardware`, :mod:`devmind.registry` and (later) the indexer, and is
only invoked by the CLI/API.
"""

from __future__ import annotations

__version__ = "0.6.2"
__all__ = ["__version__"]
