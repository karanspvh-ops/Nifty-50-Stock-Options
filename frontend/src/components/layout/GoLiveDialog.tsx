import { useEffect, useRef, useState } from 'react';
import { CheckCircle, XCircle, Warning, X, CircleNotch } from '@phosphor-icons/react';
import type { LiveStatus } from './useLiveStatus';

const API = 'http://localhost:8000';

const NAMES: Record<string, string> = { ES: 'Early Scalp', OB: 'Opening Breakout' };

export default function GoLiveDialog({
  tag, status, funds, onClose, onDone,
}: {
  tag: 'ES' | 'OB';
  status: LiveStatus | null;
  funds: number;
  onClose: () => void;
  onDone: () => void;
}) {
  const live    = status?.strategies[tag]?.live ?? false;
  const open    = status?.strategies[tag]?.open ?? { paper: 0, live: 0 };
  const phrase  = `GO LIVE ${tag}`;
  const [typed, setTyped] = useState('');
  const [busy, setBusy]   = useState(false);
  const [err, setErr]     = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const blocking = (status?.readiness ?? []).filter(c => !c.ok && ['broker', 'feed', 'halt', 'banlist'].includes(c.key));
  const hasOpen  = open.paper + open.live > 0;
  const canGo    = !!status && !live && typed.trim().toUpperCase() === phrase
                   && blocking.length === 0 && !hasOpen && !busy;
  const canStop  = live && !hasOpen && !busy;

  const submit = async (enabled: boolean) => {
    setBusy(true); setErr('');
    try {
      const r = await fetch(`${API}/api/live/${tag}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled, confirm: typed }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'Switch failed');
      onDone();
      onClose();
    } catch (e: any) {
      setErr(String(e.message || e));
    }
    setBusy(false);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-ink/25"
      style={{ backdropFilter: 'blur(6px)' }}
      onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="golive-title"
    >
      <div className="glass-strong rise-in w-full max-w-[460px] rounded-panel overflow-hidden">
        <div className="flex items-start gap-3 px-6 pt-5 pb-4">
          <div className="flex-1">
            <h2 id="golive-title" className="text-xl font-bold text-ink">
              {live ? `Move ${NAMES[tag]} back to paper` : `Take ${NAMES[tag]} live`}
            </h2>
            <p className="text-sm text-muted mt-1">
              {live
                ? 'New trades will be simulated again. Nothing is sent to Zerodha.'
                : 'New trades will place real orders on your Zerodha account.'}
            </p>
          </div>
          <button onClick={onClose} aria-label="Close" className="pressable p-1.5 -m-1 rounded-control text-muted hover:text-ink hover:bg-ink/5">
            <X size={18} />
          </button>
        </div>
        <div className="accent-rule" />

        <div className="px-6 py-4 space-y-4">
          {!live && (
            <ul className="space-y-2">
              {(status?.readiness ?? []).map(c => (
                <li key={c.key} className="flex items-start gap-2.5">
                  {c.ok
                    ? <CheckCircle size={18} weight="fill" className="text-up shrink-0 mt-px" />
                    : c.key === 'margin'
                      ? <Warning size={18} weight="fill" className="text-warn shrink-0 mt-px" />
                      : <XCircle size={18} weight="fill" className="text-down shrink-0 mt-px" />}
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-ink">{c.label}</div>
                    <div className="text-xs text-muted break-words">{c.detail}</div>
                  </div>
                </li>
              ))}
              {!status && (
                <li className="flex items-center gap-2 text-sm text-muted">
                  <CircleNotch size={16} className="animate-spin" /> Checking readiness
                </li>
              )}
            </ul>
          )}

          {!live && (
            <div className="rounded-control bg-spvh-50 border border-spvh-300/70 px-4 py-3 text-sm text-ink2 space-y-1">
              <p>Orders: intraday (MIS) option buys, marketable limit orders, real fills recorded.</p>
              <p>Sizing: smaller of your Zerodha funds and the <span className="font-semibold text-ink">₹{funds.toLocaleString('en-IN')}</span> capital limit, split equally across 5 trades, no refills.</p>
              <p>Exits sell at Zerodha first; a failed sell keeps the trade open and retries.</p>
            </div>
          )}

          {hasOpen && (
            <p className="text-sm text-down font-semibold">
              {tag} has {open.paper + open.live} open trade{open.paper + open.live > 1 ? 's' : ''}. Switch after they close.
            </p>
          )}

          {!live && (
            <label className="block">
              <span className="text-sm text-ink2">
                Type <span className="font-bold text-ink tracking-wide">{phrase}</span> to confirm
              </span>
              <input
                ref={inputRef}
                value={typed}
                onChange={e => setTyped(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && canGo) submit(true); }}
                className="mt-1.5 w-full rounded-control bg-white border border-border px-3 py-2 text-base text-ink
                  focus:border-spvh-500 focus:outline-none focus:ring-2 focus:ring-spvh-400/40"
                autoComplete="off"
                spellCheck={false}
              />
            </label>
          )}

          {err && <p className="text-sm text-down">{err}</p>}
        </div>

        <div className="flex justify-end gap-2 px-6 py-4 bg-bg/70 border-t border-border">
          <button onClick={onClose} className="pressable px-4 py-2 rounded-control text-sm font-semibold text-ink2 hover:bg-ink/5">
            Cancel
          </button>
          {live ? (
            <button
              onClick={() => submit(false)}
              disabled={!canStop}
              className="pressable px-4 py-2 rounded-control text-sm font-semibold bg-ink text-white disabled:opacity-40"
            >
              {busy ? 'Switching' : 'Switch to paper'}
            </button>
          ) : (
            <button
              onClick={() => submit(true)}
              disabled={!canGo}
              className="pressable btn-primary px-5 py-2 rounded-control text-sm font-bold disabled:opacity-40 disabled:saturate-50"
            >
              {busy ? 'Going live' : `Go live with ${tag}`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
