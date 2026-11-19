import { useEffect, useRef, useState } from 'react';
import { CheckCircle, XCircle, X, CaretDown } from '@phosphor-icons/react';

const API = 'http://localhost:8000';

interface CheckResult {
  check: string;
  node:  string;
  ok:    boolean;
  msg:   string;
}

interface SimStatus {
  running: boolean;
  done:    boolean;
  go:      boolean;
  passed:  number;
  total:   number;
  results: CheckResult[];
  run_at:  string | null;
}



function NodeBadge({ node }: { node: string }) {
  return (
    <span className="inline-flex items-center text-[11px] font-mono px-1.5 py-px rounded-md bg-bg text-muted border border-border">
      {node}
    </span>
  );
}

function CheckRow({ result, pending }: { result?: CheckResult; pending?: string }) {
  if (!result) {
    return (
      <div className="flex items-center gap-2.5 py-1.5 px-3 rounded-control">
        <span className="w-3.5 h-3.5 flex-shrink-0 flex items-center justify-center">
          <span className="w-3 h-3 rounded-full border-2 border-border border-t-muted animate-spin" />
        </span>
        <span className="text-xs text-muted">{pending}</span>
      </div>
    );
  }

  return (
    <div className={`flex items-start gap-2.5 py-1.5 px-3 rounded-control transition-colors
      ${result.ok ? '' : 'bg-down/[0.06]'}`}>
      <span className="w-3.5 h-3.5 flex-shrink-0 mt-0.5 text-center leading-none">
        {result.ok
          ? <CheckCircle size={16} weight="fill" className="text-up" />
          : <XCircle size={16} weight="fill" className="text-down" />
        }
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className={`text-xs font-medium ${result.ok ? 'text-ink' : 'text-down'}`}>
            {result.check}
          </span>
          <NodeBadge node={result.node} />
        </div>
        <p className={`text-[12px] leading-snug break-all ${result.ok ? 'text-muted' : 'text-down'}`}>
          {result.msg}
        </p>
      </div>
    </div>
  );
}

const ALL_CHECKS = [
  { check: 'Database',          node: 'storage'     },
  { check: 'Kite Token',        node: 'auth'        },
  { check: 'Kite API',          node: 'zerodha-api' },
  { check: 'Instruments Cache', node: 'universe'    },
  { check: 'Historical Data',   node: 'zerodha-api' },
  { check: 'Clock (IST)',       node: 'system'      },
  { check: 'ES Strategy',       node: 'strategy-es' },
  { check: 'OB Strategy',       node: 'strategy-ob' },
  { check: 'Risk Engine',       node: 'risk'        },
  { check: 'SMTP',              node: 'email'       },
];

const SC_KEY = 'systemcheck-dismissed';

export default function SystemCheckPanel() {
  const [status, setStatus] = useState<SimStatus | null>(null);
  const [dismissed, setDismissed] = useState(() => sessionStorage.getItem(SC_KEY) === '1');
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = () => {
    if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null; }
  };

  const startPolling = () => {
    stopPolling();
    pollingRef.current = setInterval(async () => {
      try {
        const r = await fetch(`${API}/api/simulation/status`);
        const d: SimStatus = await r.json();
        setStatus(d);
        if (d.done && !d.running) stopPolling();
      } catch { /* server not yet ready */ }
    }, 800);
  };

  useEffect(() => {
    // On mount, fetch current status first then start polling
    (async () => {
      try {
        const r = await fetch(`${API}/api/simulation/status`);
        const d: SimStatus = await r.json();
        setStatus(d);
        if (!d.done || d.running) startPolling();
      } catch {
        startPolling();   // server not ready yet, keep trying
      }
    })();
    return stopPolling;
  }, []);

  const dismiss = () => { sessionStorage.setItem(SC_KEY, '1'); setDismissed(true); };
  const expand  = () => { sessionStorage.removeItem(SC_KEY);  setDismissed(false); };

  const rerun = async () => {
    expand();
    try { await fetch(`${API}/api/simulation/run`, { method: 'POST' }); } catch { /* ignore */ }
    setStatus(null);
    startPolling();
  };

  // When done+go: write sessionStorage immediately (so tab-switches stay collapsed),
  // then visually collapse after a short delay to let the user see the results.
  useEffect(() => {
    if (status?.done && status.go) {
      sessionStorage.setItem(SC_KEY, '1');
      const t = setTimeout(() => setDismissed(true), 8000);
      return () => clearTimeout(t);
    }
  }, [status?.done, status?.go]);

  if (dismissed) {
    return (
      <button
        onClick={expand}
        className="pressable w-full flex items-center gap-2 px-4 py-2.5 bg-surface border border-border
          rounded-xl text-sm text-ink hover:border-spvh-300"
      >
        <CheckCircle size={18} weight="fill" className="text-up" />
        <span>All 10 system checks passed</span>
        <span className="ml-auto flex items-center gap-1 text-muted text-xs">Details <CaretDown size={12} /></span>
      </button>
    );
  }

  const isRunning  = status?.running ?? false;
  const isDone     = status?.done ?? false;
  const isGo       = status?.go ?? false;
  const passed     = status?.passed ?? 0;
  const total      = status?.total ?? ALL_CHECKS.length;
  const results    = status?.results ?? [];

  // Build the display list — completed results + pending items
  const displayItems = ALL_CHECKS.map((c, i) => {
    const result = results.find(r => r.check === c.check);
    return result
      ? { type: 'done' as const, result }
      : { type: isRunning && i === results.length ? 'active' : 'pending' as const, ...c };
  });

  return (
    <div className={`rounded-panel border transition-colors overflow-hidden
      ${isDone
        ? isGo  ? 'bg-surface border-border' : 'bg-surface border-down/40'
        : 'bg-surface border-border'
      }`}>

      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          {!isDone && isRunning && (
            <span className="w-3 h-3 flex-shrink-0 rounded-full border-2 border-accent border-t-transparent animate-spin" />
          )}
          {isDone && (
            isGo
              ? <CheckCircle size={20} weight="fill" className="text-up" />
              : <XCircle size={20} weight="fill" className="text-down" />
          )}
          <h2 className="text-ink font-bold text-base">System Check</h2>
          {isDone && (
            <span className={`text-[12px] font-semibold px-2 py-0.5 rounded
              ${isGo ? 'bg-up/10 text-up' : 'bg-down/10 text-down'}`}>
              {isGo ? 'GO' : 'NO-GO'}
            </span>
          )}
          {isRunning && (
            <span className="text-[12px] text-muted">running {results.length}/{total}…</span>
          )}
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          {isDone && (
            <span className={`text-xs font-mono ${isGo ? 'text-up' : 'text-down'}`}>
              {passed}/{total}
            </span>
          )}
          <button
            onClick={rerun}
            disabled={isRunning}
            className="pressable text-xs font-semibold px-3 py-1 rounded-full border border-border bg-white text-ink2
              hover:border-spvh-400 hover:text-accent disabled:opacity-40 disabled:cursor-not-allowed
              transition-colors"
          >
            {isRunning ? 'running…' : 'Re-run'}
          </button>
          {isDone && isGo && (
            <button
              onClick={dismiss}
              aria-label="Collapse" className="pressable text-muted hover:text-ink p-1 rounded-md hover:bg-ink/5"
            >
              <X size={16} />
            </button>
          )}
        </div>
      </div>

      {/* Check list */}
      <div className="p-2 space-y-0.5">
        {displayItems.map((item, i) => {
          if (item.type === 'done') {
            return <CheckRow key={i} result={item.result} />;
          }
          if (item.type === 'active') {
            return <CheckRow key={i} pending={item.check} />;
          }
          // Pending — only show if we've started at all
          if (!status || results.length === 0) return null;
          return (
            <div key={i} className="flex items-center gap-2.5 py-1.5 px-3 opacity-30">
              <span className="w-3.5 h-3.5 flex-shrink-0 flex items-center justify-center">
                <span className="w-2 h-2 rounded-full bg-border" />
              </span>
              <span className="text-xs text-faint">{item.check}</span>
              <NodeBadge node={item.node} />
            </div>
          );
        })}

        {/* Empty state — before first run */}
        {!status && (
          <div className="py-4 text-center text-xs text-muted">
            Waiting for backend…
          </div>
        )}
      </div>

      {/* Footer summary when done with failures */}
      {isDone && !isGo && (
        <div className="px-4 py-2.5 border-t border-down/20 bg-down/[0.05]">
          <p className="text-xs text-down">
            {total - passed} check{total - passed !== 1 ? 's' : ''} failed.
            Fix the issues above before starting a live session.
          </p>
        </div>
      )}
    </div>
  );
}
