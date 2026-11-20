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

/**
 * Navigation. Phones: an iOS-style tab bar along the bottom, within thumb
 * reach, clear of the home indicator. Tablet and desktop: an 84px rail.
 */
export default function Sidebar() {
  const { activeView, setActiveView } = useMarketStore();
  const go = (id: View) => {
    setActiveView(id);
    // Each view keeps its own scroller; a new tab starts at its top
    requestAnimationFrame(() => document.querySelector('main .page')?.scrollTo({ top: 0 }));
  };

  return (
    <aside
      className="glass z-20 shrink-0 order-last md:order-first
        border-t md:border-t-0 md:border-r border-white/60
        pb-[env(safe-area-inset-bottom)] md:pb-0
        md:w-[84px] md:flex md:flex-col md:items-center md:py-4 md:gap-1"
    >
      <img
        src={`${import.meta.env.BASE_URL}spvh-mark.png`}
        alt="SPVH"
        className="hidden md:block w-10 h-10 object-contain mb-5 mt-1 select-none"
        draggable={false}
      />
      <nav
        className="grid grid-cols-6 px-1 md:flex md:flex-col md:items-center md:gap-1.5 md:w-full md:px-2"
        aria-label="Sections"
      >
        {NAV.map(({ id, icon: I, label }) => {
          const active = activeView === id;
          return (
            <button
              key={id}
              onClick={() => go(id)}
              aria-current={active ? 'page' : undefined}
              className={`pressable group min-w-0 flex flex-col items-center justify-center gap-[3px]
                h-[54px] md:h-auto md:w-full md:gap-1 md:py-2.5 rounded-control
                ${active
                  ? 'text-accent md:btn-primary'
                  : 'text-muted hover:text-ink md:hover:bg-ink/[0.04]'}`}
            >
              <I size={24} weight={active ? 'fill' : 'regular'} className="md:w-[22px] md:h-[22px]" />
              <span className={`max-w-full truncate px-0.5 text-[11px] md:text-[12.5px] leading-none
                ${active ? 'font-bold md:font-semibold' : 'font-medium md:font-normal'}`}>
                {label}
              </span>
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
