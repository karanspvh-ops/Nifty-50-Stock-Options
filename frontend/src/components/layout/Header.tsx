import { useState } from 'react';
import { useMarketStore } from '../../store/marketStore';

const API = 'http://localhost:8000';

export default function Header() {
  const { settings, setSettings, feedHealth } = useMarketStore();
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
    <header className="h-14 flex items-center gap-2 px-3 bg-surface border-b border-border shrink-0 overflow-hidden">

      {/* Feed indicator */}
      <div className={`w-2 h-2 rounded-full shrink-0 ${feedHealth.connected ? 'bg-up' : 'bg-down'} animate-pulse`} />

      {/* Combined universe — every NSE stock with listed options (NIFTY 200 + ~36 extras) */}
      <span className="bg-bg border border-border text-[11px] text-white rounded px-2 py-1 shrink-0 whitespace-nowrap">
        NSE F&O · all options-tradable (~211)
      </span>

      <div className="flex-1 min-w-2" />

      {/* LIVE toggle */}
      <button
        onClick={toggleLive}
        className={`flex items-center gap-2 px-3 py-1.5 rounded-lg font-semibold text-sm transition-all shrink-0
          ${settings.is_live
            ? 'bg-down text-white shadow-[0_0_12px_rgba(239,68,68,0.5)]'
            : 'bg-surface border border-border text-muted hover:border-accent'}`}
      >
        <span className={`w-2 h-2 rounded-full ${settings.is_live ? 'bg-white animate-pulse' : 'bg-muted'}`} />
        {settings.is_live ? 'LIVE' : 'PAPER'}
      </button>

      {saving && <span className="text-muted text-[10px] shrink-0">saving…</span>}
    </header>
  );
}
