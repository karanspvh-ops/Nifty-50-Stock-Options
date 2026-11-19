import { useEffect } from 'react';
import { WarningOctagon, Warning, X } from '@phosphor-icons/react';
import { useMarketStore } from '../../store/marketStore';

import { API } from '../../lib/api';

/** Live-trading alerts: an unsold position, a missing stop, a broker mismatch. Never hidden by default. */
export default function AlertsBanner() {
  const alerts = useMarketStore(s => s.alerts);
  const setAlerts = useMarketStore(s => s.setAlerts);

  useEffect(() => {
    const load = () => fetch(`${API}/api/live/alerts`).then(r => (r.ok ? r.json() : null))
      .then(d => { if (d) setAlerts(d.alerts); }).catch(() => {});
    load();
    const id = setInterval(load, 10_000);     // the socket pushes changes; this only catches up after a reconnect
    return () => clearInterval(id);
  }, [setAlerts]);

  if (!alerts.length) return null;

  const dismiss = (key: string) => {
    setAlerts(alerts.filter(a => a.key !== key));
    fetch(`${API}/api/live/alerts/${encodeURIComponent(key)}/dismiss`, { method: 'POST' }).catch(() => {});
  };

  return (
    <div role="alert" className="shrink-0 border-b border-down/30">
      {alerts.map(a => {
        const crit = a.level === 'critical';
        return (
          <div key={a.key}
               className={`flex items-start gap-3 px-5 py-2.5 text-sm ${crit ? 'bg-down text-white' : 'bg-spvh-100 text-ink'}`}>
            {crit
              ? <WarningOctagon size={20} weight="fill" className="shrink-0 mt-px" />
              : <Warning size={20} weight="fill" className="shrink-0 mt-px text-warn" />}
            <div className="flex-1 min-w-0">
              <span className="font-semibold">{a.message}</span>
              <span className={`ml-2 text-xs ${crit ? 'text-white/80' : 'text-muted'}`}>
                {new Date(a.last_at).toLocaleTimeString('en-IN', { hour12: false })}{a.count > 1 ? ` · ${a.count} times` : ''}
              </span>
            </div>
            <button onClick={() => dismiss(a.key)} aria-label="Dismiss alert"
              className={`pressable p-1 rounded-md ${crit ? 'hover:bg-white/15' : 'hover:bg-ink/5'}`}>
              <X size={16} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
