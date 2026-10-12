import ReactDOM from 'react-dom/client'

import App from './App'
import { Toaster } from './components/ui/sonner'
import { ThemeProvider, useTheme } from './lib/theme'
import './index.css'

/** No richColors — toasts use the popover tokens + a colored icon, which is far
 *  less jarring than saturated full-bleed red/green panels. */
function ThemedToaster() {
  const { resolved } = useTheme()
  return <Toaster theme={resolved} position="top-right" closeButton />
}

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <ThemeProvider>
    <App />
    <ThemedToaster />
  </ThemeProvider>
)
