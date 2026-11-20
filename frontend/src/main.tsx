import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import OfflineScreen from './components/layout/OfflineScreen'
import { backendState, initApi, onBackendChange } from './lib/api'

/** On the hosted team site the app only mounts while the trading PC is connected,
 *  so an offline backend costs one status check every 10 s, not every panel's polling. */
function Root() {
  const [state, setState] = useState(backendState);
  useEffect(() => onBackendChange(() => setState(backendState)), []);
  if (state === 'offline' || state === 'checking') return <OfflineScreen checking={state === 'checking'} />;
  return <App />;
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
initApi()
