import { useMemo, useCallback } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ReferenceLine, ResponsiveContainer, Cell,
} from 'recharts';
import { useMarketStore } from '../../store/marketStore';

export default function SectorBarChart() {
  const { sectorMoves } = useMarketStore();

  const data = useMemo(() =>
    Object.entries(sectorMoves)
      .map(([sector, d]) => ({ sector, pct: d.pct_change ?? 0 }))
      .sort((a, b) => b.pct - a.pct),
    [sectorMoves]
  );

  const tooltipFormatter = useCallback(
    (val: unknown) => [`${Number(val).toFixed(2)}%`, 'Change'],
    []
  );

  if (!data.length) return null;

  return (
    <div className="bg-surface rounded-panel border border-border p-4">
      <h2 className="text-ink font-bold text-base mb-3">
        Sector Momentum
        <span className="ml-2 text-xs text-muted font-normal">avg % move today</span>
      </h2>
      <ResponsiveContainer width="100%" height={190}>
        <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -20 }} barCategoryGap="20%" isAnimationActive={false}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgb(22 24 29 / 0.08)" vertical={false} />
          <XAxis
            dataKey="sector"
            tick={{ fill: 'rgb(92 98 112)', fontSize: 9 }}
            axisLine={false}
            tickLine={false}
            interval={0}
            angle={-35}
            textAnchor="end"
            height={40}
          />
          <YAxis
            tick={{ fill: 'rgb(92 98 112)', fontSize: 9 }}
            axisLine={false}
            tickLine={false}
            tickFormatter={v => `${v}%`}
          />
          <Tooltip
            contentStyle={{ background: 'rgb(255 255 255 / 0.92)', border: '1px solid rgb(227 229 234)', borderRadius: 12, boxShadow: '0 12px 30px -12px rgb(22 24 29 / 0.25)', backdropFilter: 'blur(16px)' }}
            labelStyle={{ color: 'rgb(22 24 29)', fontSize: 12, fontWeight: 600 }}
            formatter={tooltipFormatter}
          />
          <ReferenceLine y={0} stroke="rgb(168 124 34 / 0.6)" />
          <Bar dataKey="pct" radius={[2, 2, 2, 2]} maxBarSize={35} isAnimationActive={false}>
            {data.map((entry, i) => (
              <Cell key={i} fill={entry.pct >= 0 ? 'rgb(11 125 69)' : 'rgb(196 43 35)'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
