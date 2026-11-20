import { useState } from 'react';
import { WifiHigh, WifiSlash, WarningOctagon } from '@phosphor-icons/react';
import { useMarketStore } from '../../store/marketStore';
import GoLiveDialog from './GoLiveDialog';
import ChecksPill from './ChecksPill';
import { useLiveStatus } from './useLiveStatus';
import { useDesk } from '../../hooks/useDesk';

function StrategySwitch({ tag, label, live, onClick, disabled }: {
  tag: 'ES' | 'OB'; label: string; live: boolean; onClick: () => void; disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={disabled ? 'Unavailable while the office PC is offline'
        : live ? `${label} is placing real orders. Click to move back to paper.` : `${label} is on paper. Click to go live.`}
      className={`pressable disabled:opacity-60 disabled:cursor-not-allowed flex items-center gap-2.5 h-10 pl-3 pr-1 rounded-full border
        ${live ? 'bg-white border-spvh-400 shadow-primary' : 'bg-white/70 border-border hover:border-spvh-300'}`}
    >
      <span className={`text-sm font-bold ${tag === 'ES' ? 'text-es' : 'text-teal'}`}>{tag}</span>
      <span className="text-xs text-muted hidden xl:inline">{label}</span>
      <span
        className={`flex items-center gap-1.5 h-8 px-3 rounded-full text-xs font-bold tracking-wide
          ${live ? 'btn-primary' : 'bg-bg text-ink2'}`}
      >
        {live && <span className="live-dot w-2 h-2 rounded-full bg-down" />}
        {live ? 'LIVE' : 'PAPER'}
      </span>
    </button>
  );
}

export default function Header() {
  const { settings, feedHealth, haltStatus } = useMarketStore();
  const { status, refresh } = useLiveStatus();
  const [dialog, setDialog] = useState<'ES' | 'OB' | null>(null);

  const d = useDesk();
  // Team site with the office PC offline: the last saved feed state must not read as live
  const connected = feedHealth.connected && d.online;
  const lastTick = feedHealth.last_tick ? new Date(feedHealth.last_tick).toLocaleTimeString('en-IN', { hour12: false }) : null;
  const savedAt = d.savedAt ? new Date(d.savedAt).toLocaleString('en-IN', { hour12: false, day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : null;

  return (
    <header className="glass relative z-30 h-16 shrink-0 flex items-center gap-4 px-5 border-b border-white/60">
      <img src={`${import.meta.env.BASE_URL}spvh-wordmark.png`} alt="SPVH Asset and Investment Management" className="h-8 w-auto select-none" draggable={false} />
      <div className="w-px h-8 bg-border" />
      <div className="min-w-0">
        <div className="text-[15px] font-bold text-ink leading-tight">Options Desk</div>
        <div className="text-xs text-muted leading-tight">NSE F&amp;O · ~211 option stocks</div>
      </div>

      <div
        className={`ml-2 flex items-center gap-2 h-8 px-3 rounded-full text-xs font-semibold
          ${connected ? 'bg-up/10 text-up' : 'bg-down/10 text-down'}`}
        title={connected ? 'Live price feed connected' : d.online ? 'Live price feed disconnected' : 'Office PC offline: showing the last saved data'}
      >
        {connected ? <WifiHigh size={15} weight="bold" /> : <WifiSlash size={15} weight="bold" />}
        {connected ? 'Feed live' : 'Feed disconnected'}
        {connected && lastTick && <span className="font-normal text-muted">· {lastTick}</span>}
        {!d.online && savedAt && <span className="font-normal text-muted">· data as of {savedAt}</span>}
      </div>

      <ChecksPill />

      {haltStatus.halted && (
        <div className="flex items-center gap-1.5 h-8 px-3 rounded-full bg-down text-white text-xs font-bold min-w-0">
          <WarningOctagon size={15} weight="fill" className="shrink-0" />
          <span className="truncate">Trading halted: {haltStatus.reason}</span>
        </div>
      )}

      <div className="flex-1" />

      <StrategySwitch tag="ES" label="Early Scalp" live={settings.es_live} disabled={!d.online} onClick={() => setDialog('ES')} />
      <StrategySwitch tag="OB" label="Opening Breakout" live={settings.ob_live} disabled={!d.online} onClick={() => setDialog('OB')} />

      {dialog && (
        <GoLiveDialog
          tag={dialog}
          status={status}
          funds={settings.available_funds || 0}
          onClose={() => setDialog(null)}
          onDone={refresh}
        />
      )}
    </header>
  );
}
