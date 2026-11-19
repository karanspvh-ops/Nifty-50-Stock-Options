import {
  SquaresFour, Lightning, Flask, Crosshair, ChartBar, ChartLineUp,
  type Icon,
} from '@phosphor-icons/react';
import { useMarketStore } from '../../store/marketStore';

type View = ReturnType<typeof useMarketStore.getState>['activeView'];

const NAV: { id: View; icon: Icon; label: string }[] = [
  { id: 'dashboard',     icon: SquaresFour, label: 'Dashboard'   },
  { id: 'early_scalp',   icon: Lightning,   label: 'Early Scalp' },
  { id: 'testing_lab',   icon: Flask,       label: 'Paper'       },
  { id: 'battle_ground', icon: Crosshair,   label: 'Live'        },
  { id: 'reports',       icon: ChartBar,    label: 'Reports'     },
  { id: 'nifty_data',    icon: ChartLineUp, label: 'Nifty'       },
];

export default function Sidebar() {
  const { activeView, setActiveView } = useMarketStore();
  return (
    <aside className="glass w-[84px] shrink-0 flex flex-col items-center py-4 gap-1 border-r border-white/60 z-10">
      <img
        src={`${import.meta.env.BASE_URL}spvh-mark.png`}
        alt="SPVH"
        className="w-10 h-10 object-contain mb-5 mt-1 select-none"
        draggable={false}
      />
      <nav className="flex flex-col items-center gap-1.5 w-full px-2" aria-label="Sections">
        {NAV.map(({ id, icon: I, label }) => {
          const active = activeView === id;
          return (
            <button
              key={id}
              onClick={() => setActiveView(id)}
              aria-current={active ? 'page' : undefined}
              className={`pressable group w-full flex flex-col items-center gap-1 rounded-control py-2.5
                ${active ? 'btn-primary' : 'text-muted hover:text-ink hover:bg-ink/[0.04]'}`}
            >
              <I size={22} weight={active ? 'fill' : 'regular'} />
              <span className={`text-[12.5px] leading-none ${active ? 'font-semibold' : ''}`}>{label}</span>
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
