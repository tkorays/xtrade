"""One-shot loader for the legacy ``mos_quant`` K-line parquet layout.

This package exposes the file-discovery and DataFrame-normalisation
helpers used by ``scripts/import_legacy_bars.py``. It is **not** part
of the recurring ingestion pipeline; ``xtrade.data.sources.pump`` owns
that responsibility.

Walk the legacy layout::

    from xtrade.data.import_legacy import discover_files, normalize_frame
    files = discover_files(Path("F:/Quant/data/bars"))
    df = pd.read_parquet(files[0][1])
    normalised = normalize_frame(df, files[0][0])
"""

from __future__ import annotations

from xtrade.data.import_legacy.discovery import discover_files
from xtrade.data.import_legacy.transform import normalize_frame

__all__ = ["discover_files", "normalize_frame"]
