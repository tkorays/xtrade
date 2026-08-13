"""Smoke tests for the one-shot import script's pure helpers."""

from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import text

from xtrade.data import get_engine
from xtrade.data.import_legacy import discover_files, normalize_frame


def _write_parquet(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "symbol": ["AAA"],
            "time": [pd.Timestamp("2025-01-01")],
            "open": [1.0],
            "high": [2.0],
            "low": [0.5],
            "close": [1.5],
            "pre_close": [1.4],
            "volume": [10],
            "amount": [15.0],
        }
    ).to_parquet(path)


def test_discover_files_minimal(tmp_path) -> None:
    _write_parquet(tmp_path / "hot" / "1d.parquet")
    assert discover_files(tmp_path) == [("1d", tmp_path / "hot" / "1d.parquet")]


def test_normalize_frame_minimal() -> None:
    df = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2025-01-02"),
                "symbol": "AAA",
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "pre_close": 1.4,
                "volume": 10,
                "amount": 15.0,
            }
        ]
    )
    out = normalize_frame(df, "1d")
    assert "pre_close" not in out.columns
    assert "date" not in out.columns
    assert "time" in out.columns
    assert out["interval"].iloc[0] == "1d"


@pytest.mark.integration
def test_legacy_import_round_trip_if_db_present() -> None:
    """Integration smoke: import 1 row, query it back. Skipped without DB or schema."""
    df = pd.DataFrame(
        [
            {
                "time": pd.Timestamp("2099-12-31"),
                "symbol": "ZZZ.TEST",
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 10,
                "amount": 15.0,
            }
        ]
    )
    norm = normalize_frame(df, "1m")

    with get_engine().connect() as conn:
        # Skip if the schema isn't migrated yet.
        present = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'kline_1m'"
            )
        ).scalar()
        if not present:
            pytest.skip("kline_1m table not present; run alembic upgrade head first")

    from xtrade.data.market_data import PostgresKLineRepository

    PostgresKLineRepository(batch_size=1000).upsert_bars(norm)

    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT symbol, ts FROM kline_1m WHERE symbol = 'ZZZ.TEST'")
        ).fetchone()
        assert row is not None
        conn.execute(text("DELETE FROM kline_1m WHERE symbol = 'ZZZ.TEST'"))
        conn.commit()
