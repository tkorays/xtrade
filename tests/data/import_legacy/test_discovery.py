"""Tests for the legacy parquet layout discovery."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from xtrade.data.import_legacy.discovery import discover_files


def _write_parquet(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"symbol": ["A"], "time": [pd.Timestamp("2025-01-01")]}).to_parquet(path)


def test_discover_files_returns_empty_when_root_missing(tmp_path: Path) -> None:
    assert discover_files(tmp_path / "missing") == []


def test_discover_files_finds_hot_1d(tmp_path: Path) -> None:
    _write_parquet(tmp_path / "hot" / "1d.parquet")
    found = discover_files(tmp_path)
    assert found == [("1d", tmp_path / "hot" / "1d.parquet")]


def test_discover_files_finds_hot_1m_sorted(tmp_path: Path) -> None:
    _write_parquet(tmp_path / "hot" / "1m" / "000002.SZ" / "2026.parquet")
    _write_parquet(tmp_path / "hot" / "1m" / "000001.SZ" / "2025.parquet")
    _write_parquet(tmp_path / "hot" / "1m" / "000001.SZ" / "2026.parquet")

    found = discover_files(tmp_path)
    interval_paths = [(interval, path.name) for interval, path in found]
    assert [(interval, name) for interval, name in interval_paths] == [
        ("1m", "2025.parquet"),
        ("1m", "2026.parquet"),
        ("1m", "2026.parquet"),
    ]


def test_discover_files_finds_cold_layout(tmp_path: Path) -> None:
    _write_parquet(tmp_path / "cold" / "1d" / "000001.SZ.parquet")
    _write_parquet(tmp_path / "cold" / "1m" / "000001.SZ" / "2024.parquet")
    found = discover_files(tmp_path)
    intervals = [iv for iv, _ in found]
    assert intervals == ["1d", "1m"]


def test_discover_files_1d_before_1m(tmp_path: Path) -> None:
    _write_parquet(tmp_path / "hot" / "1m" / "000001.SZ" / "2026.parquet")
    _write_parquet(tmp_path / "hot" / "1d.parquet")
    found = discover_files(tmp_path)
    assert [iv for iv, _ in found] == ["1d", "1m"]
