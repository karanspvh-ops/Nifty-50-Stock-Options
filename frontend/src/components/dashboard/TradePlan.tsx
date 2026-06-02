import { useEffect, useState } from 'react';

const API = 'http://localhost:8000';

interface PlanStock {
  symbol: string; token: string; opening_move: number; day_move: number;
  r_factor: number; ltp: number; est_premium: number; eligible: boolean; entered: boolean;
}
interface Plan {
  status: string; phase: string; trend: string;
  sector: string | null; direction: string | null; sector_pct: number;
  stocks: PlanStock[]; note: string; generated_at: string; enabled: boolean;
}

const PHASE_LABEL: Record<string, string> = {
  IDLE: 'Pre-market', SCANNING: 'Scanning (9:15–9:35)', PREVIEW: 'Preview (9:35–9:40)',
  PLANNED: 'Plan finalised', ENTERING: 'Entering (≤9:45)', MANAGING: 'Managing positions',
  DONE: 'Done for today', KILLED: 'Halted',
};

export default function TradePlan() {
  const [plan, setPlan] = useState<Plan | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const r = await fetch(`${API}/api/strategy/plan`);
        setPlan(await r.json());
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

  const bullish = plan?.direction === 'call';
  const showPreview = plan && plan.sector &&
    ['preview', 'final', 'entering'].includes(plan.status);

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
            <span className="text-[10px] text-muted ml-auto">
              {plan.status === 'preview' && '⏳ Review window — entries at 9:40'}
              {plan.status === 'entering' && '🟢 Entering now'}
              {plan.status === 'final' && '✅ Finalised'}
            </span>
          </div>

          {/* Planned stocks */}
          <table className="w-full text-left">
            <thead>
              <tr className="text-muted text-[10px] uppercase tracking-wider border-b border-border">
                <th className="px-2 pb-2">Stock</th>
                <th className="px-2 pb-2">Open Move</th>
                <th className="px-2 pb-2">Day %</th>
                <th className="px-2 pb-2">R-Factor</th>
                <th className="px-2 pb-2">LTP</th>
                <th className="px-2 pb-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {plan.stocks.map(s => (
                <tr key={s.token} className="border-b border-border/40">
                  <td className="px-2 py-1.5 text-white text-xs font-semibold">{s.symbol}</td>
                  <td className={`px-2 py-1.5 text-xs font-semibold ${s.opening_move >= 0 ? 'text-up' : 'text-down'}`}>
                    {s.opening_move >= 0 ? '+' : ''}{s.opening_move.toFixed(2)}%
                  </td>
                  <td className="px-2 py-1.5 text-muted text-xs">
                    {s.day_move >= 0 ? '+' : ''}{s.day_move.toFixed(2)}%
                  </td>
                  <td className="px-2 py-1.5 text-accent text-xs">{s.r_factor.toFixed(2)}</td>
                  <td className="px-2 py-1.5 text-muted text-xs">₹{s.ltp?.toFixed(1)}</td>
                  <td className="px-2 py-1.5 text-xs">
                    {s.entered
                      ? <span className="text-up">● in trade</span>
                      : s.eligible
                        ? <span className="text-up">▲ triggered (≥1.5%)</span>
                        : <span className="text-muted">waiting for 1.5%</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-[10px] text-muted mt-2">{plan.note} · SL 10% · target 50% · positional</p>
        </>
      )}
    </div>
  );
}
