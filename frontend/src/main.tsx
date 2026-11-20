import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { initApi } from './lib/api'

// Hosted team site: start the desk data socket (and local answer cache) before the
// first panel polls. On the trading PC this does nothing.
initApi()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
