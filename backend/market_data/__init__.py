"""market_data — background data-collection modules.

Separate from backend/strategies/ — nothing here places orders or reads
strategy state. Each collector here owns its own DB file and its own
background thread, isolated from the live trading path.
"""
