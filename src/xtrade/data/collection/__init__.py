"""Data-collection services.

Wraps producer-side :class:`DataSource` implementations in domain-aware
collection flows that own the watermark bookkeeping
(``data_sync_state`` table).
"""
