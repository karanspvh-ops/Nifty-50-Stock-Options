import { useEffect, useRef, useState } from 'react';
import { ShieldCheck, ShieldWarning, CircleNotch, CaretDown } from '@phosphor-icons/react';
import SystemCheckPanel from '../dashboard/SystemCheckPanel';
import StrategyDryRunPanel from '../dashboard/StrategyDryRunPanel';

const API = 'http://localhost:8000';

interface Run { running: boolean; done: boolean; passed: number; total: number }

/** Compact header pill for the system check + strategy dry-run; expands to the full panels. */
export default function ChecksPill() {
  const [sys, setSys] = useState<Run | null>(null);
  const [dry, setDry] = useState<Run | null>(null);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const [a, b] = await Promise.all([
          fetch(`${API}/api/simulation/status`).then(r => r.json()),
          fetch(`${API}/api/simulation/dryrun/status`).then(r => r.json()),
        ]);
        setSys(a); setDry(b);
      } catch { /* backend not up yet */ }
    };
    load();
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey  = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    return () => { window.removeEventListener('mousedown', onDown); window.removeEventListener('keydown', onKey); };
  }, [open]);

  const running = !!(sys?.running || dry?.running);
  const passed  = (sys?.passed ?? 0) + (dry?.passed ?? 0);
  const total   = (sys?.total ?? 10) + (dry?.total ?? 16);
  const done    = !!(sys?.done && dry?.done);
  const allOk   = done && passed === total;

  const tone = running || !done ? 'bg-bg text-ink2 border-border'
    : allOk ? 'bg-up/10 text-up border-up/20' : 'bg-down/10 text-down border-down/30';

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        title="System check and strategy dry-run"
        className={`pressable flex items-center gap-1.5 h-8 px-3 rounded-full border text-xs font-semibold ${tone}`}
      >
        {running ? <CircleNotch size={15} className="animate-spin" />
          : allOk || !done ? <ShieldCheck size={15} weight="fill" /> : <ShieldWarning size={15} weight="fill" />}
        {running ? 'Checking' : !done ? 'Checks' : allOk ? `Checks ${passed}/${total}` : `${total - passed} check${total - passed > 1 ? 's' : ''} failed`}
        <CaretDown size={11} weight="bold" className={`transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="glass-strong rise-in absolute left-0 top-10 z-50 w-[620px] max-w-[calc(100vw-120px)] max-h-[75vh] overflow-y-auto rounded-panel p-3 space-y-3"
             style={{ transformOrigin: 'top left' }}>
          <SystemCheckPanel />
          <StrategyDryRunPanel />
        </div>
      )}
    </div>
  );
}
