import { useEffect, useRef, useState } from 'react';

const API = 'http://localhost:8000';

interface CheckResult {
  check: string;
  node:  string;
  ok:    boolean;
  msg:   string;
}

interface DryRunStatus {
  running: boolean;
  done:    boolean;
  go:      boolean;
  passed:  number;
  total:   number;
  results: CheckResult[];
  run_at:  string | null;
}

const NODE_COLORS: Record<string, string> = {
  'strategy-es': 'bg-green-500/15 text-green-400 border-green-500/30',
  'strategy-ob': 'bg-teal-500/15 text-teal-400 border-teal-500/30',
  'execution':   'bg-orange-500/15 text-orange-400 border-orange-500/30',
  'reporting':   'bg-blue-500/15 text-blue-400 border-blue-500/30',
};

const ALL_NODES = [
  { check: 'ES · Params',           node: 'strategy-es' },
  { check: 'ES · Filter logic',     node: 'strategy-es' },
  { check: 'ES · Option selector',  node: 'execution'   },
  { check: 'ES · Option premium',   node: 'execution'   },
  { check: 'ES · Quantity calc',    node: 'execution'   },
  { check: 'ES · Trail logic',      node: 'strategy-es' },
  { check: 'OB · Params',           node: 'strategy-ob' },
  { check: 'OB · Sector math',      node: 'strategy-ob' },
  { check: 'OB · Breakout confirm', node: 'strategy-ob' },
  { check: 'OB · Option selector',  node: 'execution'   },
  { check: 'OB · Trail logic',      node: 'strategy-ob' },
  { check: 'Report · Costs',        node: 'reporting'   },
  { check: 'Report · HTML builder', node: 'reporting'   },
];

function NodeBadge({ node }: { node: string }) {
  const cls = NODE_COLORS[node] ?? 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30';
  return (
    <span className={`inline-flex items-center text-[9px] font-mono tracking-wider px-1.5 py-0.5 rounded border ${cls}`}>
      {node}
    </span>
  );
}

function NodeRow({ result, pending }: { result?: CheckResult; pending?: string }) {
  if (!result) {
    return (
      <div className="flex items-center gap-2.5 py-1.5 px-3 rounded-lg">
        <span className="w-3.5 h-3.5 flex-shrink-0 flex items-center justify-center">
          <span className="w-3 h-3 rounded-full border-2 border-zinc-600 border-t-zinc-400 animate-spin" />
        </span>
        <span className="text-xs text-zinc-500">{pending}</span>
      </div>
    );
  }

  return (
    <div className={`flex items-start gap-2.5 py-1.5 px-3 rounded-lg transition-colors
      ${result.ok ? 'bg-green-500/5' : 'bg-red-500/8'}`}>
      <span className="w-3.5 h-3.5 flex-shrink-0 mt-0.5 text-center leading-none">
        {result.ok
          ? <span className="text-green-400 text-xs">✓</span>
          : <span className="text-red-400 text-xs">✗</span>
        }
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className={`text-xs font-medium ${result.ok ? 'text-zinc-200' : 'text-red-300'}`}>
            {result.check}
          </span>
          <NodeBadge node={result.node} />
        </div>
        <p className={`text-[10px] leading-snug break-all ${result.ok ? 'text-zinc-500' : 'text-red-400/80'}`}>
          {result.msg}
        </p>
      </div>
    </div>
  );
}

const DR_KEY = 'dryrun-dismissed';

export default function StrategyDryRunPanel() {
  const [status, setStatus] = useState<DryRunStatus | null>(null);
  const [dismissed, setDismissed] = useState(() => sessionStorage.getItem(DR_KEY) === '1');
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = () => {
    if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null; }
  };

  const startPolling = () => {
    stopPolling();
    pollingRef.current = setInterval(async () => {
      try {
        const r = await fetch(`${API}/api/simulation/dryrun/status`);
        const d: DryRunStatus = await r.json();
        setStatus(d);
        if (d.done && !d.running) stopPolling();
      } catch { /* server not yet ready */ }
    }, 800);
  };

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(`${API}/api/simulation/dryrun/status`);
        const d: DryRunStatus = await r.json();
        setStatus(d);
        if (!d.done || d.running) startPolling();
      } catch {
        startPolling();
      }
    })();
    return stopPolling;
  }, []);

  const dismiss = () => { sessionStorage.setItem(DR_KEY, '1'); setDismissed(true); };
  const expand  = () => { sessionStorage.removeItem(DR_KEY);  setDismissed(false); };

  const rerun = async () => {
    expand();
    try { await fetch(`${API}/api/simulation/dryrun/run`, { method: 'POST' }); } catch { /* ignore */ }
    setStatus(null);
    startPolling();
  };

  // Auto-dismiss after 12s on all-green
  useEffect(() => {
    if (status?.done && status.go) {
      const t = setTimeout(dismiss, 12000);
      return () => clearTimeout(t);
    }
  }, [status?.done, status?.go]);

  if (dismissed) {
    return (
      <button
        onClick={expand}
        className="w-full flex items-center gap-2 px-3 py-2 bg-green-500/8 border border-green-500/20
          rounded-xl text-xs text-green-400 hover:bg-green-500/12 transition-colors"
      >
        <span>✓</span>
        <span>All {ALL_NODES.length} strategy nodes passed dry-run</span>
        <span className="ml-auto text-green-500/40">click to expand</span>
      </button>
    );
  }

  const isRunning  = status?.running ?? false;
  const isDone     = status?.done ?? false;
  const isGo       = status?.go ?? false;
  const passed     = status?.passed ?? 0;
  const total      = status?.total ?? ALL_NODES.length;
  const results    = status?.results ?? [];

  const displayItems = ALL_NODES.map((n, i) => {
    const result = results.find(r => r.check === n.check);
    return result
      ? { type: 'done' as const, result }
      : { type: isRunning && i === results.length ? 'active' : 'pending' as const, ...n };
  });

  return (
    <div className={`rounded-xl border transition-colors overflow-hidden
      ${isDone
        ? isGo  ? 'bg-surface border-green-500/30' : 'bg-surface border-red-500/30'
        : 'bg-surface border-border'
      }`}>

      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          {!isDone && isRunning && (
            <span className="w-3 h-3 flex-shrink-0 rounded-full border-2 border-accent border-t-transparent animate-spin" />
          )}
          {isDone && (
            <span className={`text-sm ${isGo ? 'text-green-400' : 'text-red-400'}`}>
              {isGo ? '✓' : '✗'}
            </span>
          )}
          <h2 className="text-white font-semibold text-sm">Strategy Dry-Run</h2>
          {isDone && (
            <span className={`text-[10px] font-semibold px-2 py-0.5 rounded
              ${isGo ? 'bg-green-500/15 text-green-400' : 'bg-red-500/15 text-red-400'}`}>
              {isGo ? 'CLEAN' : 'BUGS FOUND'}
            </span>
          )}
          {isRunning && (
            <span className="text-[10px] text-muted">running {results.length}/{total}…</span>
          )}
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          {isDone && (
            <span className={`text-xs font-mono ${isGo ? 'text-green-400' : 'text-red-400'}`}>
              {passed}/{total}
            </span>
          )}
          <button
            onClick={rerun}
            disabled={isRunning}
            className="text-[10px] px-2.5 py-1 rounded border border-border text-muted
              hover:border-accent hover:text-accent disabled:opacity-40 disabled:cursor-not-allowed
              transition-colors"
          >
            {isRunning ? 'running…' : 'Re-run'}
          </button>
          {isDone && isGo && (
            <button
              onClick={dismiss}
              className="text-[10px] text-zinc-600 hover:text-zinc-400 transition-colors px-1"
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {/* Node list */}
      <div className="p-2 space-y-0.5">
        {displayItems.map((item, i) => {
          if (item.type === 'done') {
            return <NodeRow key={i} result={item.result} />;
          }
          if (item.type === 'active') {
            return <NodeRow key={i} pending={item.check} />;
          }
          if (!status || results.length === 0) return null;
          return (
            <div key={i} className="flex items-center gap-2.5 py-1.5 px-3 opacity-30">
              <span className="w-3.5 h-3.5 flex-shrink-0 flex items-center justify-center">
                <span className="w-2 h-2 rounded-full bg-zinc-700" />
              </span>
              <span className="text-xs text-zinc-600">{item.check}</span>
              <NodeBadge node={item.node} />
            </div>
          );
        })}

        {!status && (
          <div className="py-4 text-center text-xs text-muted">
            Waiting for backend…
          </div>
        )}
      </div>

      {/* Footer on failure */}
      {isDone && !isGo && (
        <div className="px-4 py-2.5 border-t border-red-500/20 bg-red-500/5">
          <p className="text-[10px] text-red-400">
            {total - passed} node{total - passed !== 1 ? 's' : ''} failed.
            Fix the errors above — these will crash during a live session.
          </p>
        </div>
      )}
    </div>
  );
}
