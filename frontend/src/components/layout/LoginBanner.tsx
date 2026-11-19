import { useEffect, useState } from 'react';
import { CircleNotch, LockKey, ArrowSquareOut } from '@phosphor-icons/react';

import { API } from '../../lib/api';

interface BrokerStatus {
  broker: string; has_token: boolean; user: string | null;
  token_valid_today: boolean; live_ok: boolean;
}

export default function LoginBanner() {
  const [st, setSt]     = useState<BrokerStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [rt, setRt]     = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr]   = useState('');

  const load = async () => {
    try {
      const data = await (await fetch(`${API}/api/broker/status`)).json();
      setSt(data);
    } catch {
      setSt(null);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); const id = setInterval(load, 5000); return () => clearInterval(id); }, []);

  // Only hide the banner once the token is confirmed valid today AND Zerodha
  // itself still accepts it live (live_ok) -- token_valid_today alone just
  // means our cached file happens to be dated today, which stays true even
  // after Zerodha force-invalidates the token mid-session. Without live_ok
  // here, a dead token hides the only way back into login.
  if (loading) return null;
  if (st?.token_valid_today && st?.has_token && st?.live_ok) return null;

  const openLogin = async () => {
    const r = await (await fetch(`${API}/api/broker/login-url`)).json();
    window.open(r.login_url, '_blank');
  };

  const submit = async () => {
    if (!rt.trim()) return;
    setBusy(true); setErr('');
    try {
      const res = await fetch(`${API}/api/broker/login`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request_token: rt.trim() }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'login failed');
      setRt(''); await load();
    } catch (e: any) { setErr(String(e.message || e)); }
    setBusy(false);
  };

  // Backend not reachable yet
  if (!st) return (
    <div className="flex items-center gap-2 px-5 py-2.5 bg-spvh-100/70 border-b border-spvh-300/70 text-sm text-ink2">
      <CircleNotch size={16} className="animate-spin text-accent" />
      Waiting for the trading server on port 8000
    </div>
  );

  return (
    <div className="px-5 py-3 bg-white/80 border-b border-down/25" style={{ backdropFilter: 'blur(16px)' }}>
      <div className="flex items-center gap-3 flex-wrap">
        <span className="flex items-center gap-2 text-down font-bold text-sm">
          <LockKey size={18} weight="fill" /> Sign in to Zerodha for today
        </span>
        <button onClick={openLogin}
          className="pressable btn-primary inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-control text-sm font-bold">
          Open Zerodha login <ArrowSquareOut size={15} weight="bold" />
        </button>
        <span className="text-muted text-sm">then paste the request_token</span>
        <input
          value={rt} onChange={e => setRt(e.target.value)}
          placeholder="request_token"
          aria-label="Zerodha request token"
          className="bg-white border border-border text-ink text-sm rounded-control px-3 py-1.5 w-64
            focus:border-spvh-500 focus:outline-none focus:ring-2 focus:ring-spvh-400/40"
        />
        <button onClick={submit} disabled={busy}
          className="pressable px-3.5 py-1.5 bg-ink text-white rounded-control text-sm font-bold disabled:opacity-50">
          {busy ? 'Signing in' : 'Activate'}
        </button>
        {err && <span className="text-down text-sm">{err}</span>}
      </div>
      <p className="text-muted text-xs mt-1.5">
        Until you sign in, the live feed and trading are paused. The session lasts until about 6 AM tomorrow.
      </p>
    </div>
  );
}
