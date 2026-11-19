import { useEffect, Component, type ReactNode } from 'react';
import { useMarketSocket } from './hooks/useMarketSocket';
import { useTradesSocket } from './hooks/useTradesSocket';
import { useMarketStore } from './store/marketStore';
import Sidebar      from './components/layout/Sidebar';
import Header       from './components/layout/Header';
import LoginBanner  from './components/layout/LoginBanner';
import SectorHeatmap    from './components/dashboard/SectorHeatmap';
import SectorBarChart   from './components/dashboard/SectorBarChart';
import OpenTradesPanel  from './components/dashboard/OpenTradesPanel';
import StockRanking     from './components/dashboard/StockRanking';
import TradableSignals  from './components/dashboard/TradableSignals';
import TradePlan        from './components/dashboard/TradePlan';
import SystemCheckPanel      from './components/dashboard/SystemCheckPanel';
import StrategyDryRunPanel   from './components/dashboard/StrategyDryRunPanel';
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

const API = 'http://localhost:8000';

function PageTitle({ title, note, live }: { title: string; note: string; live?: boolean }) {
  return (
    <div className="flex items-end gap-3 mb-4">
      <h1 className="text-2xl font-bold text-ink leading-none">{title}</h1>
      <span className={`inline-flex items-center gap-1.5 h-6 px-2.5 rounded-full text-[12.5px] font-bold tracking-wide
        ${live ? 'btn-gold' : 'bg-bg text-ink2 border border-border'}`}>
        {live && <span className="live-dot w-1.5 h-1.5 rounded-full bg-down" />}
        {live ? 'LIVE' : 'PAPER'}
      </span>
      <span className="text-sm text-muted pb-px">{note}</span>
    </div>
  );
}

function Dashboard() {
  return (
    <div className="px-6 py-5 space-y-4 overflow-y-auto h-full">
      <SystemCheckPanel />
      <StrategyDryRunPanel />
      <OpenTradesPanel />
      <TradePlan />
      <SectorHeatmap />
      <SectorBarChart />
      <TradableSignals />
      <StockRanking />
    </div>
  );
}

export default function App() {
  useMarketSocket();
  useTradesSocket();
  const { activeView, setSettings } = useMarketStore();

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
      <div className="flex flex-col flex-1 min-w-0">
        <Header />
        <LoginBanner />
        <main className="flex-1 overflow-hidden">
          {activeView === 'dashboard'     && <ErrorBoundary><Dashboard /></ErrorBoundary>}
          {activeView === 'early_scalp'   && <ErrorBoundary><EarlyScalpView /></ErrorBoundary>}
          {activeView === 'testing_lab'   && (
            <div className="px-6 py-5 overflow-y-auto h-full">
              <PageTitle title="Paper trades" note="Every simulated trade from ES and OB" />
              <TradeTable env="paper" />
            </div>
          )}
          {activeView === 'battle_ground' && (
            <div className="px-6 py-5 overflow-y-auto h-full">
              <PageTitle title="Live trades" note="Real orders placed on Zerodha" live />
              <TradeTable env="live" />
            </div>
          )}
          {activeView === 'reports'       && <ReportsView />}
          {activeView === 'nifty_data'    && <NiftyDataView />}
        </main>
      </div>
    </div>
  );
}
