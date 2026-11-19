import { useEffect, useState } from 'react';
import { Broadcast, PauseCircle, CheckCircle, Circle, Clock, WarningOctagon } from '@phosphor-icons/react';

const API = 'http://localhost:8000';

interface PlanStock {
  symbol: string; token: string; opening_move: number; day_move: number;
  r_factor: number; ltp: number; est_premium: number; eligible: boolean; entered: boolean;
  sector: string; direction: string;
  confirmed: boolean; consec: number; vol_ratio: number;
  vp_poc: number | null; vp_vah: number | null; vp_val: number | null;
}
interface Plan {
  status: string; phase: string; trend: string;
  sector: string | null; direction: string | null; sector_pct: number;
  stocks: PlanStock[]; note: string; generated_at: string; enabled: boolean;
  entry_window_open: boolean; trail_gap_pct: number;
}

// The session in order. Each backend phase lights up one step.
const STEPS: { label: string; time: string; phases: string[] }[] = [
  { label: 'Pre-market', time: 'before 9:15', phases: ['IDLE'] },
  { label: 'Scan',       time: '9:15–9:35',   phases: ['SCANNING'] },
  { label: 'Plan',       time: '9:35–9:40',   phases: ['PREVIEW', 'PLANNED'] },
  { label: 'Entry',      time: '9:40–10:00',  phases: ['ENTERING', 'ENTERING_EXT'] },
  { label: 'Manage',     time: 'until 3:15',  phases: ['MANAGING'] },
  { label: 'Done',       time: 'after exit',  phases: ['DONE'] },
];

function Timeline({ phase }: { phase: string }) {
  const at = STEPS.findIndex(s => s.phases.includes(phase));
  return (
    <ol className="grid grid-cols-6 gap-1" aria-label="Session progress">
      {STEPS.map((s, i) => {
        const done = at >= 0 && i < at, now = i === at;
        return (
          <li key={s.label} aria-current={now ? 'step' : undefined} className="min-w-0">
            <div className={`h-1.5 rounded-full ${now ? 'bg-spvh-500' : done ? 'bg-spvh-300' : 'bg-border'}`} />
            <div className={`mt-1.5 text-xs truncate ${now ? 'font-bold text-ink' : done ? 'text-ink2' : 'text-muted'}`}>{s.label}</div>
            <div className="text-[11px] text-muted truncate">{s.time}</div>
          </li>
        );
      })}
    </ol>
  );
}

function Status({ s }: { s: PlanStock }) {
  const b = s.direction === 'call';
  if (s.entered) return (
    <span className={`inline-flex items-center gap-1 text-xs font-bold px-2 py-0.5 rounded-full ${b ? 'bg-up/10 text-up' : 'bg-down/10 text-down'}`}>
      <Circle size={8} weight="fill" /> In trade
    </span>
  );
  if (s.confirmed) return (
    <span className="inline-flex items-center gap-1 text-xs font-bold px-2 py-0.5 rounded-full btn-primary">
      <CheckCircle size={13} weight="fill" /> Confirmed
    </span>
  );
  if (s.eligible) return <span className="text-xs font-semibold text-warn">Moved, awaiting confirm</span>;
  return <span className="text-xs text-muted">Waiting for ±1.5%</span>;
}

const rankOf = (s: PlanStock) => (s.entered ? 0 : s.confirmed ? 1 : s.eligible ? 2 : 3);

export default function TradePlan() {
  const [plan, setPlan] = useState<Plan | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const r = await fetch(`${API}/api/strategy/plan`);
        if (r.ok) setPlan(await r.json());
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
    setPlan(p => (p ? { ...p, enabled: !p.enabled } : p));
  };

  const killed = plan?.phase === 'KILLED';
  const bullish = plan?.direction === 'call';
  const stocks = [...(plan?.stocks ?? [])].sort((a, b) => rankOf(a) - rankOf(b) || Math.abs(b.opening_move) - Math.abs(a.opening_move));
  const counts = {
    trade: stocks.filter(s => s.entered).length,
    confirmed: stocks.filter(s => s.confirmed && !s.entered).length,
    moved: stocks.filter(s => s.eligible && !s.confirmed && !s.entered).length,
  };
  const bias = !plan?.sector ? null
    : plan.trend === 'mixed' ? { t: 'Mixed: best setups on both sides', c: 'bg-warn/10 text-warn' }
    : bullish ? { t: 'Bullish: buy calls', c: 'bg-up/10 text-up' }
    : { t: 'Bearish: buy puts', c: 'bg-down/10 text-down' };

  return (
    <section className="bg-surface rounded-panel border border-border overflow-hidden" aria-label="Opening Breakout trade plan">
      {/* Header: name, on/off switch */}
      <div className="flex items-center gap-3 px-4 pt-4">
        <h2 className="text-ink font-bold text-base">Opening Breakout</h2>
        <span className="text-xs text-muted">trade plan · SL 10% · target 50%</span>
        <button
          role="switch" aria-checked={!!plan?.enabled} onClick={toggle} disabled={!plan}
          className="ml-auto flex items-center gap-2 text-xs font-semibold text-ink2 disabled:opacity-40"
        >
          {plan?.enabled ? 'Strategy on' : 'Strategy off'}
          <span className={`relative w-10 h-6 rounded-full transition-colors duration-200 ${plan?.enabled ? 'bg-up' : 'bg-border'}`}>
            <span className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform duration-200 ease-out
              ${plan?.enabled ? 'translate-x-4' : ''}`} />
          </span>
        </button>
      </div>

      {/* Where the session is */}
      <div className="px-4 pt-3 pb-4">
        {killed
          ? <div className="flex items-center gap-2 text-sm font-semibold text-down"><WarningOctagon size={18} weight="fill" /> Halted by the portfolio stop-loss</div>
          : <Timeline phase={plan?.phase ?? 'IDLE'} />}
      </div>

      {!plan || !plan.sector ? (
        <div className="flex items-center gap-2 px-4 py-4 border-t border-border text-sm text-muted">
          <Clock size={16} />
          {plan?.note && plan.note !== 'Waiting for market data…' ? plan.note : 'Sectors are scanned from 9:15. The plan appears here once a lead sector emerges.'}
        </div>
      ) : (
        <>
          {/* Decision */}
          <div className="flex items-center gap-x-4 gap-y-2 flex-wrap px-4 py-3 border-t border-border bg-bg/50">
            {bias && <span className={`text-sm font-bold px-2.5 py-1 rounded-full ${bias.c}`}>{bias.t}</span>}
            <span className="text-sm text-ink2">
              Lead sector <span className="font-bold text-ink">{plan.sector}</span>
              <span className={`ml-1.5 font-semibold ${plan.sector_pct >= 0 ? 'text-up' : 'text-down'}`}>
                {plan.sector_pct >= 0 ? '+' : ''}{plan.sector_pct.toFixed(2)}%
              </span>
            </span>
            <span className="text-sm text-ink2">
              <span className="font-bold text-ink">{counts.trade}</span> in trade ·{' '}
              <span className="font-bold text-ink">{counts.confirmed}</span> confirmed ·{' '}
              <span className="font-bold text-ink">{counts.moved}</span> moved
            </span>
            <span className="ml-auto text-xs">
              {plan.entry_window_open
                ? <span className="inline-flex items-center gap-1.5 text-up font-semibold"><Broadcast size={15} weight="bold" /> Entry window open until 9:45 (10:30 if nothing fires)</span>
                : <span className="inline-flex items-center gap-1.5 text-muted"><PauseCircle size={15} /> Entry window closed</span>}
            </span>
          </div>

          {/* Candidates, most advanced first */}
          <div className="max-h-[360px] overflow-y-auto border-t border-border">
            <table className="w-full text-sm tabular-nums">
              <thead className="sticky top-0 bg-white/95 backdrop-blur z-[1]">
                <tr className="text-muted text-xs font-semibold border-b border-border">
                  <th className="text-left px-4 py-1.5">Stock</th>
                  <th className="text-right px-2 py-1.5">Open move</th>
                  <th className="text-right px-2 py-1.5">R-factor</th>
                  <th className="text-right px-2 py-1.5">Momentum</th>
                  <th className="text-right px-2 py-1.5">Volume</th>
                  <th className="text-right px-2 py-1.5">POC / VAH / VAL</th>
                  <th className="text-right px-4 py-1.5">Status</th>
                </tr>
              </thead>
              <tbody>
                {stocks.map(s => {
                  const b = s.direction === 'call';
                  return (
                    <tr key={s.token} className={`border-b border-border/50 ${s.entered ? 'bg-spvh-50' : ''}`}>
                      <td className="px-4 py-1.5 whitespace-nowrap">
                        <span className="font-semibold text-ink">{s.symbol}</span>
                        <span className={`ml-2 text-[11px] font-bold px-1.5 py-px rounded ${b ? 'bg-up/10 text-up' : 'bg-down/10 text-down'}`}>{b ? 'CE' : 'PE'}</span>
                        <span className="ml-2 text-xs text-muted">₹{s.ltp?.toFixed(1)} · {s.sector}</span>
                      </td>
                      <td className={`px-2 py-1.5 text-right font-semibold ${s.opening_move >= 0 ? 'text-up' : 'text-down'}`}>
                        {s.opening_move >= 0 ? '+' : ''}{s.opening_move.toFixed(2)}%
                      </td>
                      <td className="px-2 py-1.5 text-right text-ink2">{s.r_factor.toFixed(2)}</td>
                      <td className={`px-2 py-1.5 text-right ${s.consec >= 2 ? (b ? 'text-up' : 'text-down') : 'text-muted'}`}>
                        {s.consec} candle{s.consec === 1 ? '' : 's'}
                      </td>
                      <td className={`px-2 py-1.5 text-right ${s.vol_ratio >= 1.3 ? 'text-up font-semibold' : 'text-muted'}`}>
                        {s.vol_ratio?.toFixed(1)}×
                      </td>
                      <td className="px-2 py-1.5 text-right text-xs text-muted">
                        {s.vp_poc ? `${s.vp_poc} / ${s.vp_vah} / ${s.vp_val}` : '—'}
                      </td>
                      <td className="px-4 py-1.5 text-right whitespace-nowrap"><Status s={s} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
