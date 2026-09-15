import { useMarketStore } from '../../store/marketStore';
import type { SectorData } from '../../store/marketStore';

function intensity(pct: number) {
  const abs = Math.min(Math.abs(pct), 4);
  const alpha = 0.25 + (abs / 4) * 0.65;
  return pct >= 0
    ? `rgba(34,197,94,${alpha.toFixed(2)})`
    : `rgba(239,68,68,${alpha.toFixed(2)})`;
}

function SectorBlock({ name, data }: { name: string; data: SectorData }) {
  const top3 = data.stocks.slice(0, 6);
  const pct = data.pct_change ?? 0;
  const area = Math.max(1, Math.abs(pct));

  return (
    <div
      className="rounded-lg p-2 border border-white/5 flex flex-col gap-1"
      style={{
        background: intensity(pct),
        gridRow: `span ${Math.ceil(area)}`,
      }}
    >
      <div className="flex items-center justify-between">
        <span className="text-white font-bold text-xs uppercase tracking-wider">{name}</span>
        <span className={`text-xs font-semibold ${pct >= 0 ? 'text-up' : 'text-down'}`}>
          {pct >= 0 ? '+' : ''}{pct.toFixed(2)}%
        </span>
      </div>
      <div className="flex flex-wrap gap-1">
        {top3.map(s => {
          const spct = s.pct_change ?? 0;
          return (
            <div
              key={s.token}
              className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-black/30 text-white"
              title={`${s.symbol} ${spct >= 0 ? '+' : ''}${spct.toFixed(2)}%`}
            >
              {s.symbol}
              <span className={`ml-1 ${spct >= 0 ? 'text-up' : 'text-down'}`}>
                {spct >= 0 ? '+' : ''}{spct.toFixed(1)}%
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function SectorHeatmap() {
  const { sectorMoves } = useMarketStore();
  const sectors = Object.entries(sectorMoves).sort(
    (a, b) => Math.abs(b[1].pct_change ?? 0) - Math.abs(a[1].pct_change ?? 0)
  );

  if (!sectors.length) {
    return (
      <div className="bg-surface rounded-xl border border-border p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-white font-semibold text-sm">Sector Scope</h2>
          <span className="text-muted text-xs">Waiting for market data…</span>
        </div>
        <div className="grid grid-cols-4 gap-2 h-40 animate-pulse">
          {[...Array(8)].map((_, i) => (
            <div key={i} className="bg-border rounded-lg" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="bg-surface rounded-xl border border-border p-4">
      <h2 className="text-white font-semibold text-sm mb-3">
        Sector Scope
        <span className="ml-2 text-xs text-muted font-normal">
          {sectors.length} sectors · live
        </span>
      </h2>
      <div className="grid grid-cols-4 gap-2">
        {sectors.map(([name, data]) => (
          <SectorBlock key={name} name={name} data={data} />
        ))}
      </div>
    </div>
  );
}
