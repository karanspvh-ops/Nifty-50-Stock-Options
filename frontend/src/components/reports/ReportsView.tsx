import { useEffect, useState } from 'react';
import { useMarketStore } from '../../store/marketStore';

const API = 'http://localhost:8000';

export default function ReportsView() {
  const { settings } = useMarketStore();
  const env = settings.is_live ? 'live' : 'paper';
  const [pnl, setPnl] = useState<any>(null);
  const [ml,  setMl]  = useState<any>(null);
  const [tab, setTab] = useState<'pnl' | 'ml'>('pnl');
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    const [r1, r2] = await Promise.all([
      fetch(`${API}/api/reports/pnl/${env}`).then(r => r.json()),
      fetch(`${API}/api/reports/ml/${env}`).then(r => r.json()),
    ]);
    setPnl(r1); setMl(r2); setLoading(false);
  };

  const generate = async () => {
    setLoading(true);
    await fetch(`${API}/api/reports/pnl/${env}/generate`, { method: 'POST' });
    await fetch(`${API}/api/reports/ml/${env}/trigger`,   { method: 'POST' });
    await load();
  };

  useEffect(() => { load(); }, [env]);

  return (
    <div className="p-6 space-y-4 overflow-y-auto h-full">
      <div className="flex items-center justify-between">
        <h1 className="text-white font-bold text-lg">Reports</h1>
        <button onClick={generate} disabled={loading}
          className="px-4 py-2 bg-accent text-white rounded-lg text-sm hover:bg-blue-600 disabled:opacity-50">
          {loading ? 'Generating…' : 'Generate Now'}
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-2">
        {(['pnl', 'ml'] as const).map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors
              ${tab === t ? 'bg-accent text-white' : 'bg-surface border border-border text-muted hover:text-white'}`}>
            {t === 'pnl' ? 'P&L Report' : 'ML Retrospective'}
          </button>
        ))}
      </div>

      {tab === 'pnl' && pnl && !pnl.status && (
        <div className="space-y-4">
          {/* Summary */}
          <div className="bg-surface rounded-xl border border-border p-4 grid grid-cols-4 gap-4">
            {[
              { label: 'Total Trades', val: pnl.summary?.total_trades },
              { label: 'Win Rate', val: `${pnl.summary?.win_rate_pct}%` },
              { label: 'Net PnL',   val: `₹${pnl.summary?.total_pnl?.toFixed(2)}`, color: (pnl.summary?.total_pnl || 0) >= 0 ? 'text-up' : 'text-down' },
              { label: 'Profit Factor', val: pnl.summary?.profit_factor },
            ].map(s => (
              <div key={s.label} className="text-center">
                <div className="text-muted text-xs uppercase mb-1">{s.label}</div>
                <div className={`text-white font-bold text-lg ${s.color || ''}`}>{s.val}</div>
              </div>
            ))}
          </div>
          {/* Trade list */}
          <div className="bg-surface rounded-xl border border-border p-4 space-y-3">
            <h3 className="text-white font-semibold text-sm">Trade Log</h3>
            {(pnl.trades || []).map((t: any) => (
              <div key={t.id} className="border border-border rounded-lg p-3 text-xs space-y-1">
                <div className="flex items-center justify-between">
                  <span className="text-white font-semibold">{t.symbol} {t.direction?.toUpperCase()}</span>
                  <span className={`font-semibold ${(t.pnl || 0) >= 0 ? 'text-up' : 'text-down'}`}>
                    {(t.pnl || 0) >= 0 ? '+' : ''}₹{(t.pnl || 0).toFixed(2)}
                  </span>
                </div>
                <p className="text-muted leading-relaxed">{t.trade_statement}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === 'ml' && ml && !ml.status && (
        <div className="space-y-4">
          {/* Summary */}
          <div className="bg-surface rounded-xl border border-border p-4">
            <h3 className="text-white font-semibold text-sm mb-2">Analysis Summary</h3>
            <p className="text-muted text-xs leading-relaxed">{ml.summary}</p>
          </div>
          {/* Patterns */}
          <div className="bg-surface rounded-xl border border-border p-4">
            <h3 className="text-white font-semibold text-sm mb-3">Loss Patterns</h3>
            <div className="space-y-2">
              {Object.entries(ml.patterns || {}).map(([k, v]: [string, any]) => (
                <div key={k} className="flex items-center gap-3">
                  <div className="w-32 text-[10px] text-muted truncate uppercase">{k.replace(/_/g,' ')}</div>
                  <div className="flex-1 bg-border rounded-full h-2">
                    <div className="bg-down h-2 rounded-full" style={{ width: `${v.pct_of_total}%` }} />
                  </div>
                  <div className="text-xs text-down w-12 text-right">{v.pct_of_total}%</div>
                  <div className="text-xs text-muted w-20 text-right">avg ₹{v.avg_loss}</div>
                </div>
              ))}
            </div>
          </div>
          {/* Recommendations */}
          <div className="bg-surface rounded-xl border border-border p-4 space-y-3">
            <h3 className="text-white font-semibold text-sm">Recommendations</h3>
            {(ml.recommendations || []).map((r: any, i: number) => (
              <div key={i} className="border border-accent/30 rounded-lg p-3 text-xs space-y-1 bg-accent/5">
                <div className="text-accent font-semibold">{r.pattern?.replace(/_/g,' ')}</div>
                <p className="text-muted">{r.finding}</p>
                <p className="text-white">{r.action}</p>
                {r.current !== undefined && (
                  <div className="flex gap-4 mt-1">
                    <span className="text-muted">Current: <span className="text-white">{String(r.current)}</span></span>
                    <span className="text-muted">Suggested: <span className="text-up">{String(r.suggested)}</span></span>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {(tab === 'pnl' && (!pnl || pnl.status)) && (
        <div className="bg-surface rounded-xl border border-border p-8 text-center">
          <p className="text-muted text-sm">No report available for today yet. Click Generate Now.</p>
        </div>
      )}
    </div>
  );
}
