import { useState } from 'react';
import { WifiHigh, WifiSlash, WarningOctagon } from '@phosphor-icons/react';
import { useMarketStore } from '../../store/marketStore';
import GoLiveDialog from './GoLiveDialog';
import ChecksPill from './ChecksPill';
import { useLiveStatus } from './useLiveStatus';
import { useDesk } from '../../hooks/useDesk';
import IstClock from './IstClock';

function StrategySwitch({ tag, label, short, live, onClick, disabled, className = '' }: {
  tag: 'ES' | 'OB'; label: string; short: string; live: boolean; onClick: () => void;
  disabled?: boolean; className?: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={`${label}: ${live ? 'live' : 'paper'}`}
      title={disabled ? 'Unavailable while the office PC is offline'
        : live ? `${label} is placing real orders. Click to move back to paper.` : `${label} is on paper. Click to go live.`}
      className={`pressable disabled:opacity-60 disabled:cursor-not-allowed flex items-center gap-2 h-11 md:h-10 pl-3 pr-1 rounded-full border
        ${live ? 'glass-pill border-spvh-400/70' : 'glass-pill border-transparent hover:border-spvh-300/70'} ${className}`}
    >
      <span className={`text-sm font-bold ${tag === 'ES' ? 'text-es' : 'text-teal'}`}>{tag}</span>
      <span className="text-xs text-muted truncate md:hidden">{short}</span>
      <span className="text-xs text-muted hidden xl:inline">{label}</span>
      <span
        className={`ml-auto md:ml-0 flex items-center gap-1.5 h-9 md:h-8 px-3 rounded-full text-xs font-bold tracking-wide shrink-0
          ${live ? 'btn-primary' : 'glass-track text-ink2'}`}
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
  const lastTick = feedHealth.last_tick ? new Date(feedHealth.last_tick).toLocaleTimeString('en-IN', { hour12: false, timeZone: 'Asia/Kolkata' }) : null;
  const savedAt = d.savedAt ? new Date(d.savedAt).toLocaleString('en-IN', { hour12: false, day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata' }) : null;
  const feedTitle = (connected ? 'Live price feed connected' : d.online ? 'Live price feed disconnected' : 'Office PC offline: showing the last saved data')
    + (lastTick ? ` · last price tick ${lastTick} IST` : '');

  const switches = (cls: string) => (
    <>
      <StrategySwitch tag="ES" label="Early Scalp" short="Early Scalp" live={settings.es_live} disabled={!d.online}
        onClick={() => setDialog('ES')} className={cls} />
      <StrategySwitch tag="OB" label="Opening Breakout" short="Breakout" live={settings.ob_live} disabled={!d.online}
        onClick={() => setDialog('OB')} className={cls} />
    </>
  );

  return (
    <header className="glass z-30 pt-[env(safe-area-inset-top)] md:pt-0 md:rounded-[22px]">
      <div className="flex items-center gap-3 md:gap-4 h-14 md:h-16 px-3 sm:px-4 md:px-5">
        {/* Brand: mark on phones (no rail there), full wordmark on desktop */}
        <img src={`${import.meta.env.BASE_URL}spvh-mark.png`} alt="SPVH" className="md:hidden w-8 h-8 object-contain select-none" draggable={false} />
        <img src={`${import.meta.env.BASE_URL}spvh-wordmark.png`} alt="SPVH Asset and Investment Management" className="hidden lg:block h-8 w-auto select-none" draggable={false} />
        <div className="hidden lg:block w-px h-8 bg-border" />

        <div className="min-w-0">
          <div className="text-[15px] font-bold text-ink leading-tight">Options Desk</div>
          {/* Phones: the feed state lives here, under the name */}
          <div className="md:hidden flex items-center gap-1.5 text-xs leading-tight min-w-0" title={feedTitle}>
            <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${connected ? 'bg-up live-dot' : 'bg-down'}`} />
            <span className={`font-semibold shrink-0 ${connected ? 'text-up' : 'text-down'}`}>
              {connected ? 'Feed live' : d.online ? 'Feed disconnected' : 'Office PC offline'}
            </span>
            <span className="text-muted shrink-0">·</span>
            <IstClock className="text-muted shrink-0" />
            {!d.online && savedAt && <span className="text-muted truncate">· data {savedAt}</span>}
          </div>
          <div className="hidden lg:block text-xs text-muted leading-tight">NSE F&amp;O · ~211 option stocks</div>
        </div>

        <div
          className={`hidden md:flex items-center gap-2 h-8 px-3 rounded-full text-xs font-semibold min-w-0
            ${connected ? 'bg-up/10 text-up' : 'bg-down/10 text-down'}`}
          title={feedTitle}
        >
          {connected ? <WifiHigh size={15} weight="bold" className="shrink-0" /> : <WifiSlash size={15} weight="bold" className="shrink-0" />}
          <span className="shrink-0">{connected ? 'Feed live' : 'Feed disconnected'}</span>
          <span className="font-normal text-muted shrink-0">·</span>
          <IstClock className="font-normal text-muted shrink-0" />
          {!d.online && savedAt && <span className="font-normal text-muted truncate">· data as of {savedAt}</span>}
        </div>

        <div className="ml-auto md:ml-0">
          <ChecksPill />
        </div>

        {haltStatus.halted && (
          <div className="hidden md:flex items-center gap-1.5 h-8 px-3 rounded-full bg-down text-white text-xs font-bold min-w-0">
            <WarningOctagon size={15} weight="fill" className="shrink-0" />
            <span className="truncate">Trading halted: {haltStatus.reason}</span>
          </div>
        )}

        <div className="hidden md:block flex-1" />
        <div className="hidden md:flex items-center gap-2 lg:gap-3">{switches('')}</div>
      </div>

      {/* Phones: live/paper state for both strategies, full width under the bar */}
      <div className="md:hidden grid grid-cols-2 gap-2 px-3 sm:px-4 pb-2.5">{switches('w-full min-w-0')}</div>
      {haltStatus.halted && (
        <div className="md:hidden flex items-center gap-2 px-3 py-2 bg-down text-white text-sm font-bold">
          <WarningOctagon size={16} weight="fill" className="shrink-0" />
          <span className="min-w-0">Trading halted: {haltStatus.reason}</span>
        </div>
      )}

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
