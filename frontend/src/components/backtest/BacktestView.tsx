import { useEffect, useState, useRef } from 'react';

const API = 'http://localhost:8000';

function isoDaysAgo(n: number) {
  const d = new Date(); d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

export default function BacktestView() {
  const [from, setFrom] = useState(isoDaysAgo(14));
  const [to, setTo]     = useState(isoDaysAgo(0));
  const [status, setStatus] = useState<any>(null);
  const [result, setResult] = useState<any>(null);
  const poll = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  const loadStatusResult = async () => {
    const s = await (await fetch(`${API}/api/backtest/status`)).json();
    setStatus(s);
    if (!s.running && s.phase === 'done') {
      const r = await (await fetch(`${API}/api/backtest/result`)).json();
      setResult(r);
      clearInterval(poll.current);
    }
  };

  useEffect(() => { loadStatusResult(); return () => clearInterval(poll.current); }, []);

  const run = async () => {
    setResult(null);
    await fetch(`${API}/api/backtest/run`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from_date: from, to_date: to }),
    });
    clearInterval(poll.current);
    poll.current = setInterval(loadStatusResult, 2000);
  };

  const sum = result?.summary;
  const running = status?.running;
  const pct = status?.total_days ? Math.round((status.done_days / status.total_days) * 100) : 0;

  return (
    <div className="p-6 space-y-4 overflow-y-auto h-full">
      <h1 className="text-white font-bold text-lg">Strategy Backtest
        <span className="ml-2 text-xs text-muted font-normal">Opening Breakout · real stock + option prices</span>
      </h1>

      {/* Controls */}
      <div className="bg-surface rounded-xl border border-border p-4 flex items-end gap-3 flex-wrap">
        <div className="flex flex-col gap-1">
          <span className="text-muted text-[10px] uppercase">From</span>
          <input type="date" value={from} onChange={e => setFrom(e.target.value)}
            className="bg-bg border border-border text-white text-xs rounded px-2 py-1.5" />
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-muted text-[10px] uppercase">To</span>
          <input type="date" value={to} onChange={e => setTo(e.target.value)}
            className="bg-bg border border-border text-white text-xs rounded px-2 py-1.5" />
        </div>
        <button onClick={run} disabled={running}
          className="px-4 py-2 bg-accent text-white rounded-lg text-sm font-semibold hover:bg-blue-600 disabled:opacity-50">
          {running ? 'Running…' : 'Run Backtest'}
        </button>
        {running && (
          <div className="flex items-center gap-2 text-xs text-muted">
            <span>{status.phase}</span>
            <div className="w-40 h-1.5 bg-border rounded-full"><div className="h-1.5 bg-accent rounded-full" style={{ width: `${pct}%` }} /></div>
            <span>{status.done_days}/{status.total_days} days</span>
          </div>
        )}
        {status?.phase === 'error' && <span className="text-down text-xs">⚠ {status.note}</span>}
      </div>
      <p className="text-[10px] text-muted">Pulls real Zerodha historical data for ~175 F&amp;O stocks; a 2-week run takes ~1–2 min. Uses your current params (trail gap, SL, target).</p>

      {/* Summary */}
      {sum && (
        <>
          <div className="bg-surface rounded-xl border border-border p-4 grid grid-cols-4 md:grid-cols-7 gap-4">
            {[
              { l: 'Days scanned', v: sum.days_scanned },
              { l: 'Days traded',  v: sum.days_traded },
              { l: 'Total trades', v: sum.total_trades },
              { l: 'Win rate',     v: `${sum.win_rate}%` },
              { l: 'Avg / trade',  v: `${sum.avg_pnl_pct >= 0 ? '+' : ''}${sum.avg_pnl_pct}%`, c: sum.avg_pnl_pct >= 0 ? 'text-up' : 'text-down' },
              { l: 'Best',         v: `+${sum.best}%`, c: 'text-up' },
              { l: 'Worst',        v: `${sum.worst}%`, c: 'text-down' },
            ].map(s => (
              <div key={s.l} className="text-center">
                <div className="text-muted text-[10px] uppercase mb-1">{s.l}</div>
                <div className={`text-white font-bold text-lg ${s.c || ''}`}>{s.v}</div>
              </div>
            ))}
          </div>
          <p className="text-[10px] text-muted">Params used: trail gap {sum.params.trail_gap_pct}% · SL {sum.params.hard_sl_pct}% · target {sum.params.target_pct}%</p>

          {/* Trade log */}
          <div className="bg-surface rounded-xl border border-border p-4">
            <h3 className="text-white font-semibold text-sm mb-3">Trade Log ({result.trades.length})</h3>
            {!result.trades.length ? (
              <p className="text-muted text-xs">No trades triggered in this window (no qualifying breakout passed confirmation).</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left">
                  <thead>
                    <tr className="text-muted text-[10px] uppercase tracking-wider border-b border-border">
                      <th className="px-2 pb-2">Date</th><th className="px-2 pb-2">Sector</th>
                      <th className="px-2 pb-2">Stock</th><th className="px-2 pb-2">Contract</th>
                      <th className="px-2 pb-2">Entry</th><th className="px-2 pb-2">Exit</th>
                      <th className="px-2 pb-2">Peak</th><th className="px-2 pb-2">PnL</th>
                      <th className="px-2 pb-2">Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.map((t: any, i: number) => (
                      <tr key={i} className="border-b border-border/40">
                        <td className="px-2 py-1.5 text-muted text-[11px]">{t.date}</td>
                        <td className="px-2 py-1.5 text-muted text-[11px]">{t.sector}</td>
                        <td className="px-2 py-1.5 text-white text-xs font-semibold">{t.symbol}</td>
                        <td className="px-2 py-1.5 text-[11px] font-mono text-muted">
                          {t.strike} {t.direction === 'call' ? 'CE' : 'PE'}
                        </td>
                        <td className="px-2 py-1.5 text-xs text-muted">{t.entry_time} ₹{t.entry_premium}</td>
                        <td className="px-2 py-1.5 text-xs text-muted">{t.exit_time} ₹{t.exit_premium}</td>
                        <td className="px-2 py-1.5 text-xs text-muted">+{t.peak_pct}%</td>
                        <td className={`px-2 py-1.5 text-xs font-semibold ${t.pnl_pct >= 0 ? 'text-up' : 'text-down'}`}>
                          {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct}%
                        </td>
                        <td className="px-2 py-1.5 text-[11px] text-muted">{t.exit_reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
