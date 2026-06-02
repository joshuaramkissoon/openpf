import React from 'react'
import ReactDOM from 'react-dom/client'

import App from './App'
import { Toaster } from './components/ui/sonner'
import './index.css'

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <>
    <App />
    {/* Operator theme is dark; pin the toaster to match (no next-themes provider). */}
    <Toaster theme="dark" richColors position="top-right" closeButton />
  </>
)
