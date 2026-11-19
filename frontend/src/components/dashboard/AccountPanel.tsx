import { useEffect, useRef, useState } from 'react';
import { Wallet } from '@phosphor-icons/react';
import { useMarketStore } from '../../store/marketStore';

const API = 'http://localhost:8000';

const rs = (v: number | undefined) =>
  v == null ? '—' : `${v < 0 ? '−' : ''}₹${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
const signed = (v: number | undefined) =>
  v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}₹${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;

/** Zerodha balance, pushed by the backend on every refresh (2 s, and instantly on order events). */
export default function AccountPanel() {
  const account  = useMarketStore(s => s.account);
  const setAcct  = useMarketStore(s => s.setAccount);
  const setSettings = useMarketStore(s => s.setSettings);
  const settings = useMarketStore(s => s.settings);
  const [, tick] = useState(0);

  useEffect(() => {
    fetch(`${API}/api/live/account`).then(r => (r.ok ? r.json() : null)).then(d => { if (d) setAcct(d); }).catch(() => {});
    const id = setInterval(() => tick(n => n + 1), 250);   // keeps the "updated Xs ago" honest
    return () => clearInterval(id);
  }, [setAcct]);

  const age = account?.received_at ? (Date.now() - account.received_at) / 1000 : null;
  const fresh = age != null && age < 5 && account?.ok;
  const bal = account?.live_balance;

  const stats = [
    { l: 'Opening balance', v: rs(account?.opening_balance) },
    { l: 'Added today', v: rs(account?.intraday_payin) },
    { l: 'Margin used', v: rs(account?.used_margin) },
    { l: 'Realised P&L', v: signed(account?.m2m_realised), c: (account?.m2m_realised ?? 0) >= 0 ? 'text-up' : 'text-down' },
    { l: 'Open P&L', v: signed(account?.m2m_unrealised), c: (account?.m2m_unrealised ?? 0) >= 0 ? 'text-up' : 'text-down' },
  ];

  return (
    <section className="bg-surface rounded-panel border border-border overflow-hidden" aria-label="Zerodha account">
      <div className="flex flex-wrap items-stretch">
        <div className="flex-1 min-w-[260px] px-5 py-4">
          <div className="flex items-center gap-2 text-sm font-semibold text-ink2">
            <Wallet size={18} weight="duotone" className="text-accent" />
            Available to trade
            <span className="ml-1 inline-flex items-center gap-1.5 text-xs font-normal text-muted">
              <span className={`w-1.5 h-1.5 rounded-full ${fresh ? 'bg-up live-dot' : 'bg-down'}`} />
              {account == null ? 'Waiting for Zerodha'
                : account.ok === false ? 'Zerodha not answering'
                : `Updated ${age == null ? '' : age < 1 ? 'just now' : `${age.toFixed(0)}s ago`}`}
            </span>
          </div>
          <div className={`mt-1 text-[40px] leading-[46px] font-bold tabular-nums ${(bal ?? 0) < 0 ? 'text-down' : 'text-ink'}`}>
            {rs(bal)}
          </div>
          {account?.ok === false && account.error && <div className="mt-1 text-xs text-down">{account.error}</div>}
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-5 border-t md:border-t-0 md:border-l border-border flex-[2] min-w-[320px]">
          {stats.map(s => (
            <div key={s.l} className="px-4 py-4 border-r border-border/70 last:border-r-0">
              <div className="text-xs font-semibold text-muted">{s.l}</div>
              <div className={`mt-1 text-lg font-bold tabular-nums ${s.c ?? 'text-ink'}`}>{s.v}</div>
            </div>
          ))}
        </div>
      </div>
      <CapitalLimit
        limit={settings.available_funds || 500000}
        startFunds={account?.ok ? (account.opening_balance ?? 0) + (account.intraday_payin ?? 0) : null}
        live={settings.es_live || settings.ob_live}
        onSaved={v => setSettings({ available_funds: v })}
      />
    </section>
  );
}

const STEP = 100_000;

/** Capital limit control: trading capital = min(start-of-day funds, limit), split across 5 trades. */
function CapitalLimit({ limit, startFunds, live, onSaved }: {
  limit: number; startFunds: number | null; live: boolean; onSaved: (v: number) => void;
}) {
  const [draft, setDraft] = useState(limit);
  const [saving, setSaving] = useState(false);
  const dragging = useRef(false);
  useEffect(() => { if (!dragging.current) setDraft(limit); }, [limit]);

  const max = Math.max(5_000_000, Math.ceil(((startFunds ?? 0) * 1.2) / STEP) * STEP, draft);
  const save = async (v: number) => {
    dragging.current = false;
    if (v === limit) return;
    setSaving(true);
    try {
      const r = await fetch(`${API}/api/settings/`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ available_funds: v }),
      });
      if (r.ok) onSaved(v);
    } finally { setSaving(false); }
  };
  const nudge = (d: number) => { const v = Math.min(max, Math.max(STEP, draft + d)); setDraft(v); save(v); };

  const liveCapital = startFunds == null ? null : Math.max(0, Math.min(startFunds, draft));
  const lakh = (v: number) => `₹${(v / 100_000).toLocaleString('en-IN', { maximumFractionDigits: 2 })}L`;
  const btn = 'pressable w-7 h-7 rounded-full border border-border bg-white text-ink2 hover:border-gold-400 font-bold leading-none';

  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 px-5 py-3 border-t border-border bg-bg/50">
      <div className="flex items-center gap-3">
        <span className="text-sm font-semibold text-ink2">Capital limit</span>
        <button className={btn} onClick={() => nudge(-STEP)} aria-label="Lower capital limit by ₹1 lakh">−</button>
        <input
          type="range" min={STEP} max={max} step={STEP} value={draft}
          aria-label="Capital limit"
          onChange={e => { dragging.current = true; setDraft(Number(e.target.value)); }}
          onPointerUp={e => save(Number((e.target as HTMLInputElement).value))}
          onKeyUp={e => save(Number((e.target as HTMLInputElement).value))}
          className="w-48 accent-[rgb(var(--gold-600))]"
        />
        <button className={btn} onClick={() => nudge(STEP)} aria-label="Raise capital limit by ₹1 lakh">+</button>
        <span className="text-base font-bold text-ink tabular-nums w-20">{lakh(draft)}</span>
        {saving && <span className="text-xs text-muted">Saving</span>}
      </div>
      <div className="text-sm text-ink2 tabular-nums">
        {liveCapital == null ? (
          <>Per trade on paper: <span className="font-bold text-ink">{lakh(draft / 5)}</span></>
        ) : (
          <>
            Live trades use <span className="font-bold text-ink">{lakh(liveCapital)}</span>
            <span className="text-muted"> (smaller of start-of-day funds {lakh(startFunds ?? 0)} and the limit)</span>
            {' '}→ <span className={`font-bold ${liveCapital >= 5 * 1000 ? 'text-accent' : 'text-down'}`}>{lakh(liveCapital / 5)} per trade</span>
            {live && liveCapital <= 0 && <span className="ml-2 font-semibold text-down">No funds: live entries will be skipped</span>}
          </>
        )}
      </div>
    </div>
  );
}
