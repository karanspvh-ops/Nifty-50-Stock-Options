import { useEffect, useState } from 'react';

const API = 'http://localhost:8000';

interface BrokerStatus {
  broker: string; has_token: boolean; user: string | null;
  token_valid_today: boolean;
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

  // Only hide banner when we've confirmed the token is valid today AND loaded in memory
  if (loading) return null;
  if (st?.token_valid_today && st?.has_token) return null;

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
    <div className="bg-yellow-500/10 border-b border-yellow-500/30 px-4 py-2">
      <span className="text-yellow-400 text-xs font-medium">
        ⏳ Backend not reachable — waiting for server on port 8000…
      </span>
    </div>
  );

  return (
    <div className="bg-down/10 border-b border-down/40 px-4 py-3">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-down font-semibold text-sm">🔐 Zerodha login required for today</span>
        <button onClick={openLogin}
          className="px-3 py-1.5 bg-accent text-white rounded-lg text-xs font-semibold hover:bg-blue-600">
          1 · Open Zerodha login
        </button>
        <span className="text-muted text-xs">2 · paste the request_token from the callback page →</span>
        <input
          value={rt} onChange={e => setRt(e.target.value)}
          placeholder="request_token"
          className="bg-bg border border-border text-white text-xs rounded px-2 py-1.5 w-64"
        />
        <button onClick={submit} disabled={busy}
          className="px-3 py-1.5 bg-up text-white rounded-lg text-xs font-semibold hover:opacity-90 disabled:opacity-50">
          {busy ? 'Logging in…' : '3 · Activate'}
        </button>
        {err && <span className="text-down text-xs">{err}</span>}
      </div>
      <p className="text-muted text-[10px] mt-1">
        Until you log in, the live feed and trading are paused. The token lasts until ~6 AM tomorrow.
      </p>
    </div>
  );
}
