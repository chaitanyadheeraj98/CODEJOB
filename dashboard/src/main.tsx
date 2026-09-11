import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { installApiCredentials } from './apiCredentials.ts'

// Before anything renders: the API is on a different origin, so every request
// must opt in to sending the session cookie. See apiCredentials.ts.
installApiCredentials()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
