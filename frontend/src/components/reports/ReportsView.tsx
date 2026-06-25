import { useEffect, useState, useMemo } from 'react';
import { useMarketStore } from '../../store/marketStore';

const API = 'http://localhost:8000';

type SummaryRange = 'today' | '7d' | 'all';

function getStrategy(logic: string) {
  if (logic?.startsWith('[ES]')) return 'ES';
  if (logic?.startsWith('[OB]')) return 'OB';
  return 'TE';
}
function fmtPnL(v: number) {
  return `${v >= 0 ? '+' : '−'}₹${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
}
function fmtDate(s: string) {
  return new Date(s).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', weekday: 'short' });
}

function PnLSummaryPanel({ env }: { env: string }) {
  const [allTrades, setAllTrades] = useState<any[]>([]);
  const [range, setRange] = useState<SummaryRange>('today');

  useEffect(() => {
    const load = () => fetch(`${API}/api/trades?env=${env}`).then(r => r.json()).then(setAllTrades).catch(() => {});
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, [env]);

  const trades = useMemo(() => {
    if (range === 'all') return allTrades;
    const cutoff = new Date();
    if (range === 'today') { cutoff.setHours(0, 0, 0, 0); }
    else { cutoff.setDate(cutoff.getDate() - 7); cutoff.setHours(0, 0, 0, 0); }
    return allTrades.filter(t => t.entered_at && new Date(t.entered_at) >= cutoff);
  }, [allTrades, range]);

  const byDay = useMemo(() => {
    const map: Record<string, { es: number; ob: number; net: number; esN: number; obN: number; wins: number; n: number }> = {};
    trades.forEach(t => {
      const d = t.entered_at?.slice(0, 10) ?? '';
      if (!map[d]) map[d] = { es: 0, ob: 0, net: 0, esN: 0, obN: 0, wins: 0, n: 0 };
      const pnl = t.pnl || 0;
      const s = getStrategy(t.entry_logic);
      if (s === 'ES') { map[d].es += pnl; map[d].esN++; }
      else if (s === 'OB') { map[d].ob += pnl; map[d].obN++; }
      map[d].net += pnl; map[d].n++;
      if (pnl > 0) map[d].wins++;
    });
    return Object.entries(map).sort((a, b) => b[0].localeCompare(a[0]));
  }, [trades]);

  const esPnL = trades.filter(t => getStrategy(t.entry_logic) === 'ES').reduce((s, t) => s + (t.pnl || 0), 0);
  const obPnL = trades.filter(t => getStrategy(t.entry_logic) === 'OB').reduce((s, t) => s + (t.pnl || 0), 0);
  const net   = trades.reduce((s, t) => s + (t.pnl || 0), 0);
  const wins  = trades.filter(t => (t.pnl || 0) > 0).length;
  const esN   = trades.filter(t => getStrategy(t.entry_logic) === 'ES').length;
  const obN   = trades.filter(t => getStrategy(t.entry_logic) === 'OB').length;

  const RANGES: { key: SummaryRange; label: string }[] = [
    { key: 'today', label: 'Today' },
    { key: '7d',    label: 'Last 7 Days' },
    { key: 'all',   label: 'All Time' },
  ];

  return (
    <div className="bg-surface rounded-xl border border-border p-4 space-y-4">
      {/* Range tabs */}
      <div className="flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-wider text-muted mr-1">Range</span>
        {RANGES.map(r => (
          <button key={r.key} onClick={() => setRange(r.key)}
            className={`text-[11px] px-3 py-1 rounded font-semibold transition-colors border ${
              range === r.key
                ? 'bg-accent/20 text-accent border-accent/40'
                : 'bg-border/20 text-muted border-border hover:bg-border/40'
            }`}>
            {r.label}
          </button>
        ))}
      </div>

      {/* Stat cards */}
      <div className="flex flex-wrap gap-3">
        <div className="flex flex-col px-4 py-2 rounded-lg border border-purple-500/30 bg-purple-500/5">
          <div className="text-[10px] uppercase tracking-wider mb-0.5 opacity-70">Early Scalp [ES]</div>
          <div className={`font-bold text-base ${esPnL >= 0 ? 'text-up' : 'text-down'}`}>{fmtPnL(esPnL)}</div>
          <div className="text-[10px] opacity-50">{esN} trade{esN !== 1 ? 's' : ''}</div>
        </div>
        <div className="flex flex-col px-4 py-2 rounded-lg border border-blue-500/30 bg-blue-500/5">
          <div className="text-[10px] uppercase tracking-wider mb-0.5 opacity-70">Opening Breakout [OB]</div>
          <div className={`font-bold text-base ${obPnL >= 0 ? 'text-up' : 'text-down'}`}>{fmtPnL(obPnL)}</div>
          <div className="text-[10px] opacity-50">{obN} trade{obN !== 1 ? 's' : ''}</div>
        </div>
        <div className="flex flex-col px-4 py-2 rounded-lg border border-border bg-white/5 ml-auto">
          <div className="text-[10px] uppercase tracking-wider mb-0.5 text-muted">Net PnL</div>
          <div className={`font-bold text-xl ${net >= 0 ? 'text-up' : 'text-down'}`}>{fmtPnL(net)}</div>
          <div className="text-[10px] text-muted">
            {trades.length} trades · {trades.length ? Math.round(wins / trades.length * 100) : 0}% win
          </div>
        </div>
      </div>

      {/* Per-day breakdown */}
      {range !== 'today' && byDay.length > 0 && (
        <div className="rounded-lg border border-border overflow-hidden">
          <table className="w-full text-left">
            <thead>
              <tr className="text-muted text-[10px] uppercase tracking-wider bg-border/20 border-b border-border">
                <th className="px-3 py-2">Date</th>
                <th className="px-3 py-2 text-purple-400">ES</th>
                <th className="px-3 py-2 text-blue-400">OB</th>
                <th className="px-3 py-2">Net</th>
                <th className="px-3 py-2">Trades</th>
                <th className="px-3 py-2">Win%</th>
              </tr>
            </thead>
            <tbody>
              {byDay.map(([date, d]) => (
                <tr key={date} className="border-b border-border/40 hover:bg-border/10 text-xs">
                  <td className="px-3 py-2 text-white font-medium">{fmtDate(date)}</td>
                  <td className={`px-3 py-2 font-mono font-semibold ${d.es >= 0 ? 'text-up' : 'text-down'}`}>
                    {d.esN > 0 ? fmtPnL(d.es) : <span className="text-muted">—</span>}
                    {d.esN > 0 && <span className="text-muted ml-1 font-normal text-[10px]">{d.esN}t</span>}
                  </td>
                  <td className={`px-3 py-2 font-mono font-semibold ${d.ob >= 0 ? 'text-up' : 'text-down'}`}>
                    {d.obN > 0 ? fmtPnL(d.ob) : <span className="text-muted">—</span>}
                    {d.obN > 0 && <span className="text-muted ml-1 font-normal text-[10px]">{d.obN}t</span>}
                  </td>
                  <td className={`px-3 py-2 font-mono font-bold ${d.net >= 0 ? 'text-up' : 'text-down'}`}>{fmtPnL(d.net)}</td>
                  <td className="px-3 py-2 text-muted">{d.n}</td>
                  <td className="px-3 py-2 text-muted">{d.n ? Math.round(d.wins / d.n * 100) : 0}%</td>
                </tr>
              ))}
              <tr className="bg-border/20 text-xs font-bold border-t border-border">
                <td className="px-3 py-2 text-muted text-[10px] uppercase tracking-wider">Total</td>
                <td className={`px-3 py-2 font-mono ${esPnL >= 0 ? 'text-up' : 'text-down'}`}>{fmtPnL(esPnL)}</td>
                <td className={`px-3 py-2 font-mono ${obPnL >= 0 ? 'text-up' : 'text-down'}`}>{fmtPnL(obPnL)}</td>
                <td className={`px-3 py-2 font-mono ${net >= 0 ? 'text-up' : 'text-down'}`}>{fmtPnL(net)}</td>
                <td className="px-3 py-2 text-muted">{trades.length}</td>
                <td className="px-3 py-2 text-muted">{trades.length ? Math.round(wins / trades.length * 100) : 0}%</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

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

      {tab === 'pnl' && <PnLSummaryPanel env={env} />}

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
            {(pnl.trades || []).map((t: any) => {
              const fmtT = (s?: string | null) => {
                if (!s) return '—';
                const m = String(s).match(/(\d{2}:\d{2}:\d{2})/);
                return m ? m[1] : '—';
              };
              const peakCls = (t.peak_pct ?? 0) >= 0 ? 'text-up' : 'text-down';
              return (
                <div key={t.id} className="border border-border rounded-lg p-3 text-xs space-y-2">
                  {/* header line */}
                  <div className="flex items-center justify-between flex-wrap gap-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-white font-semibold">{t.symbol}</span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-semibold
                        ${t.direction === 'call' ? 'bg-up/20 text-up' : 'bg-down/20 text-down'}`}>
                        {t.direction?.toUpperCase()}
                      </span>
                      {t.strike != null && (
                        <span className="text-[11px] font-mono text-white bg-bg/60 px-1.5 py-0.5 rounded">
                          {t.strike} {t.option_type || ''}
                          {t.expiry && <span className="text-muted ml-1">{String(t.expiry).slice(5)}</span>}
                        </span>
                      )}
                      {t.qty != null && t.lot_size != null && (
                        <span className="text-[10px] text-muted font-mono">{t.qty}×{t.lot_size}={t.qty * t.lot_size}</span>
                      )}
                    </div>
                    <span className={`font-semibold ${(t.pnl || 0) >= 0 ? 'text-up' : 'text-down'}`}>
                      {(t.pnl || 0) >= 0 ? '+' : ''}₹{(t.pnl || 0).toFixed(2)}
                      <span className="text-muted ml-1 text-[10px]">({(t.pnl_pct || 0).toFixed(1)}%)</span>
                    </span>
                  </div>
                  {/* metrics line */}
                  <div className="grid grid-cols-2 md:grid-cols-5 gap-x-3 gap-y-1 text-[11px]">
                    <div><span className="text-muted">Entry @ </span><span className="text-white">₹{t.entry?.toFixed(2)}</span></div>
                    <div><span className="text-muted">Exit @ </span><span className="text-white">{t.exit != null ? `₹${t.exit.toFixed(2)}` : '—'}</span></div>
                    <div><span className="text-muted">Peak </span><span className={peakCls}>{t.peak != null ? `₹${t.peak.toFixed(2)}` : '—'}</span>{t.peak_pct != null && <span className={`ml-1 ${peakCls}`}>({t.peak_pct >= 0 ? '+' : ''}{t.peak_pct.toFixed(1)}%)</span>}</div>
                    <div><span className="text-muted">Entered </span><span className="text-white font-mono">{fmtT(t.entered_at)}</span></div>
                    <div><span className="text-muted">Exited </span><span className="text-white font-mono">{fmtT(t.exited_at)}</span></div>
                  </div>
                  <p className="text-muted leading-relaxed">{t.trade_statement}</p>
                </div>
              );
            })}
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
