import { useEffect, useRef, useState } from 'react';
import { Wallet } from '@phosphor-icons/react';
import { useMarketStore } from '../../store/marketStore';

import { API } from '../../lib/api';

const rs = (v: number | undefined) =>
  v == null ? '—' : `${v < 0 ? '−' : ''}₹${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
const signed = (v: number | undefined) =>
  v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}₹${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;

/** Zerodha balance, pushed by the backend every second from 9 to 11 AM (and while a live
 *  trade is open), instantly on any order event, and once at sign-in. */
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
  // Outside the trading window the backend stops polling on purpose: the figure
  // is the last one fetched, not a stale feed, so it isn't shown as a fault.
  const idle = account?.ok && account.watching === false;
  const asOf = account?.fetched_at
    ? new Date(account.fetched_at).toLocaleTimeString('en-IN', { hour12: false, hour: '2-digit', minute: '2-digit' })
    : null;
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
        <div className="basis-full lg:basis-0 flex-1 min-w-0 px-4 md:px-5 py-3.5 md:py-4">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm font-semibold text-ink2">
            <Wallet size={18} weight="duotone" className="text-accent" />
            Available to trade
            <span className="ml-1 inline-flex items-center gap-1.5 text-xs font-normal text-muted">
              <span className={`w-1.5 h-1.5 rounded-full ${idle ? 'bg-faint' : fresh ? 'bg-up live-dot' : 'bg-down'}`} />
              {account == null ? 'Waiting for Zerodha'
                : account.ok === false ? 'Zerodha not answering'
                : idle ? `As of ${asOf ?? '—'} · live 9–11 AM and on orders`
                : `Updated ${age == null ? '' : age < 1 ? 'just now' : `${age.toFixed(0)}s ago`}`}
            </span>
          </div>
          <div className={`mt-1 text-[34px] leading-[40px] md:text-[40px] md:leading-[46px] font-bold tabular-nums ${(bal ?? 0) < 0 ? 'text-down' : 'text-ink'}`}>
            {rs(bal)}
          </div>
          {account?.ok === false && account.error && <div className="mt-1 text-xs text-down">{account.error}</div>}
        </div>

        <div className="basis-full lg:basis-0 flex-[2] min-w-0 grid grid-cols-2 sm:grid-cols-5 border-t lg:border-t-0 lg:border-l border-border">
          {stats.map((s, i) => (
            <div key={s.l} className={`min-w-0 px-4 py-3 md:py-4 sm:border-r border-border/70 last:border-r-0
              ${i === stats.length - 1 ? 'col-span-2 sm:col-span-1' : ''} ${i >= 2 ? 'border-t sm:border-t-0' : ''}`}>
              <div className="text-xs font-semibold text-muted truncate">{s.l}</div>
              <div className={`mt-0.5 md:mt-1 text-base md:text-lg font-bold tabular-nums truncate ${s.c ?? 'text-ink'}`}>{s.v}</div>
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
  // Lakhs for real capital; small or negative balances in plain rupees (−₹295, not ₹-0L)
  const lakh = (v: number) => Math.abs(v) >= 100_000
    ? `₹${(v / 100_000).toLocaleString('en-IN', { maximumFractionDigits: 2 })}L`
    : `${v < 0 ? '−' : ''}₹${Math.abs(Math.round(v)).toLocaleString('en-IN')}`;
  const btn = 'pressable shrink-0 w-10 h-10 md:w-7 md:h-7 text-lg md:text-base rounded-full border border-border bg-white text-ink2 hover:border-spvh-400 font-bold leading-none';

  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 px-4 md:px-5 py-3 border-t border-border bg-bg/50">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 w-full sm:w-auto">
        <span className="basis-full sm:basis-auto text-sm font-semibold text-ink2">Capital limit</span>
        <button className={btn} onClick={() => nudge(-STEP)} aria-label="Lower capital limit by ₹1 lakh">−</button>
        <input
          type="range" min={STEP} max={max} step={STEP} value={draft}
          aria-label="Capital limit"
          onChange={e => { dragging.current = true; setDraft(Number(e.target.value)); }}
          onPointerUp={e => save(Number((e.target as HTMLInputElement).value))}
          onKeyUp={e => save(Number((e.target as HTMLInputElement).value))}
          className="flex-1 min-w-0 sm:flex-none sm:w-48 h-10 md:h-auto accent-[rgb(var(--spvh-500))]"
        />
        <button className={btn} onClick={() => nudge(STEP)} aria-label="Raise capital limit by ₹1 lakh">+</button>
        <span className="text-base font-bold text-ink tabular-nums w-[4.5rem] sm:w-20 text-right sm:text-left">{lakh(draft)}</span>
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
