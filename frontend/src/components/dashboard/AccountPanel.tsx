import { useEffect, useState } from 'react';
import { Wallet, WarningCircle } from '@phosphor-icons/react';
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
  const capital = settings.available_funds || 0;
  const anyLive = settings.es_live || settings.ob_live;
  const short = bal != null && capital > 0 ? capital - bal : null;

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
          {capital > 0 && bal != null && (
            short != null && short > 0 ? (
              <div className={`mt-1 flex items-center gap-1.5 text-sm ${anyLive ? 'text-down font-semibold' : 'text-warn'}`}>
                <WarningCircle size={16} weight="fill" />
                ₹{short.toLocaleString('en-IN', { maximumFractionDigits: 0 })} short of the ₹{capital.toLocaleString('en-IN')} strategy capital
              </div>
            ) : (
              <div className="mt-1 text-sm text-up font-semibold">Covers the ₹{capital.toLocaleString('en-IN')} strategy capital</div>
            )
          )}
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
    </section>
  );
}
