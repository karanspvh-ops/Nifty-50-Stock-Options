import { useMarketStore } from '../../store/marketStore';

const NAV = [
  { id: 'dashboard',    icon: '▦', label: 'Dashboard'     },
  { id: 'early_scalp',  icon: '⚡', label: 'Early Scalp'  },
  { id: 'testing_lab',  icon: '⚗', label: 'Testing Lab'   },
  { id: 'battle_ground',icon: '⚔', label: 'Battle Ground' },
  { id: 'reports',      icon: '📊', label: 'Reports'       },
  { id: 'nifty_data',   icon: '📈', label: 'Nifty Data'    },
] as const;

export default function Sidebar() {
  const { activeView, setActiveView } = useMarketStore();
  return (
    <aside className="w-16 flex flex-col items-center py-6 gap-2 bg-surface border-r border-border shrink-0">
      <div className="text-accent font-bold text-lg mb-6">N</div>
      {NAV.map(n => (
        <button
          key={n.id}
          onClick={() => setActiveView(n.id)}
          title={n.label}
          className={`w-10 h-10 rounded-lg flex items-center justify-center text-lg transition-colors
            ${activeView === n.id
              ? 'bg-accent text-white'
              : 'text-muted hover:bg-border hover:text-white'}`}
        >
          {n.icon}
        </button>
      ))}
    </aside>
  );
}
