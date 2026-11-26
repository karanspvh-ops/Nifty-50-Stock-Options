import { useEffect, useRef, Component, type ReactNode } from 'react';
import { useMarketSocket } from './hooks/useMarketSocket';
import { useTradesSocket } from './hooks/useTradesSocket';
import { useMarketStore } from './store/marketStore';
import Sidebar      from './components/layout/Sidebar';
import Header       from './components/layout/Header';
import LoginBanner  from './components/layout/LoginBanner';
import AlertsBanner from './components/layout/AlertsBanner';
import SectorHeatmap    from './components/dashboard/SectorHeatmap';
import SectorBarChart   from './components/dashboard/SectorBarChart';
import OpenTradesPanel  from './components/dashboard/OpenTradesPanel';
import StockRanking     from './components/dashboard/StockRanking';
import TradableSignals  from './components/dashboard/TradableSignals';
import TradePlan        from './components/dashboard/TradePlan';
import AccountPanel     from './components/dashboard/AccountPanel';
import TradeTable       from './components/trading/TradeTable';
import ReportsView      from './components/reports/ReportsView';
import NiftyDataView    from './components/niftydata/NiftyDataView';
import EarlyScalpView  from './components/scalp/EarlyScalpView';

class ErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state = { error: null };
  static getDerivedStateFromError(e: Error) { return { error: e }; }
  render() {
    if (this.state.error) {
      return (
        <div className="p-8 text-down">
          <div className="font-bold mb-2">Render error</div>
          <pre className="text-xs text-muted whitespace-pre-wrap">
            {(this.state.error as Error).message}
            {'\n\n'}
            {(this.state.error as Error).stack}
          </pre>
        </div>
      );
    }
    return this.props.children;
  }
}

import { API } from './lib/api';

function PageTitle({ title, note, live }: { title: string; note: string; live?: boolean }) {
  return (
    <div className="flex flex-wrap items-end gap-x-3 gap-y-1.5 mb-3 md:mb-4">
      <h1 className="text-xl md:text-2xl font-bold text-ink leading-none">{title}</h1>
      <span className={`inline-flex items-center gap-1.5 h-6 px-2.5 rounded-full text-[12.5px] font-bold tracking-wide
        ${live ? 'btn-primary' : 'bg-bg text-ink2 border border-border'}`}>
        {live && <span className="live-dot w-1.5 h-1.5 rounded-full bg-down" />}
        {live ? 'LIVE' : 'PAPER'}
      </span>
      <span className="basis-full sm:basis-auto text-sm text-muted pb-px">{note}</span>
    </div>
  );
}

function Dashboard() {
  return (
    <div className="page"><div className="page-inner space-y-3 md:space-y-4">
      <AccountPanel />
      <OpenTradesPanel />
      <TradePlan />
      <SectorHeatmap />
      <SectorBarChart />
      <StockRanking />
      <TradableSignals />
    </div></div>
  );
}

export default function App() {
  useMarketSocket();
  useTradesSocket();
  const { activeView, setSettings } = useMarketStore();

  // The header floats over the page; pages pad themselves by its live height
  // (it grows when alerts or the Zerodha sign-in banner appear).
  const chrome = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = chrome.current; if (!el) return;
    const set = () => document.documentElement.style.setProperty('--chrome-h', `${Math.ceil(el.getBoundingClientRect().height)}px`);
    const ro = new ResizeObserver(set);
    ro.observe(el); set();
    return () => ro.disconnect();
  }, []);

  // Load settings on startup — retry every 2s until the backend responds
  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetch(`${API}/api/settings/`)
        .then(r => r.ok ? r.json() : Promise.reject(r.status))
        .then(data => { if (!cancelled) setSettings(data); })
        .catch(() => { if (!cancelled) setTimeout(load, 2000); });
    };
    load();
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="flex h-full w-full overflow-hidden text-ink">
      <Sidebar />
      <div className="relative flex-1 min-w-0 h-full">
        {/* Floating chrome: content scrolls underneath and shows through the glass */}
        <div ref={chrome} className="absolute inset-x-0 top-0 z-30 pointer-events-none [&>*]:pointer-events-auto md:px-3 md:pt-3">
          <Header />
          <div className="mt-0 md:mt-2 md:rounded-[18px] md:overflow-hidden empty:hidden shadow-[0_10px_30px_-14px_rgb(30_50_90/0.3)]">
            <AlertsBanner />
            <LoginBanner />
          </div>
        </div>
        <main className="h-full overflow-hidden">
          {activeView === 'dashboard'     && <ErrorBoundary><Dashboard /></ErrorBoundary>}
          {activeView === 'early_scalp'   && <ErrorBoundary><EarlyScalpView /></ErrorBoundary>}
          {activeView === 'testing_lab'   && (
            <div className="page"><div className="page-inner">
              <PageTitle title="Paper trades" note="Every simulated trade from ES and OB" />
              <TradeTable env="paper" />
            </div></div>
          )}
          {activeView === 'battle_ground' && (
            <div className="page"><div className="page-inner">
              <PageTitle title="Live trades" note="Real orders placed on Zerodha" live />
              <TradeTable env="live" />
            </div></div>
          )}
          {activeView === 'reports'       && <ReportsView />}
          {activeView === 'nifty_data'    && <NiftyDataView />}
        </main>
      </div>
    </div>
  );
}
