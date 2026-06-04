import { useEffect, useState } from 'react';

const API = 'http://localhost:8000';

interface PlanStock {
  symbol: string; token: string; opening_move: number; day_move: number;
  r_factor: number; ltp: number; est_premium: number; eligible: boolean; entered: boolean;
  confirmed: boolean; consec: number; vol_ratio: number;
  vp_poc: number | null; vp_vah: number | null; vp_val: number | null;
}
interface Plan {
  status: string; phase: string; trend: string;
  sector: string | null; direction: string | null; sector_pct: number;
  stocks: PlanStock[]; note: string; generated_at: string; enabled: boolean;
  entry_window_open: boolean; trail_gap_pct: number;
}
interface Tune {
  status: string; trades?: number; required?: number; current_gap?: number;
  recommended_gap?: number; applied?: boolean; rationale?: string;
  win_rate?: number; avg_giveback_pct?: number; message?: string;
}

const PHASE_LABEL: Record<string, string> = {
  IDLE: 'Pre-market', SCANNING: 'Scanning (9:15–9:35)', PREVIEW: 'Preview (9:35–9:40)',
  PLANNED: 'Plan finalised', ENTERING: 'Entering (≤9:45)',
  ENTERING_EXT: 'Entering — EXTENDED (≤10:00)', MANAGING: 'Managing positions',
  DONE: 'Done for today', KILLED: 'Halted',
};

export default function TradePlan() {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [tune, setTune] = useState<Tune | null>(null);
  const [gap, setGap]   = useState<number>(20);

  useEffect(() => {
    const load = async () => {
      try {
        const [p, t] = await Promise.all([
          fetch(`${API}/api/strategy/plan`).then(r => r.json()),
          fetch(`${API}/api/strategy/tune`).then(r => r.json()),
        ]);
        setPlan(p); setTune(t);
        if (typeof p.trail_gap_pct === 'number') setGap(p.trail_gap_pct);
      } catch { /* offline */ }
    };
    load();
    const id = setInterval(load, 3000);
    return () => clearInterval(id);
  }, []);

  const toggle = async () => {
    if (!plan) return;
    await fetch(`${API}/api/strategy/enable`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: !plan.enabled }),
    });
  };

  const saveGap = async (v: number) => {
    setGap(v);
    await fetch(`${API}/api/strategy/params`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trail_gap_pct: v }),
    });
  };

  const bullish = plan?.direction === 'call';
  const showPreview = plan && plan.sector &&
    ['preview', 'final', 'entering'].includes(plan.status);
  const windowOpen = plan?.entry_window_open;

  return (
    <div className={`rounded-xl border p-4 ${
      showPreview
        ? (bullish ? 'border-up/50 bg-up/5' : 'border-down/50 bg-down/5')
        : 'border-border bg-surface'}`}>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-white font-semibold text-sm flex items-center gap-2">
          Opening Breakout — Trade Plan
          {plan && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-bg text-muted">
              {PHASE_LABEL[plan.phase] || plan.phase}
            </span>
          )}
        </h2>
        <button onClick={toggle}
          className={`text-[10px] px-2 py-1 rounded font-semibold
            ${plan?.enabled ? 'bg-up/20 text-up' : 'bg-border text-muted'}`}>
          {plan?.enabled ? 'STRATEGY ON' : 'STRATEGY OFF'}
        </button>
      </div>

      {!plan || !plan.sector ? (
        <p className="text-muted text-xs py-2">
          {plan?.note || 'Waiting for the opening session to scan sectors…'}
        </p>
      ) : (
        <>
          {/* Decision banner */}
          <div className="flex items-center gap-3 mb-3 flex-wrap">
            <span className={`text-xs px-2 py-1 rounded font-bold
              ${bullish ? 'bg-up/20 text-up' : 'bg-down/20 text-down'}`}>
              {plan.trend.toUpperCase()} → {bullish ? 'BUY CALLS' : 'BUY PUTS'}
            </span>
            <span className="text-xs text-white">
              Sector: <span className="font-semibold">{plan.sector}</span>
              <span className={`ml-1 ${plan.sector_pct >= 0 ? 'text-up' : 'text-down'}`}>
                {plan.sector_pct >= 0 ? '+' : ''}{plan.sector_pct.toFixed(2)}%
              </span>
            </span>
            <span className="text-[10px] ml-auto">
              {windowOpen
                ? <span className="text-up">🟢 Entry window OPEN (primary ≤9:45, extends to 10:00 if unfilled)</span>
                : <span className="text-muted">⏸ Entry window closed — opens 9:15 tomorrow. Below is the current breakout picture for reference.</span>}
            </span>
          </div>

          {/* Planned stocks */}
          <table className="w-full text-left">
            <thead>
              <tr className="text-muted text-[10px] uppercase tracking-wider border-b border-border">
                <th className="px-2 pb-2">Stock</th>
                <th className="px-2 pb-2">Open Move</th>
                <th className="px-2 pb-2">R-Factor</th>
                <th className="px-2 pb-2">Momentum</th>
                <th className="px-2 pb-2">Volume</th>
                <th className="px-2 pb-2">VP levels (POC / VAH / VAL)</th>
                <th className="px-2 pb-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {plan.stocks.map(s => (
                <tr key={s.token} className="border-b border-border/40">
                  <td className="px-2 py-1.5 text-white text-xs font-semibold">{s.symbol}
                    <span className="text-muted ml-1">₹{s.ltp?.toFixed(1)}</span>
                  </td>
                  <td className={`px-2 py-1.5 text-xs font-semibold ${s.opening_move >= 0 ? 'text-up' : 'text-down'}`}>
                    {s.opening_move >= 0 ? '+' : ''}{s.opening_move.toFixed(2)}%
                  </td>
                  <td className="px-2 py-1.5 text-accent text-xs">{s.r_factor.toFixed(2)}</td>
                  <td className="px-2 py-1.5 text-xs">
                    <span className={s.consec >= 2 ? (bullish ? 'text-up' : 'text-down') : 'text-muted'}>
                      {s.consec} {bullish ? '↑↑' : '↓↓'}
                    </span>
                  </td>
                  <td className={`px-2 py-1.5 text-xs ${s.vol_ratio >= 1.3 ? 'text-up' : 'text-muted'}`}>
                    {s.vol_ratio?.toFixed(1)}×
                  </td>
                  <td className="px-2 py-1.5 text-[11px] text-muted font-mono">
                    {s.vp_poc ? `${s.vp_poc} / ${s.vp_vah} / ${s.vp_val}` : '—'}
                  </td>
                  <td className="px-2 py-1.5 text-xs">
                    {s.entered
                      ? <span className={bullish ? 'text-up' : 'text-down'}>● in trade</span>
                      : s.confirmed
                        ? <span className={bullish ? 'text-up' : 'text-down'}>✓ confirmed</span>
                        : s.eligible
                          ? <span className="text-yellow-400">moved, awaiting confirm</span>
                          : <span className="text-muted">waiting for ±1.5%</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-[10px] text-muted mt-2">{plan.note} · SL 10% · target 50% · positional</p>
        </>
      )}

      {/* Trailing gap control + ML auto-tune */}
      <div className="mt-3 pt-3 border-t border-border flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-muted uppercase tracking-wider">Trailing gap</span>
          <input type="range" min={8} max={35} step={1} value={gap}
            onChange={e => saveGap(parseInt(e.target.value))}
            className="w-28 accent-accent" />
          <span className="text-xs text-white font-semibold w-10">{gap}%</span>
        </div>

        {tune && tune.status === 'insufficient_data' && (
          <span className="text-[10px] text-muted">
            🤖 Auto-tune learning: {tune.trades}/{tune.required} trades
          </span>
        )}
        {tune && (tune.status === 'tuned' || tune.status === 'analyzed') && (
          <span className={`text-[10px] ${tune.applied ? 'text-up' : 'text-muted'}`}>
            🤖 {tune.applied ? `Auto-tuned → ${tune.recommended_gap}%` : `Suggests ${tune.recommended_gap}%`}
            {tune.rationale ? ` · ${tune.rationale}` : ''}
          </span>
        )}
      </div>
    </div>
  );
}
