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

const RAIL_ITEM = 60;   // px, desktop rail item height
const RAIL_GAP  = 6;    // px, gap between rail items

/**
 * Navigation as floating liquid glass.
 * Phones: a capsule tab bar hovering above the home indicator, content
 * scrolling beneath it. Tablet/desktop: a floating rail. On both, the
 * selection is a drop of tinted glass that slides to the chosen section.
 */
export default function Sidebar() {
  const { activeView, setActiveView } = useMarketStore();
  const at = Math.max(0, NAV.findIndex(n => n.id === activeView));
  const go = (id: View) => {
    setActiveView(id);
    // Each view keeps its own scroller; a new tab starts at its top
    requestAnimationFrame(() => document.querySelector('main .page')?.scrollTo({ top: 0 }));
  };

  return (
    <aside
      className="glass z-40 rounded-[30px]
        fixed left-3 right-3 bottom-[max(10px,env(safe-area-inset-bottom))]
        md:static md:shrink-0 md:my-3 md:ml-3 md:w-[84px] md:rounded-[26px]
        md:flex md:flex-col md:items-center md:py-4"
    >
      <img
        src={`${import.meta.env.BASE_URL}spvh-mark.png`}
        alt="SPVH"
        className="hidden md:block w-10 h-10 object-contain mb-5 mt-1 select-none"
        draggable={false}
      />
      <nav
        className="relative grid grid-cols-6 p-1.5 md:flex md:flex-col md:w-full md:px-2 md:p-0 md:gap-[6px]"
        aria-label="Sections"
      >
        {/* Phone lens: one column wide, slides sideways */}
        <span
          aria-hidden
          className="glass-lens md:hidden absolute top-1.5 bottom-1.5 left-1.5 rounded-[24px]"
          style={{ width: 'calc((100% - 12px) / 6)', transform: `translateX(${at * 100}%)` }}
        />
        {/* Rail lens: slides vertically */}
        <span
          aria-hidden
          className="glass-lens hidden md:block absolute left-2 right-2 top-0 rounded-[16px]"
          style={{ height: RAIL_ITEM, transform: `translateY(${at * (RAIL_ITEM + RAIL_GAP)}px)` }}
        />
        {NAV.map(({ id, icon: I, label }) => {
          const active = activeView === id;
          return (
            <button
              key={id}
              onClick={() => go(id)}
              aria-current={active ? 'page' : undefined}
              className={`pressable relative min-w-0 flex flex-col items-center justify-center gap-[3px] md:gap-1
                h-[52px] md:h-[60px] md:w-full rounded-[22px] md:rounded-[16px]
                ${active ? 'text-accent' : 'text-ink2 hover:text-ink'}`}
            >
              <I size={24} weight={active ? 'fill' : 'regular'} className="md:w-[22px] md:h-[22px]" />
              <span className={`max-w-full truncate px-0.5 text-[11px] md:text-[12.5px] leading-none
                ${active ? 'font-bold' : 'font-medium'}`}>
                {label}
              </span>
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
