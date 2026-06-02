import { useEffect, useState, useRef } from 'react';

const API = 'http://localhost:8000';

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
  return `${m}m ${s}s`;
}
function fmtTime(iso: string) {
  return new Date(iso).toLocaleTimeString('en-IN', { hour12: false });
}

export default function TradableSignals() {
  const [active, setActive] = useState<Tradable[]>([]);
  const prevTokens = useRef<Set<string>>(new Set());
  const [flash, setFlash] = useState<Set<string>>(new Set());

  useEffect(() => {
    const load = async () => {
      try {
        const res  = await fetch(`${API}/api/scanner/tradable/active`);
        const data: Tradable[] = await res.json();
        // Detect newly-appeared tokens to flash
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

  return (
    <div className="bg-surface rounded-xl border border-border p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-white font-semibold text-sm flex items-center gap-2">
          Tradable Now
          <span className="text-xs text-muted font-normal">stocks meeting entry criteria</span>
        </h2>
        <span className="flex items-center gap-1.5 text-xs">
          <span className={`w-2 h-2 rounded-full ${active.length ? 'bg-up animate-pulse' : 'bg-muted'}`} />
          <span className="text-muted">{active.length} live</span>
        </span>
      </div>

      {!active.length ? (
        <p className="text-muted text-xs py-3">
          No stocks currently meet the entry criteria. Watching the market…
        </p>
      ) : (
        <div className="grid grid-cols-2 xl:grid-cols-3 gap-2">
          {active.map(t => (
            <div
              key={t.token}
              className={`rounded-lg border p-3 transition-all duration-500
                ${flash.has(t.token) ? 'border-up shadow-[0_0_14px_rgba(34,197,94,0.6)]' : 'border-border'}
                ${t.direction === 'call' ? 'bg-up/5' : 'bg-down/5'}`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-white font-bold text-sm">{t.symbol}</span>
                <span className={`text-[10px] px-1.5 py-0.5 rounded font-semibold
                  ${t.direction === 'call' ? 'bg-up/20 text-up' : 'bg-down/20 text-down'}`}>
                  {t.direction.toUpperCase()}
                </span>
              </div>
              <div className="flex items-center justify-between text-[11px] text-muted mb-1">
                <span>{t.sector}</span>
                <span className="text-accent font-semibold">{t.entry_score}/{t.max_score}</span>
              </div>
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-muted">LTP</span>
                <span className="text-white">{t.ltp ? `₹${t.ltp.toFixed(1)}` : '—'}</span>
              </div>
              <div className="mt-2 pt-2 border-t border-border/50 flex items-center justify-between text-[10px]">
                <span className="text-muted">since {fmtTime(t.opened_at)}</span>
                <span className="text-up font-semibold">▲ {fmtDuration(t.live_secs)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
