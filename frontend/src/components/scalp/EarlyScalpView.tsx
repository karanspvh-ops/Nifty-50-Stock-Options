import { useEffect, useState, useCallback } from 'react';

const API = 'http://localhost:8000';

// ── Types ─────────────────────────────────────────────────────────────────────
interface Candidate {
  symbol:       string;
  token:        string;
  gap_pct:      number;
  opening_move: number;
  vol_ratio:    number;
  oi_signal:    'bullish' | 'bearish' | 'neutral';
  oi_ce:        number;
  oi_pe:        number;
  direction:    'call' | 'put';
  score:        number;
  confirmed:    boolean;
  consec_1m:    number;
  consec_3m:    number;
  ltp:          number;
  est_premium:  number;
  entered:      boolean;
  skip_reason:  string;
}

interface ScalpPlan {
  status:       string;
  phase:        string;
  market_trend: string;
  nifty_move:   number;
  candidates:   Candidate[];
  note:         string;
  generated_at: string;
  params: {
    gap_min_pct:        number;
    move_min_pct:       number;
    vol_ratio_min:      number;
    max_positions:      number;
    hard_sl_pct:        number;
    target_pct:         number;
    trail_activate_pct: number;
    trail_gap_pct:      number;
  };
}

interface ScalpTrade {
  trade_id:     number;
  symbol:       string;
  option_symbol?: string;
  direction:    string;
  env:          string;
  entry:        number;
  ltp:          number | null;
  pnl_pct:      number;
  pnl?:         number;
  quantity?:    number | null;
  lot_size?:    number | null;
  hard_sl:      number;
  target:       number | null;
  status:       string;
  entered_at?:  string | null;
  entry_logic?: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmt(n: number | null | undefined, d = 2) {
  if (n == null || isNaN(n)) return '—';
  return (n >= 0 ? '+' : '') + n.toFixed(d) + '%';
}

function fmtK(n: number) {
  if (n === 0) return '—';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000)     return (n / 1_000).toFixed(0) + 'K';
  return String(n);
}

function fmtTime(iso?: string | null) {
  if (!iso) return '—';
  try { return iso.replace('T', ' ').slice(11, 19); }
  catch { return '—'; }
}

const PHASE_COLOR: Record<string, string> = {
  IDLE:     'text-muted',
  WARMING:  'text-yellow-400',
  SCANNING: 'text-accent',
  ENTERING: 'text-up',
  MANAGING: 'text-up',
  DONE:     'text-muted',
  KILLED:   'text-down',
};

const OI_BADGE: Record<string, string> = {
  bullish: 'bg-up/20 text-up',
  bearish: 'bg-down/20 text-down',
  neutral: 'bg-border text-muted',
};

// ── Sub-components ────────────────────────────────────────────────────────────
function ParamSlider({
  label, value, min, max, step, unit, onChange,
}: {
  label: string; value: number; min: number; max: number;
  step: number; unit: string; onChange: (v: number) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between text-xs text-muted">
        <span>{label}</span>
        <span className="text-white font-semibold">{value}{unit}</span>
      </div>
      <input
        type="range" min={min} max={max} step={step} value={value}
        onChange={e => onChange(parseFloat(e.target.value))}
        className="w-full accent-accent h-1.5"
      />
    </div>
  );
}

function StatusBadge({ phase }: { phase: string }) {
  const col = PHASE_COLOR[phase] || 'text-muted';
  const dot = ['SCANNING','ENTERING','MANAGING'].includes(phase)
    ? 'bg-up animate-pulse' : 'bg-muted';
  return (
    <span className={`flex items-center gap-1.5 text-xs font-semibold ${col}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${dot}`} />
      {phase}
    </span>
  );
}

// ── Active trades panel ───────────────────────────────────────────────────────
function ActiveTrades({ trades }: { trades: ScalpTrade[] }) {
  if (!trades.length) {
    return (
      <div className="text-muted text-sm text-center py-6">
        No active Early Scalp positions
      </div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-muted border-b border-border">
            <th className="text-left py-2 pr-3">Symbol</th>
            <th className="text-left py-2 pr-3">Option</th>
            <th className="text-right py-2 pr-3">Entry</th>
            <th className="text-right py-2 pr-3">LTP</th>
            <th className="text-right py-2 pr-3">PnL%</th>
            <th className="text-right py-2 pr-3">PnL ₹</th>
            <th className="text-right py-2 pr-3">SL</th>
            <th className="text-right py-2 pr-3">Target</th>
            <th className="text-right py-2">Entered</th>
          </tr>
        </thead>
        <tbody>
          {trades.map(t => {
            const pnlCol = t.pnl_pct >= 0 ? 'text-up' : 'text-down';
            return (
              <tr key={t.trade_id} className="border-b border-border/40 hover:bg-border/20">
                <td className="py-2 pr-3 font-semibold text-white">{t.symbol}</td>
                <td className="py-2 pr-3 text-muted">{t.option_symbol || '—'}</td>
                <td className="py-2 pr-3 text-right">₹{t.entry?.toFixed(2) ?? '—'}</td>
                <td className="py-2 pr-3 text-right text-accent">
                  {t.ltp != null ? `₹${t.ltp.toFixed(2)}` : '—'}
                </td>
                <td className={`py-2 pr-3 text-right font-semibold ${pnlCol}`}>
                  {fmt(t.pnl_pct)}
                </td>
                <td className={`py-2 pr-3 text-right ${pnlCol}`}>
                  {t.pnl != null ? `₹${t.pnl.toFixed(0)}` : '—'}
                </td>
                <td className="py-2 pr-3 text-right text-down">
                  {t.hard_sl != null ? `₹${t.hard_sl.toFixed(2)}` : '—'}
                </td>
                <td className="py-2 pr-3 text-right text-up">
                  {t.target != null ? `₹${t.target.toFixed(2)}` : '—'}
                </td>
                <td className="py-2 text-right text-muted">
                  {fmtTime(t.entered_at)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Candidate table ───────────────────────────────────────────────────────────
function CandidateTable({ candidates }: { candidates: Candidate[] }) {
  const [showAll, setShowAll] = useState(false);
  const visible = showAll ? candidates : candidates.slice(0, 10);

  if (!candidates.length) {
    return (
      <div className="text-muted text-sm text-center py-6">
        No candidates yet — waiting for scan window (9:20)
      </div>
    );
  }

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted border-b border-border">
              <th className="text-left py-2 pr-2">#</th>
              <th className="text-left py-2 pr-2">Symbol</th>
              <th className="text-left py-2 pr-2">Dir</th>
              <th className="text-right py-2 pr-2">Gap%</th>
              <th className="text-right py-2 pr-2">Move%</th>
              <th className="text-right py-2 pr-2">Vol</th>
              <th className="text-right py-2 pr-2">1m</th>
              <th className="text-right py-2 pr-2">3m</th>
              <th className="text-center py-2 pr-2">OI</th>
              <th className="text-right py-2 pr-2">Score</th>
              <th className="text-right py-2 pr-2">LTP</th>
              <th className="text-right py-2 pr-2">Est Prem</th>
              <th className="text-left py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((c, i) => {
              const gapCol  = c.gap_pct  >= 0 ? 'text-up' : 'text-down';
              const moveCol = c.opening_move >= 0 ? 'text-up' : 'text-down';
              return (
                <tr
                  key={c.symbol}
                  className={`border-b border-border/30 hover:bg-border/20
                    ${c.entered ? 'bg-up/5' : ''}`}
                >
                  <td className="py-1.5 pr-2 text-muted">{i + 1}</td>
                  <td className="py-1.5 pr-2 font-semibold text-white">{c.symbol}</td>
                  <td className="py-1.5 pr-2">
                    <span className={`px-1.5 py-0.5 rounded text-xs font-semibold
                      ${c.direction === 'call'
                        ? 'bg-up/20 text-up' : 'bg-down/20 text-down'}`}>
                      {c.direction.toUpperCase()}
                    </span>
                  </td>
                  <td className={`py-1.5 pr-2 text-right font-semibold ${gapCol}`}>
                    {fmt(c.gap_pct)}
                  </td>
                  <td className={`py-1.5 pr-2 text-right ${moveCol}`}>
                    {fmt(c.opening_move)}
                  </td>
                  <td className={`py-1.5 pr-2 text-right
                    ${c.vol_ratio >= 1.3 ? 'text-accent' : 'text-muted'}`}>
                    {c.vol_ratio > 0 ? c.vol_ratio.toFixed(1) + 'x' : '—'}
                  </td>
                  <td className={`py-1.5 pr-2 text-right
                    ${c.consec_1m >= 2 ? 'text-up' : 'text-muted'}`}>
                    {c.consec_1m}
                  </td>
                  <td className={`py-1.5 pr-2 text-right
                    ${c.consec_3m >= 1 ? 'text-up' : 'text-muted'}`}>
                    {c.consec_3m}
                  </td>
                  <td className="py-1.5 pr-2 text-center">
                    <span className={`px-1.5 py-0.5 rounded text-xs ${OI_BADGE[c.oi_signal]}`}>
                      {c.oi_signal === 'neutral' ? '—' : c.oi_signal}
                      {c.oi_ce > 0 && (
                        <span className="ml-1 opacity-70">
                          C:{fmtK(c.oi_ce)} P:{fmtK(c.oi_pe)}
                        </span>
                      )}
                    </span>
                  </td>
                  <td className="py-1.5 pr-2 text-right font-semibold text-accent">
                    {c.score.toFixed(2)}
                  </td>
                  <td className="py-1.5 pr-2 text-right text-muted">
                    ₹{c.ltp.toFixed(1)}
                  </td>
                  <td className="py-1.5 pr-2 text-right text-muted">
                    ~₹{c.est_premium.toFixed(1)}
                  </td>
                  <td className="py-1.5 text-xs">
                    <div className="flex items-center gap-2">
                      {c.entered ? (
                        <span className="text-up font-semibold">IN TRADE</span>
                      ) : c.confirmed ? (
                        <span className="text-accent font-semibold">CONFIRMED</span>
                      ) : (
                        <span className="text-muted" title={c.skip_reason}>
                          {c.skip_reason ? c.skip_reason.split(';')[0] : 'watching'}
                        </span>
                      )}
                      {!c.entered && env === 'paper' && (
                        <button
                          onClick={() => forceEntry(c.symbol, c.direction)}
                          disabled={forcing === `${c.symbol}-${c.direction}`}
                          className="ml-1 px-1.5 py-0.5 rounded text-[9px] font-semibold
                            border border-accent/40 text-accent/70
                            hover:border-accent hover:text-accent hover:bg-accent/10
                            disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                        >
                          {forcing === `${c.symbol}-${c.direction}` ? '…' : 'Force'}
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {candidates.length > 10 && (
        <button
          onClick={() => setShowAll(v => !v)}
          className="mt-2 text-xs text-accent hover:underline"
        >
          {showAll ? 'Show top 10' : `Show all ${candidates.length}`}
        </button>
      )}
    </div>
  );
}

// ── Main view ─────────────────────────────────────────────────────────────────
export default function EarlyScalpView() {
  const [plan,        setPlan]        = useState<ScalpPlan | null>(null);
  const [trades,      setTrades]      = useState<ScalpTrade[]>([]);
  const [enabled,     setEnabledState]= useState(true);
  const [localParams, setLocalParams] = useState<ScalpPlan['params'] | null>(null);
  const [saving,      setSaving]      = useState(false);
  const [showParams,  setShowParams]  = useState(false);
  const [env]                         = useState<'paper' | 'live'>('paper');
  const [forcing,     setForcing]     = useState<string | null>(null);
  const [forceMsg,    setForceMsg]    = useState<string | null>(null);

  // Load plan
  const loadPlan = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/scalp/plan`);
      if (r.ok) {
        const d: ScalpPlan = await r.json();
        setPlan(d);
        if (!localParams) setLocalParams(d.params);
      }
    } catch {}
  }, [localParams]);

  // Load ES trades
  const loadTrades = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/trades?env=${env}`);
      if (r.ok) {
        const all: ScalpTrade[] = await r.json();
        setTrades(all.filter(t =>
          t.entry_logic?.includes('[ES]') && t.status === 'open'
        ));
      }
    } catch {}
  }, [env]);

  useEffect(() => {
    loadPlan();
    loadTrades();
    const id1 = setInterval(loadPlan,   3000);
    const id2 = setInterval(loadTrades, 2000);
    return () => { clearInterval(id1); clearInterval(id2); };
  }, [loadPlan, loadTrades]);

  const toggleEnabled = async () => {
    const next = !enabled;
    setEnabledState(next);
    try {
      await fetch(`${API}/api/scalp/enable`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: next }),
      });
    } catch {}
  };

  const forceEntry = async (symbol: string, direction: string) => {
    const key = `${symbol}-${direction}`;
    setForcing(key);
    setForceMsg(null);
    try {
      const r = await fetch(`${API}/api/scalp/force-entry`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, direction }),
      });
      const d = await r.json();
      if (d.status === 'entered') {
        setForceMsg(`✓ Entered ${d.option_symbol} @ ₹${d.entry_price} · SL ₹${d.sl_price?.toFixed(2)}`);
        loadTrades();
      } else {
        setForceMsg(`✗ ${d.reason}`);
      }
    } catch (e) {
      setForceMsg('✗ Request failed');
    }
    setForcing(null);
    setTimeout(() => setForceMsg(null), 6000);
  };

  const saveParams = async () => {
    if (!localParams) return;
    setSaving(true);
    try {
      await fetch(`${API}/api/scalp/params`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(localParams),
      });
    } catch {}
    setSaving(false);
  };

  const p = localParams ?? plan?.params;
  const phase = plan?.phase ?? 'IDLE';
  const confirmed = plan?.candidates.filter(c => c.confirmed).length ?? 0;
  const entered   = plan?.candidates.filter(c => c.entered).length ?? 0;
  const trendCol  = plan?.market_trend === 'bullish' ? 'text-up'
                  : plan?.market_trend === 'bearish' ? 'text-down' : 'text-muted';

  return (
    <div className="p-6 space-y-4 overflow-y-auto h-full">

      {/* ── Force-entry result toast ───────────────────────────────────────── */}
      {forceMsg && (
        <div className={`text-xs px-3 py-2 rounded-lg border font-medium
          ${forceMsg.startsWith('✓')
            ? 'bg-up/10 border-up/30 text-up'
            : 'bg-down/10 border-down/30 text-down'}`}>
          {forceMsg}
        </div>
      )}

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-white font-bold text-lg">Early Scalp</h1>
          <span className="text-xs font-normal text-accent bg-accent/10 px-2 py-0.5 rounded">
            9:25–9:30 · Gap &amp; Go
          </span>
          <StatusBadge phase={phase} />
        </div>
        <div className="flex items-center gap-3">
          {/* Market context */}
          {plan && (
            <div className="flex items-center gap-3 text-xs text-muted">
              <span>
                Nifty{' '}
                <span className={plan.nifty_move >= 0 ? 'text-up' : 'text-down'}>
                  {fmt(plan.nifty_move)}
                </span>
              </span>
              <span>
                Trend <span className={`font-semibold ${trendCol}`}>
                  {plan.market_trend.toUpperCase()}
                </span>
              </span>
              <span>
                <span className="text-accent font-semibold">{confirmed}</span> confirmed
                &nbsp;·&nbsp;
                <span className="text-up font-semibold">{entered}</span> entered
              </span>
            </div>
          )}
          {/* Enable toggle */}
          <button
            onClick={toggleEnabled}
            className={`text-xs px-3 py-1 rounded font-semibold transition-colors
              ${enabled
                ? 'bg-up/20 text-up hover:bg-up/30'
                : 'bg-border text-muted hover:bg-border/80'}`}
          >
            {enabled ? 'ENABLED' : 'DISABLED'}
          </button>
          {/* Params toggle */}
          <button
            onClick={() => setShowParams(v => !v)}
            className="text-xs px-3 py-1 rounded bg-border text-muted hover:text-white transition-colors"
          >
            {showParams ? 'Hide Params' : 'Params'}
          </button>
        </div>
      </div>

      {/* ── Note bar ───────────────────────────────────────────────────────── */}
      {plan?.note && (
        <div className="text-xs text-muted bg-surface border border-border rounded px-3 py-2">
          {plan.note}
          {plan.generated_at && (
            <span className="ml-2 opacity-50">
              · {fmtTime(plan.generated_at)} IST
            </span>
          )}
        </div>
      )}

      {/* ── Parameters panel ───────────────────────────────────────────────── */}
      {showParams && p && (
        <div className="bg-surface border border-border rounded-lg p-4 space-y-4">
          <div className="text-xs font-semibold text-muted uppercase tracking-wide mb-2">
            Parameters
          </div>
          <div className="grid grid-cols-2 gap-x-8 gap-y-4">
            <ParamSlider label="Min gap from prev close" unit="%"
              value={p.gap_min_pct} min={0.2} max={3} step={0.1}
              onChange={v => setLocalParams(prev => prev ? {...prev, gap_min_pct: v} : prev)} />
            <ParamSlider label="Min opening move from 9:15" unit="%"
              value={p.move_min_pct} min={0.5} max={3} step={0.1}
              onChange={v => setLocalParams(prev => prev ? {...prev, move_min_pct: v} : prev)} />
            <ParamSlider label="Hard SL" unit="%"
              value={p.hard_sl_pct} min={2} max={10} step={0.5}
              onChange={v => setLocalParams(prev => prev ? {...prev, hard_sl_pct: v} : prev)} />
            <ParamSlider label="Target" unit="%"
              value={p.target_pct} min={5} max={30} step={0.5}
              onChange={v => setLocalParams(prev => prev ? {...prev, target_pct: v} : prev)} />
            <ParamSlider label="Trail arm" unit="%"
              value={p.trail_activate_pct} min={3} max={20} step={0.5}
              onChange={v => setLocalParams(prev => prev ? {...prev, trail_activate_pct: v} : prev)} />
            <ParamSlider label="Trail gap" unit="%"
              value={p.trail_gap_pct} min={2} max={12} step={0.5}
              onChange={v => setLocalParams(prev => prev ? {...prev, trail_gap_pct: v} : prev)} />
            <ParamSlider label="Min vol ratio" unit="x"
              value={p.vol_ratio_min} min={1} max={3} step={0.1}
              onChange={v => setLocalParams(prev => prev ? {...prev, vol_ratio_min: v} : prev)} />
            <ParamSlider label="Max positions" unit=""
              value={p.max_positions} min={1} max={5} step={1}
              onChange={v => setLocalParams(prev => prev ? {...prev, max_positions: v} : prev)} />
          </div>
          <div className="flex items-center justify-between pt-2">
            <div className="text-xs text-muted">
              SL {p.hard_sl_pct}% · Target {p.target_pct}% ·
              Trail @+{p.trail_activate_pct}% gap {p.trail_gap_pct}% ·
              Stop 10:30 AM
            </div>
            <button
              onClick={saveParams}
              disabled={saving}
              className="text-xs px-4 py-1.5 rounded bg-accent text-white hover:bg-accent/80 disabled:opacity-50"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      )}

      {/* ── Active positions ────────────────────────────────────────────────── */}
      <div className="bg-surface border border-border rounded-lg p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="text-sm font-semibold text-white">
            Active Positions
            {trades.length > 0 && (
              <span className="ml-2 text-xs text-up bg-up/10 px-1.5 py-0.5 rounded">
                {trades.length} open
              </span>
            )}
          </div>
        </div>
        <ActiveTrades trades={trades} />
      </div>

      {/* ── Scan results ────────────────────────────────────────────────────── */}
      <div className="bg-surface border border-border rounded-lg p-4">
        <div className="flex items-center gap-3 mb-3">
          <div className="text-sm font-semibold text-white">Scan Candidates</div>
          <div className="text-xs text-muted">
            Ranked by composite score (gap × move × vol × OI)
          </div>
          {/* Column legend */}
          <div className="ml-auto flex items-center gap-3 text-xs text-muted">
            <span><span className="text-accent font-semibold">1m</span> = consecutive 1-min candles</span>
            <span><span className="text-accent font-semibold">3m</span> = consecutive 3-min candles</span>
          </div>
        </div>

        {/* Filter tabs: all / confirmed / entered */}
        <CandidateTable candidates={plan?.candidates ?? []} />
      </div>

      {/* ── Strategy logic summary ──────────────────────────────────────────── */}
      <div className="bg-surface border border-border rounded-lg p-4">
        <div className="text-xs font-semibold text-muted uppercase tracking-wide mb-3">
          How this strategy works
        </div>
        <div className="grid grid-cols-3 gap-4 text-xs">
          <div>
            <div className="text-white font-semibold mb-1">Entry (9:25–9:30)</div>
            <ul className="text-muted space-y-0.5">
              <li>· Gap from prev close ≥ {p?.gap_min_pct ?? 0.5}%</li>
              <li>· Opening move from 9:15 ≥ {p?.move_min_pct ?? 1.0}%</li>
              <li>· 2+ consecutive 1-min candles in direction</li>
              <li>· 1+ consecutive 3-min candles in direction</li>
              <li>· Volume ratio ≥ {p?.vol_ratio_min ?? 1.3}x</li>
              <li>· OI not contradicting direction</li>
            </ul>
          </div>
          <div>
            <div className="text-white font-semibold mb-1">Exit rules</div>
            <ul className="text-muted space-y-0.5">
              <li>· Hard SL: option premium −{p?.hard_sl_pct ?? 5}%</li>
              <li>· Target: option premium +{p?.target_pct ?? 12}%</li>
              <li>· Trail: armed at +{p?.trail_activate_pct ?? 8}%, gap {p?.trail_gap_pct ?? 5}%</li>
              <li>· Time stop: 10:30 AM (square off all)</li>
            </ul>
          </div>
          <div>
            <div className="text-white font-semibold mb-1">OI signal</div>
            <ul className="text-muted space-y-0.5">
              <li>· Fetches ATM Call &amp; Put OI via Kite quote</li>
              <li>· Call OI &gt; Put OI × 1.25 → bullish OI</li>
              <li>· Put OI &gt; Call OI × 1.25 → bearish OI</li>
              <li>· Skips if OI contradicts trade direction</li>
            </ul>
          </div>
        </div>
      </div>

    </div>
  );
}
