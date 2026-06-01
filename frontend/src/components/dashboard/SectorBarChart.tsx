import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ReferenceLine, ResponsiveContainer, Cell,
} from 'recharts';
import { useMarketStore } from '../../store/marketStore';

export default function SectorBarChart() {
  const { sectorMoves } = useMarketStore();

  const data = Object.entries(sectorMoves)
    .map(([sector, d]) => ({ sector, pct: d.pct_change }))
    .sort((a, b) => b.pct - a.pct);

  if (!data.length) return null;

  return (
    <div className="bg-surface rounded-xl border border-border p-4">
      <h2 className="text-white font-semibold text-sm mb-3">
        Sector Momentum
        <span className="ml-2 text-xs text-muted font-normal">avg % move today</span>
      </h2>
      <ResponsiveContainer width="100%" height={190}>
        <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -20 }} barCategoryGap="20%">
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" vertical={false} />
          <XAxis
            dataKey="sector"
            tick={{ fill: '#64748b', fontSize: 9 }}
            axisLine={false}
            tickLine={false}
            interval={0}
            angle={-35}
            textAnchor="end"
            height={40}
          />
          <YAxis
            tick={{ fill: '#64748b', fontSize: 9 }}
            axisLine={false}
            tickLine={false}
            tickFormatter={v => `${v}%`}
          />
          <Tooltip
            contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8 }}
            labelStyle={{ color: '#e2e8f0', fontSize: 11 }}
            formatter={(val: any) => [`${Number(val).toFixed(2)}%`, 'Change']}
          />
          <ReferenceLine y={0} stroke="#475569" />
          <Bar dataKey="pct" radius={[2, 2, 2, 2]} maxBarSize={35}>
            {data.map((entry, i) => (
              <Cell key={i} fill={entry.pct >= 0 ? '#22c55e' : '#ef4444'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
