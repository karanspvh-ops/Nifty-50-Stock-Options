import { useEffect, useState } from 'react';
import { useMarketStore } from '../../store/marketStore';

const API = 'http://localhost:8000';

interface RankedStock {
  token: string; symbol: string; ltp: number | null;
  pct_change: number; r_factor: number; score: number;
  signal: 'up' | 'down' | 'flat';
}
interface SectorGroup {
  sector: string; sector_pct: number; sector_dir: string;
  up_count: number; down_count: number; stocks: RankedStock[];
}

function SignalArrow({ s }: { s: string }) {
  if (s === 'up')   return <span className="text-up">▲</span>;
  if (s === 'down') return <span className="text-down">▼</span>;
  return <span className="text-muted">▬</span>;
}

function SectorCard({ g }: { g: SectorGroup }) {
  const total = g.up_count + g.down_count || 1;
  const upPct = Math.round((g.up_count / total) * 100);
  return (
    <div className="bg-bg rounded-lg border border-border overflow-hidden">
      {/* Sector header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border">
        <div className="flex items-center gap-2">
          <span className="text-white font-bold text-xs uppercase tracking-wider">{g.sector}</span>
          <span className={`text-xs font-semibold ${g.sector_pct >= 0 ? 'text-up' : 'text-down'}`}>
            {g.sector_pct >= 0 ? '+' : ''}{g.sector_pct.toFixed(2)}%
          </span>
        </div>
        <span className="text-[10px] text-muted">{g.up_count}▲ {g.down_count}▼</span>
      </div>
      {/* Up/Down ratio bar */}
      <div className="h-1 w-full bg-down/40 flex">
        <div className="h-1 bg-up" style={{ width: `${upPct}%` }} />
      </div>
      {/* Stock rows */}
      <table className="w-full">
        <thead>
          <tr className="text-muted text-[9px] uppercase tracking-wider border-b border-border/50">
            <th className="text-left px-3 py-1">Symbol</th>
            <th className="text-right px-2 py-1">LTP</th>
            <th className="text-right px-2 py-1">%</th>
            <th className="text-right px-2 py-1">R-Fact</th>
            <th className="text-center px-2 py-1">Signal</th>
          </tr>
        </thead>
        <tbody>
          {g.stocks.map(s => (
            <tr key={s.token} className="border-b border-border/30 hover:bg-border/20">
              <td className="px-3 py-1 text-white text-xs font-medium">{s.symbol}</td>
              <td className="px-2 py-1 text-right text-muted text-xs">
                {s.ltp ? `₹${s.ltp.toFixed(1)}` : '—'}
              </td>
              <td className={`px-2 py-1 text-right text-xs font-semibold ${s.pct_change >= 0 ? 'text-up' : 'text-down'}`}>
                {s.pct_change >= 0 ? '+' : ''}{s.pct_change.toFixed(2)}%
              </td>
              <td className="px-2 py-1 text-right text-xs text-accent">{s.r_factor.toFixed(2)}</td>
              <td className="px-2 py-1 text-center text-xs"><SignalArrow s={s.signal} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function StockRanking() {
  const { settings } = useMarketStore();
  const [groups, setGroups] = useState<SectorGroup[]>([]);
  const [order, setOrder]   = useState<'most_likely' | 'least_likely'>('most_likely');

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(`${API}/api/market/ranking?index=${settings.active_index}&order=${order}`);
        setGroups(await res.json());
      } catch { /* feed offline */ }
    };
    load();
    const id = setInterval(load, 3000);
    return () => clearInterval(id);
  }, [settings.active_index, order]);

  return (
    <div className="bg-surface rounded-xl border border-border p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-white font-semibold text-sm">
          Stock Ranking — Sector-wise
          <span className="ml-2 text-xs text-muted font-normal">{settings.active_index} · live</span>
        </h2>
        {/* Filter */}
        <div className="flex gap-1 bg-bg rounded-lg p-0.5 border border-border">
          {([
            ['most_likely',  'Most Likely'],
            ['least_likely', 'Least Likely'],
          ] as const).map(([val, label]) => (
            <button
              key={val}
              onClick={() => setOrder(val)}
              className={`px-3 py-1 rounded-md text-xs font-medium transition-colors
                ${order === val ? 'bg-accent text-white' : 'text-muted hover:text-white'}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {!groups.length ? (
        <p className="text-muted text-xs">Waiting for market data…</p>
      ) : (
        <div className="grid grid-cols-2 xl:grid-cols-3 gap-3 max-h-[480px] overflow-y-auto pr-1">
          {groups.map(g => <SectorCard key={g.sector} g={g} />)}
        </div>
      )}
    </div>
  );
}
