import { useCallback, useEffect, useState } from 'react';
import { useMarketStore } from '../../store/marketStore';

import { API } from '../../lib/api';

export interface LiveCheck { key: string; label: string; ok: boolean; detail: string }
export interface LiveStatus {
  strategies: Record<'ES' | 'OB', { live: boolean; open: { paper: number; live: number } }>;
  readiness: LiveCheck[];
}

/** Polls /api/live/status and mirrors the ES/OB live flags into the store. */
export function useLiveStatus(pollMs = 5000) {
  const setSettings = useMarketStore(s => s.setSettings);
  const [status, setStatus] = useState<LiveStatus | null>(null);

  const refresh = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/live/status`);
      if (!r.ok) return;
      const d: LiveStatus = await r.json();
      setStatus(d);
      setSettings({ es_live: d.strategies.ES.live, ob_live: d.strategies.OB.live });
    } catch { /* backend not up yet */ }
  }, [setSettings]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, pollMs);
    return () => clearInterval(id);
  }, [refresh, pollMs]);

  return { status, refresh };
}
