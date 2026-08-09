"""Engine: backtest + live drivers.

Public surface:

- :class:`BacktestEngine` — deterministic, explicit-step backtest driver.
- :class:`LiveEngine` — event-driven live driver.
- :class:`LiveMarketSource` — Protocol for live market data.
- :class:`RunSummary` — terminal state of a backtest run.
- :class:`EngineUsageError` — engine-side usage violation.
- :func:`load_strategy` / :class:`StrategyLoadError` — CLI loader.
"""

from xtrade.engine.backtest import BacktestEngine, MarketDataFeed
from xtrade.engine.clock import EngineUsageError, RunSummary
from xtrade.engine.live import LiveEngine, LiveMarketSource
from xtrade.engine.runner import StrategyLoadError, load_strategy

__all__ = [
    "BacktestEngine",
    "EngineUsageError",
    "LiveEngine",
    "LiveMarketSource",
    "MarketDataFeed",
    "RunSummary",
    "StrategyLoadError",
    "load_strategy",
]
