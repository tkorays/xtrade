"""Normalise a legacy parquet DataFrame into the ``KLineRepository`` contract.

The legacy layout uses column name ``date`` for daily bars and ``time``
for minute bars. The new ``xtrade`` repository expects a generic
``time`` column regardless of frequency, with an ``interval`` column
driving the routing. We also drop ``pre_close`` — the new schema does
not store it (recoverable from the ``adj_factor`` table on the read
path).
"""

from __future__ import annotations

import pandas as pd

# Output column order — must match ``KLINE_REQUIRED_COLUMNS`` on
# ``xtrade.data.market_data.kline``.
_OUTPUT_COLUMNS: tuple[str, ...] = (
    "symbol",
    "time",
    "interval",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
)


def normalize_frame(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Return ``df`` normalised to the ``KLineRepository.upsert_bars`` contract.

    Steps:
    1. ``1d`` intervals rename ``date`` → ``time`` for parity with 1m.
    2. Drop ``pre_close`` (intentional; the new schema doesn't store it).
    3. Add the ``interval`` column so the repository can route the frame.
    4. Coerce ``time`` to tz-aware UTC; numeric columns to ``float64`` /
       ``int64``.
    5. Return columns in the exact order required by ``KLINE_REQUIRED_COLUMNS``.

    Raises:
        ValueError: if ``df`` is missing any required column after rename /
            drop.
    """
    if interval not in ("1d", "1m"):
        raise ValueError(f"unsupported interval {interval!r}; expected '1d' or '1m'")

    out = df.copy()

    if interval == "1d" and "date" in out.columns and "time" not in out.columns:
        out = out.rename(columns={"date": "time"})

    if "pre_close" in out.columns:
        out = out.drop(columns=["pre_close"])

    out["interval"] = interval
    out["time"] = pd.to_datetime(out["time"], utc=True)

    required = ("symbol", "time", "open", "high", "low", "close", "volume", "amount")
    missing = [c for c in required if c not in out.columns]
    if missing:
        raise ValueError(f"normalize_frame: missing columns {missing}")

    # Drop rows with NaN in any numeric column — the schema enforces
    # NOT NULL on every price / volume / amount column, so a row with
    # a missing value cannot be INSERTed. The legacy ``LocalDataSystem``
    # stored some rows with NaN; we drop them rather than impute.
    numeric_cols = ("open", "high", "low", "close", "volume", "amount")
    before_rows = len(out)
    out = out.dropna(subset=numeric_cols)
    dropped = before_rows - len(out)
    if dropped > 0:
        logger = __import__("logging").getLogger(__name__)
        logger.info("normalize_frame: dropped %d rows with NaN numeric columns", dropped)

    for col in ("open", "high", "low", "close", "amount"):
        out[col] = out[col].astype("float64")
    out["volume"] = out["volume"].astype("int64")
    out["symbol"] = out["symbol"].astype(str)

    # Reorder so the output is row-stable and matches the contract.
    return out.loc[:, list(_OUTPUT_COLUMNS)]


__all__ = ["normalize_frame"]
