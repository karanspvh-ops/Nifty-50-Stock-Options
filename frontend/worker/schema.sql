-- Cloudflare D1 (spvh-nifty): staging area for NIFTY option snapshots the
-- cloud collector takes while the office backend is offline. The backend
-- pulls these rows into its own nifty_options.db and, once saved, they are
-- deleted here (see /sync/* in worker/index.js and backend/market_data/cloud_sync.py).
CREATE TABLE IF NOT EXISTS nifty_snapshots (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  date           TEXT NOT NULL,
  snapshot_time  TEXT NOT NULL,        -- 'YYYY-MM-DD HH:MM:SS' IST, minute the snapshot was taken
  expiry         TEXT NOT NULL,
  strike         REAL NOT NULL,
  option_type    TEXT NOT NULL,
  moneyness_rank INTEGER NOT NULL,
  nifty_spot     REAL,
  close          REAL,
  oi             INTEGER,
  oi_day_high    INTEGER,
  oi_day_low     INTEGER,
  buy_quantity   INTEGER,
  sell_quantity  INTEGER,
  day_volume     INTEGER,
  bid_price      REAL,
  ask_price      REAL,
  spread_pct     REAL,
  bid_depth      TEXT,
  ask_depth      TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_snap ON nifty_snapshots (snapshot_time, strike, option_type);

-- Small settings the collector needs: today's Zerodha token, the NIFTY option list.
-- Readable only by the Worker's own code; no endpoint returns these values.
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL, at INTEGER NOT NULL);
