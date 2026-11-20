import { PlugsConnected } from '@phosphor-icons/react';

/** Hosted team site, backend not connected: nothing else is loaded or polled. */
export default function OfflineScreen({ checking }: { checking: boolean }) {
  return (
    <div className="h-full flex items-center justify-center px-6">
      <div className="glass rounded-panel max-w-md w-full px-8 py-10 text-center">
        <img src={`${import.meta.env.BASE_URL}spvh-wordmark.png`} alt="SPVH" className="h-9 mx-auto mb-6" />
        <div className="mx-auto mb-4 w-12 h-12 rounded-full bg-bg flex items-center justify-center text-muted">
          <PlugsConnected size={26} weight="bold" />
        </div>
        <h1 className="text-xl font-bold text-ink">
          {checking ? 'Connecting to the trading system' : 'The trading system is offline'}
        </h1>
        <p className="mt-2 text-sm text-muted">
          {checking
            ? 'One moment.'
            : 'The Options Desk opens here as soon as the backend is started on the office PC. This page checks every 10 seconds.'}
        </p>
      </div>
    </div>
  );
}
