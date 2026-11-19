import { useEffect, useState, useRef } from 'react';

import { API } from '../../lib/api';

interface Tradable {
  symbol: string; token: string; sector: string;
  direction: 'call' | 'put';
  opened_at: string; live_secs: number;
  entry_score: number; max_score: number;
  ltp: number | null; sector_pct: number; reason: string;
}

function fmtDuration(secs: number) {
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60), s = secs % 60;
  return m < 60 ? `${m}m ${s}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}
function fmtTime(iso: string) {
  return new Date(iso).toLocaleTimeString('en-IN', { hour12: false });
}

/** Compact list of stocks currently meeting entry criteria: one dense row each. */
export default function TradableSignals() {
  const [active, setActive] = useState<Tradable[]>([]);
  const prevTokens = useRef<Set<string>>(new Set());
  const [flash, setFlash] = useState<Set<string>>(new Set());

  useEffect(() => {
    const load = async () => {
      try {
        const res  = await fetch(`${API}/api/scanner/tradable/active`);
        const data: Tradable[] = await res.json();
        // Detect newly-appeared tokens to highlight briefly
        const now = new Set(data.map(d => d.token));
        const fresh = new Set([...now].filter(t => !prevTokens.current.has(t)));
        if (fresh.size) {
          setFlash(fresh);
          setTimeout(() => setFlash(new Set()), 2500);
        }
        prevTokens.current = now;
        setActive(data);
      } catch { /* offline */ }
    };
    load();
    const id = setInterval(load, 2000);
    return () => clearInterval(id);
  }, []);

  // Strongest first: full score, then the longest-standing signals
  const rows = [...active].sort((a, b) =>
    b.entry_score / b.max_score - a.entry_score / a.max_score || b.live_secs - a.live_secs);
  const calls = active.filter(t => t.direction === 'call').length;

  return (
    <div className="bg-surface rounded-panel border border-border overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <h2 className="text-ink font-bold text-base flex items-center gap-2">
          Tradable Now
          <span className="text-xs text-muted font-normal">stocks meeting entry criteria</span>
        </h2>
        <span className="flex items-center gap-3 text-xs text-muted">
          {active.length > 0 && (
            <span><span className="text-up font-semibold">{calls} call</span> · <span className="text-down font-semibold">{active.length - calls} put</span></span>
          )}
          <span className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full ${active.length ? 'bg-up' : 'bg-faint'}`} />
            {active.length} live
          </span>
        </span>
      </div>

      {!active.length ? (
        <p className="text-muted text-sm px-4 py-4">No stocks meet the entry criteria right now.</p>
      ) : (
        <div className="max-h-[300px] overflow-y-auto">
          <table className="w-full text-sm tabular-nums">
            <thead className="sticky top-0 bg-white/95 backdrop-blur z-[1]">
              <tr className="text-muted text-xs font-semibold border-b border-border">
                <th className="text-left px-4 py-1.5">Stock</th>
                <th className="text-left px-2 py-1.5">Sector</th>
                <th className="text-right px-2 py-1.5">Score</th>
                <th className="text-right px-2 py-1.5">LTP</th>
                <th className="text-right px-2 py-1.5">Since</th>
                <th className="text-right px-4 py-1.5">Live for</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(t => {
                const call = t.direction === 'call';
                const full = t.entry_score === t.max_score;
                return (
                  <tr key={t.token}
                      className={`border-b border-border/50 transition-colors duration-700 ${flash.has(t.token) ? 'bg-spvh-100/80' : ''}`}>
                    <td className="px-4 py-1.5 whitespace-nowrap">
                      <span className="font-semibold text-ink">{t.symbol}</span>
                      <span className={`ml-2 text-[11px] font-bold px-1.5 py-px rounded ${call ? 'bg-up/10 text-up' : 'bg-down/10 text-down'}`}>
                        {call ? 'CE' : 'PE'}
                      </span>
                    </td>
                    <td className="px-2 py-1.5 text-muted text-xs whitespace-nowrap">{t.sector}</td>
                    <td className={`px-2 py-1.5 text-right font-semibold ${full ? 'text-accent' : 'text-ink2'}`}>
                      {t.entry_score}/{t.max_score}
                    </td>
                    <td className="px-2 py-1.5 text-right text-ink">{t.ltp ? `₹${t.ltp.toFixed(1)}` : '—'}</td>
                    <td className="px-2 py-1.5 text-right text-muted text-xs">{fmtTime(t.opened_at)}</td>
                    <td className="px-4 py-1.5 text-right text-ink2 text-xs">{fmtDuration(t.live_secs)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
