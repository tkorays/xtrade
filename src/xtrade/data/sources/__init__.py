"""Data sources: producer-side abstraction for fetching market reference data."""

from xtrade.data.sources.base import DataSource, SourceRegistry
from xtrade.data.sources.mock_source import InMemoryMockSource
from xtrade.data.sources.pump import PumpResult, pump

__all__ = [
    "DataSource",
    "InMemoryMockSource",
    "PumpResult",
    "SourceRegistry",
    "pump",
]
