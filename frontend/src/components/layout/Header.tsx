import { useState } from 'react';
import { useMarketStore } from '../../store/marketStore';

const API = 'http://localhost:8000';

export default function Header() {
  const { settings, setSettings, feedHealth, haltStatus } = useMarketStore();
  const [saving, setSaving] = useState(false);

  const patch = async (key: string, value: unknown) => {
    setSettings({ [key]: value } as any);
    setSaving(true);
    await fetch(`${API}/api/settings`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [key]: value }),
    });
    setSaving(false);
  };

  const toggleLive = () => patch('is_live', !settings.is_live);

  return (
    <header className="h-14 flex items-center gap-3 px-4 bg-surface border-b border-border shrink-0">

      {/* Feed indicator */}
      <div className={`w-2 h-2 rounded-full ${feedHealth.connected ? 'bg-up' : 'bg-down'} animate-pulse`} />

      {/* Index selector */}
      <select
        value={settings.active_index}
        onChange={e => patch('active_index', e.target.value)}
        className="bg-bg border border-border text-xs text-white rounded px-2 py-1"
      >
        <option value="NIFTY50">NIFTY 50</option>
        <option value="NIFTY100">NIFTY 100</option>
        <option value="NIFTY200">NIFTY 200</option>
      </select>

      <div className="flex-1" />

      {/* Settings inputs */}
      {[
        { label: 'Funds',        key: 'available_funds',   type: 'number', step: 1000 },
        { label: 'Target %',     key: 'target_profit_pct', type: 'number', step: 1    },
        { label: 'Trade SL %',   key: 'trade_sl_pct',      type: 'number', step: 0.5  },
        { label: 'Portfolio SL %',key:'portfolio_sl_pct',  type: 'number', step: 1    },
      ].map(f => (
        <div key={f.key} className="flex flex-col items-center gap-0.5">
          <span className="text-muted text-[10px] uppercase tracking-wider">{f.label}</span>
          <input
            type={f.type}
            step={f.step}
            value={(settings as any)[f.key]}
            onChange={e => patch(f.key, parseFloat(e.target.value))}
            className="w-20 bg-bg border border-border text-white text-xs rounded px-2 py-1 text-center"
          />
        </div>
      ))}

      {/* Dynamic SL toggle */}
      <div className="flex flex-col items-center gap-0.5">
        <span className="text-muted text-[10px] uppercase tracking-wider">Dyn SL</span>
        <button
          onClick={() => patch('dynamic_sl_enabled', !settings.dynamic_sl_enabled)}
          className={`w-10 h-5 rounded-full transition-colors relative ${settings.dynamic_sl_enabled ? 'bg-accent' : 'bg-border'}`}
        >
          <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${settings.dynamic_sl_enabled ? 'left-5' : 'left-0.5'}`} />
        </button>
      </div>

      {/* Halt banner */}
      {haltStatus.halted && (
        <div className="px-2 py-1 bg-down/20 border border-down text-down text-xs rounded">
          HALTED: {haltStatus.reason.slice(0, 30)}
        </div>
      )}

      {/* LIVE toggle */}
      <button
        onClick={toggleLive}
        className={`flex items-center gap-2 px-4 py-1.5 rounded-lg font-semibold text-sm transition-all
          ${settings.is_live
            ? 'bg-down text-white shadow-[0_0_12px_rgba(239,68,68,0.5)]'
            : 'bg-surface border border-border text-muted hover:border-accent'}`}
      >
        <span className={`w-2 h-2 rounded-full ${settings.is_live ? 'bg-white animate-pulse' : 'bg-muted'}`} />
        {settings.is_live ? 'LIVE' : 'PAPER'}
      </button>

      {saving && <span className="text-muted text-xs">saving…</span>}
    </header>
  );
}
