import { useEffect, useState } from 'react';
import { PlugsConnected } from '@phosphor-icons/react';
import { backendState, onBackendChange } from '../../lib/api';

/** Hosted team site only: shown while the trading PC's backend isn't reachable. */
export default function BackendOffline() {
  const [state, setState] = useState(backendState);
  useEffect(() => onBackendChange(() => setState(backendState)), []);
  if (state !== 'offline') return null;
  return (
    <div className="flex items-center gap-2 px-5 py-2.5 bg-ink text-white text-sm">
      <PlugsConnected size={18} weight="bold" />
      <span className="font-semibold">The trading system is offline.</span>
      <span className="text-white/75">This page reconnects on its own once the backend is started on the office PC.</span>
    </div>
  );
}
