import { useEffect } from 'react';
import { useMarketSocket } from './hooks/useMarketSocket';
import { useMarketStore } from './store/marketStore';
import Sidebar      from './components/layout/Sidebar';
import Header       from './components/layout/Header';
import SectorHeatmap    from './components/dashboard/SectorHeatmap';
import SectorBarChart   from './components/dashboard/SectorBarChart';
import OpenTradesPanel  from './components/dashboard/OpenTradesPanel';
import StockRanking     from './components/dashboard/StockRanking';
import TradableSignals  from './components/dashboard/TradableSignals';
import TradePlan        from './components/dashboard/TradePlan';
import TradeTable       from './components/trading/TradeTable';
import ReportsView      from './components/reports/ReportsView';

const API = 'http://localhost:8000';

function Dashboard() {
  return (
    <div className="p-6 space-y-4 overflow-y-auto h-full">
      <OpenTradesPanel />
      <TradePlan />
      <SectorHeatmap />
      <SectorBarChart />
      <TradableSignals />
      <StockRanking />
    </div>
  );
}

function EngineStatus() {
  const { feedHealth, haltStatus } = useMarketStore();
  return (
    <div className="flex items-center gap-2 px-4 py-1 bg-surface border-b border-border text-xs">
      <span className={`w-1.5 h-1.5 rounded-full ${feedHealth.connected ? 'bg-up' : 'bg-down'}`} />
      <span className="text-muted">Feed: {feedHealth.connected ? 'Connected' : 'Disconnected'}</span>
      {feedHealth.last_tick && (
        <span className="text-muted">· Last tick: {new Date(feedHealth.last_tick).toLocaleTimeString()}</span>
      )}
      {haltStatus.halted && (
        <span className="ml-auto text-down font-semibold">⚠ TRADING HALTED — {haltStatus.reason}</span>
      )}
    </div>
  );
}

export default function App() {
  useMarketSocket();
  const { activeView, setSettings } = useMarketStore();

  // Load persisted settings on startup
  useEffect(() => {
    fetch(`${API}/api/settings`)
      .then(r => r.json())
      .then(data => setSettings(data))
      .catch(() => {});
  }, []);

  return (
    <div className="flex h-full w-full overflow-hidden bg-bg text-white">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0">
        <Header />
        <EngineStatus />
        <main className="flex-1 overflow-hidden">
          {activeView === 'dashboard'     && <Dashboard />}
          {activeView === 'testing_lab'   && (
            <div className="p-6 overflow-y-auto h-full">
              <h1 className="text-white font-bold text-lg mb-4">
                Testing Lab
                <span className="ml-2 text-xs font-normal text-accent bg-accent/10 px-2 py-0.5 rounded">PAPER</span>
              </h1>
              <TradeTable env="paper" />
            </div>
          )}
          {activeView === 'battle_ground' && (
            <div className="p-6 overflow-y-auto h-full">
              <h1 className="text-white font-bold text-lg mb-4">
                Battle Ground
                <span className="ml-2 text-xs font-normal text-down bg-down/10 px-2 py-0.5 rounded">LIVE</span>
              </h1>
              <TradeTable env="live" />
            </div>
          )}
          {activeView === 'reports'       && <ReportsView />}
        </main>
      </div>
    </div>
  );
}
