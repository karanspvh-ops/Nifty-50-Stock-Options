import { useEffect, useState } from 'react';
import { useMarketStore } from '../../store/marketStore';

const API = 'http://localhost:8000';

export default function ReportsView() {
  const { settings } = useMarketStore();
  const env = settings.is_live ? 'live' : 'paper';
  const [pnl, setPnl] = useState<any>(null);
  const [ml,  setMl]  = useState<any>(null);
  const [trd, setTrd] = useState<any>(null);
  const [tab, setTab] = useState<'pnl' | 'ml' | 'tradable'>('pnl');
  const [loading, setLoading] = useState(false);
  const [dates, setDates] = useState<string[]>([]);   // available report days (newest first)
  const [date,  setDate]  = useState<string>('');      // selected day ('' = today/latest)

  // Refresh the list of available report days whenever the env changes.
  const loadDates = async (selectLatest = true) => {
    try {
      const d = await fetch(`${API}/api/reports/pnl/${env}/list`).then(r => r.json());
      const ds: string[] = d.dates || [];
      setDates(ds);
      if (selectLatest) setDate(ds[0] || '');
      return ds;
    } catch { return []; }
  };

  const load = async (d = date) => {
    setLoading(true);
    const pnlUrl = d ? `${API}/api/reports/pnl/${env}?date=${d}` : `${API}/api/reports/pnl/${env}`;
    const [r1, r2, r3] = await Promise.all([
      fetch(pnlUrl).then(r => r.json()),
      fetch(`${API}/api/reports/ml/${env}`).then(r => r.json()),
      fetch(`${API}/api/reports/tradable/${env}`).then(r => r.json()),
    ]);
    setPnl(r1); setMl(r2); setTrd(r3); setLoading(false);
  };

  const generate = async () => {
    setLoading(true);
    await fetch(`${API}/api/reports/pnl/${env}/generate`,      { method: 'POST' });
    await fetch(`${API}/api/reports/ml/${env}/trigger`,        { method: 'POST' });
    await fetch(`${API}/api/reports/tradable/${env}/generate`, { method: 'POST' });
    await loadDates();   // today's report may be new → refresh the day list
    await load();
  };

  // env change → refresh day list (selecting the latest day)
  useEffect(() => { loadDates(); }, [env]);
  // selected day change → load that day's report
  useEffect(() => { load(date); }, [env, date]);

  return (
    <div className="p-6 space-y-4 overflow-y-auto h-full">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h1 className="text-white font-bold text-lg">Reports</h1>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-muted uppercase tracking-wider">Day</span>
          <select value={date} onChange={e => setDate(e.target.value)}
            className="bg-surface border border-border text-white text-xs rounded-lg px-2 py-2 min-w-[130px]">
            {dates.length === 0 && <option value="">Today</option>}
            {dates.map((d, i) => (
              <option key={d} value={d}>{d}{i === 0 ? ' (latest)' : ''}</option>
            ))}
          </select>
          <button onClick={generate} disabled={loading}
            className="px-4 py-2 bg-accent text-white rounded-lg text-sm hover:bg-blue-600 disabled:opacity-50">
            {loading ? 'Generating…' : 'Generate Today'}
          </button>
        </div>
      </div>
      {tab === 'pnl' && (
        <p className="text-[10px] text-muted -mt-2">
          P&amp;L report shown for <b className="text-white">{date || 'today'}</b>
          {dates.length ? ` · ${dates.length} day(s) available` : ''}.
          ML &amp; Tradable tabs show the latest available.
        </p>
      )}

      {/* Tabs */}
      <div className="flex gap-2">
        {([['pnl','P&L Report'], ['tradable','Tradable Windows'], ['ml','ML Retrospective']] as const).map(([t, label]) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors
              ${tab === t ? 'bg-accent text-white' : 'bg-surface border border-border text-muted hover:text-white'}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'pnl' && pnl && !pnl.status && (
        <div className="space-y-4">
          {/* Summary */}
          <div className="bg-surface rounded-xl border border-border p-4 grid grid-cols-4 gap-4">
            {[
              { label: 'Total Trades', val: pnl.summary?.total_trades },
              { label: 'Win Rate', val: `${pnl.summary?.win_rate_pct}%` },
              { label: 'Net PnL',   val: `₹${pnl.summary?.total_pnl?.toFixed(2)}`, color: (pnl.summary?.total_pnl || 0) >= 0 ? 'text-up' : 'text-down' },
              { label: 'Profit Factor', val: pnl.summary?.profit_factor },
            ].map(s => (
              <div key={s.label} className="text-center">
                <div className="text-muted text-xs uppercase mb-1">{s.label}</div>
                <div className={`text-white font-bold text-lg ${s.color || ''}`}>{s.val}</div>
              </div>
            ))}
          </div>
          {/* Trade list */}
          <div className="bg-surface rounded-xl border border-border p-4 space-y-3">
            <h3 className="text-white font-semibold text-sm">Trade Log</h3>
            {(pnl.trades || []).map((t: any) => (
              <div key={t.id} className="border border-border rounded-lg p-3 text-xs space-y-1">
                <div className="flex items-center justify-between">
                  <span className="text-white font-semibold">{t.symbol} {t.direction?.toUpperCase()}</span>
                  <span className={`font-semibold ${(t.pnl || 0) >= 0 ? 'text-up' : 'text-down'}`}>
                    {(t.pnl || 0) >= 0 ? '+' : ''}₹{(t.pnl || 0).toFixed(2)}
                  </span>
                </div>
                <p className="text-muted leading-relaxed">{t.trade_statement}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === 'ml' && ml && !ml.status && (
        <div className="space-y-4">
          {/* Summary */}
          <div className="bg-surface rounded-xl border border-border p-4">
            <h3 className="text-white font-semibold text-sm mb-2">Analysis Summary</h3>
            <p className="text-muted text-xs leading-relaxed">{ml.summary}</p>
          </div>
          {/* Patterns */}
          <div className="bg-surface rounded-xl border border-border p-4">
            <h3 className="text-white font-semibold text-sm mb-3">Loss Patterns</h3>
            <div className="space-y-2">
              {Object.entries(ml.patterns || {}).map(([k, v]: [string, any]) => (
                <div key={k} className="flex items-center gap-3">
                  <div className="w-32 text-[10px] text-muted truncate uppercase">{k.replace(/_/g,' ')}</div>
                  <div className="flex-1 bg-border rounded-full h-2">
                    <div className="bg-down h-2 rounded-full" style={{ width: `${v.pct_of_total}%` }} />
                  </div>
                  <div className="text-xs text-down w-12 text-right">{v.pct_of_total}%</div>
                  <div className="text-xs text-muted w-20 text-right">avg ₹{v.avg_loss}</div>
                </div>
              ))}
            </div>
          </div>
          {/* Recommendations */}
          <div className="bg-surface rounded-xl border border-border p-4 space-y-3">
            <h3 className="text-white font-semibold text-sm">Recommendations</h3>
            {(ml.recommendations || []).map((r: any, i: number) => (
              <div key={i} className="border border-accent/30 rounded-lg p-3 text-xs space-y-1 bg-accent/5">
                <div className="text-accent font-semibold">{r.pattern?.replace(/_/g,' ')}</div>
                <p className="text-muted">{r.finding}</p>
                <p className="text-white">{r.action}</p>
                {r.current !== undefined && (
                  <div className="flex gap-4 mt-1">
                    <span className="text-muted">Current: <span className="text-white">{String(r.current)}</span></span>
                    <span className="text-muted">Suggested: <span className="text-up">{String(r.suggested)}</span></span>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Tradable Windows report ── */}
      {tab === 'tradable' && trd && !trd.status && (
        <div className="space-y-4">
          <div className="bg-surface rounded-xl border border-border p-4 grid grid-cols-4 gap-4">
            {[
              { label: 'Total Windows',  val: trd.summary?.total_windows },
              { label: 'Traded',         val: trd.summary?.windows_traded,  color: 'text-up' },
              { label: 'Missed',         val: trd.summary?.windows_missed,  color: 'text-muted' },
              { label: 'Avg Window',     val: `${trd.summary?.avg_window_seconds ?? 0}s` },
            ].map(s => (
              <div key={s.label} className="text-center">
                <div className="text-muted text-xs uppercase mb-1">{s.label}</div>
                <div className={`text-white font-bold text-lg ${s.color || ''}`}>{s.val}</div>
              </div>
            ))}
          </div>
          <div className="bg-surface rounded-xl border border-border p-4">
            <h3 className="text-white font-semibold text-sm mb-3">Tradable Windows Log</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead>
                  <tr className="text-muted text-[10px] uppercase tracking-wider border-b border-border">
                    <th className="px-2 pb-2">Symbol</th>
                    <th className="px-2 pb-2">Dir</th>
                    <th className="px-2 pb-2">Sector</th>
                    <th className="px-2 pb-2">Identified</th>
                    <th className="px-2 pb-2">Until</th>
                    <th className="px-2 pb-2">Duration</th>
                    <th className="px-2 pb-2">Score</th>
                    <th className="px-2 pb-2">Traded?</th>
                  </tr>
                </thead>
                <tbody>
                  {(trd.signals || []).map((s: any) => (
                    <tr key={s.id} className="border-b border-border/40 hover:bg-border/20">
                      <td className="px-2 py-1.5 text-white text-xs font-medium">{s.symbol}</td>
                      <td className="px-2 py-1.5">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded font-semibold
                          ${s.direction === 'call' ? 'bg-up/20 text-up' : 'bg-down/20 text-down'}`}>
                          {s.direction?.toUpperCase()}
                        </span>
                      </td>
                      <td className="px-2 py-1.5 text-muted text-[11px]">{s.sector}</td>
                      <td className="px-2 py-1.5 text-muted text-[11px]">
                        {new Date(s.opened_at).toLocaleTimeString('en-IN', { hour12: false })}
                      </td>
                      <td className="px-2 py-1.5 text-muted text-[11px]">
                        {s.closed_at ? new Date(s.closed_at).toLocaleTimeString('en-IN', { hour12: false })
                          : <span className="text-up">active</span>}
                      </td>
                      <td className="px-2 py-1.5 text-white text-xs">
                        {s.duration_sec != null ? `${s.duration_sec}s` : '—'}
                      </td>
                      <td className="px-2 py-1.5 text-accent text-xs">{s.entry_score}/{s.max_score}</td>
                      <td className="px-2 py-1.5 text-xs">
                        {s.was_traded ? <span className="text-up">✓</span> : <span className="text-muted">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {((tab === 'pnl' && (!pnl || pnl.status)) ||
        (tab === 'tradable' && (!trd || trd.status))) && (
        <div className="bg-surface rounded-xl border border-border p-8 text-center">
          <p className="text-muted text-sm">
            No {tab === 'pnl' ? 'P&L' : 'Tradable'} report for {date || 'today'}.
            {dates.length ? ' Pick another day above, or ' : ' '}click Generate Today.
          </p>
        </div>
      )}
    </div>
  );
}
