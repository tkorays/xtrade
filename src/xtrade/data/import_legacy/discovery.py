"""File discovery for the legacy K-line parquet layout.

The legacy :class:`LocalDataSystem` writes four kinds of files:

- ``<root>/hot/1d.parquet`` — one global file, multi-symbol rows.
- ``<root>/hot/1m/{symbol}/{year}.parquet`` — per-symbol, per-year.
- ``<root>/cold/1d/{symbol}.parquet`` — per-symbol cold archive.
- ``<root>/cold/1m/{symbol}/{year}.parquet`` — per-symbol, per-year cold.

This walker returns the union of every present file as a list of
``(interval, path)`` tuples, ordered 1d first then 1m (so the larger
single 1d file is processed up front; the multi-file 1m corpus can
fan out to the worker pool).
"""

from __future__ import annotations

from pathlib import Path


def discover_files(root: Path) -> list[tuple[str, Path]]:
    """Walk ``root`` and return every legacy K-line parquet as
    ``(interval, path)`` tuples.

    The function is defensive: missing directories are silently skipped,
    non-``.parquet`` files are ignored, and ``root`` is allowed to be
    missing entirely (returns ``[]``).
    """
    root = Path(root)
    if not root.exists():
        return []

    found: list[tuple[str, Path]] = []

    # ---- 1d ----
    hot_1d = root / "hot" / "1d.parquet"
    if hot_1d.is_file():
        found.append(("1d", hot_1d))
    cold_1d_dir = root / "cold" / "1d"
    if cold_1d_dir.is_dir():
        for path in sorted(cold_1d_dir.glob("*.parquet")):
            if path.is_file():
                found.append(("1d", path))

    # ---- 1m (sorted by symbol then year for deterministic order) ----
    hot_1m_dir = root / "hot" / "1m"
    if hot_1m_dir.is_dir():
        for symbol_dir in sorted(p for p in hot_1m_dir.iterdir() if p.is_dir()):
            for path in sorted(symbol_dir.glob("*.parquet")):
                if path.is_file():
                    found.append(("1m", path))
    cold_1m_dir = root / "cold" / "1m"
    if cold_1m_dir.is_dir():
        for symbol_dir in sorted(p for p in cold_1m_dir.iterdir() if p.is_dir()):
            for path in sorted(symbol_dir.glob("*.parquet")):
                if path.is_file():
                    found.append(("1m", path))

    return found


__all__ = ["discover_files"]
