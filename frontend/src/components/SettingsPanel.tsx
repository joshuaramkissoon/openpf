import { useMemo, useState } from 'react'

import { testTelegram, updateAccountCredentials, updateBroker, updateRisk, updateTelegram, updateWatchlist } from '../api/client'
import type { AppConfig } from '../types'

interface Props {
  config: AppConfig | null
  onReload: () => void
  onError: (message: string) => void
  hideHeader?: boolean
  presentationMask?: boolean
  onTogglePresentationMask?: (enabled: boolean) => void
}

export function SettingsPanel({
  config,
  onReload,
  onError,
  hideHeader = false,
  presentationMask = false,
  onTogglePresentationMask,
}: Props) {
  const [working, setWorking] = useState(false)

  const [investKey, setInvestKey] = useState('')
  const [investSecret, setInvestSecret] = useState('')
  const [isaKey, setIsaKey] = useState('')
  const [isaSecret, setIsaSecret] = useState('')

  const [watchlistText, setWatchlistText] = useState('')
  const [telegramToken, setTelegramToken] = useState('')
  const [telegramChatId, setTelegramChatId] = useState('')
  const [telegramUsers, setTelegramUsers] = useState('')

  const riskDefaults = useMemo(
    () => ({
      max_single_order_notional: config?.risk.max_single_order_notional ?? 500,
      max_daily_notional: config?.risk.max_daily_notional ?? 1500,
      max_position_weight: config?.risk.max_position_weight ?? 0.25,
      duplicate_order_window_seconds: config?.risk.duplicate_order_window_seconds ?? 90,
    }),
    [config]
  )

  const brokerDefaults = useMemo(
    () => ({
      broker_mode: config?.broker.broker_mode ?? 'paper',
      autopilot_enabled: config?.broker.autopilot_enabled ?? false,
      t212_base_env: config?.broker.t212_base_env ?? 'demo',
    }),
    [config]
  )

  async function saveRisk(formData: FormData) {
    setWorking(true)
    try {
      await updateRisk({
        max_single_order_notional: Number(formData.get('max_single_order_notional')),
        max_daily_notional: Number(formData.get('max_daily_notional')),
        max_position_weight: Number(formData.get('max_position_weight')),
        duplicate_order_window_seconds: Number(formData.get('duplicate_order_window_seconds')),
      })
      onReload()
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'Failed to save risk settings'
      onError(msg)
    } finally {
      setWorking(false)
    }
  }

  async function saveBroker(formData: FormData) {
    setWorking(true)
    try {
      await updateBroker({
        broker_mode: String(formData.get('broker_mode')) as 'paper' | 'live',
        t212_base_env: String(formData.get('t212_base_env')) as 'demo' | 'live',
        autopilot_enabled: formData.get('autopilot_enabled') === 'on',
      })
      onReload()
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'Failed to save broker settings'
      onError(msg)
    } finally {
      setWorking(false)
    }
  }

  async function saveInvestCredentials(formData: FormData) {
    const enabled = formData.get('invest_enabled') === 'on'
    if ((investKey || investSecret) && (!investKey || !investSecret)) {
      onError('Provide both Invest key and secret, or leave both blank.')
      return
    }

    setWorking(true)
    try {
      await updateAccountCredentials('invest', {
        t212_api_key: investKey,
        t212_api_secret: investSecret,
        enabled,
      })
      setInvestKey('')
      setInvestSecret('')
      onReload()
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'Failed to save Invest credentials'
      onError(msg)
    } finally {
      setWorking(false)
    }
  }

  async function saveIsaCredentials(formData: FormData) {
    const enabled = formData.get('isa_enabled') === 'on'
    if ((isaKey || isaSecret) && (!isaKey || !isaSecret)) {
      onError('Provide both ISA key and secret, or leave both blank.')
      return
    }

    setWorking(true)
    try {
      await updateAccountCredentials('stocks_isa', {
        t212_api_key: isaKey,
        t212_api_secret: isaSecret,
        enabled,
      })
      setIsaKey('')
      setIsaSecret('')
      onReload()
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'Failed to save ISA credentials'
      onError(msg)
    } finally {
      setWorking(false)
    }
  }

  async function saveWatchlist() {
    setWorking(true)
    try {
      const parsed = watchlistText
        .split(',')
        .map((s) => s.trim().toUpperCase())
        .filter(Boolean)
      await updateWatchlist(parsed)
      setWatchlistText('')
      onReload()
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'Failed to update watchlist'
      onError(msg)
    } finally {
      setWorking(false)
    }
  }

  async function saveTelegram(formData: FormData) {
    setWorking(true)
    try {
      const ids = telegramUsers
        .split(',')
        .map((x) => x.trim())
        .filter(Boolean)
        .map((x) => Number(x))
        .filter((x) => Number.isFinite(x))

      await updateTelegram({
        enabled: formData.get('telegram_enabled') === 'on',
        poll_enabled: formData.get('telegram_poll_enabled') === 'on',
        chat_id: telegramChatId || config?.telegram.chat_id || '',
        bot_token: telegramToken || undefined,
        high_conviction_threshold: Number(formData.get('telegram_high_conviction_threshold')),
        notify_general_updates: formData.get('telegram_notify_general_updates') === 'on',
        allowed_user_ids: ids.length > 0 ? ids : config?.telegram.allowed_user_ids || [],
      })
      setTelegramToken('')
      onReload()
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'Failed to save Telegram settings'
      onError(msg)
    } finally {
      setWorking(false)
    }
  }

  async function triggerTelegramTest() {
    setWorking(true)
    try {
      await testTelegram('MyPF test ping: Telegram integration is active.')
      onReload()
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'Failed to send Telegram test message'
      onError(msg)
    } finally {
      setWorking(false)
    }
  }

  return (
    <section className="glass-card settings-card">
      {!hideHeader && (
        <div className="section-heading-row">
          <h2>Control Tower</h2>
          <span className="hint">Broker mode, guardrails, dual-account credentials, Telegram ops</span>
        </div>
      )}

      <div className="settings-form">
        <h3>Presentation</h3>
        <label className="check">
          <input
            type="checkbox"
            checked={presentationMask}
            onChange={(e) => onTogglePresentationMask?.(e.target.checked)}
          />
          Obfuscate portfolio values in UI (demo mode)
        </label>
        <p className="muted">Masks cash, totals, invested, P/L, prices, and quantities for safer screen sharing.</p>
      </div>

      <form
        className="settings-form"
        onSubmit={(e) => {
          e.preventDefault()
          void saveBroker(new FormData(e.currentTarget))
        }}
      >
        <h3>Broker</h3>
        <label>
          Mode
          <select name="broker_mode" defaultValue={brokerDefaults.broker_mode}>
            <option value="paper">paper</option>
            <option value="live">live</option>
          </select>
        </label>
        <label>
          T212 Env
          <select name="t212_base_env" defaultValue={brokerDefaults.t212_base_env}>
            <option value="demo">demo</option>
            <option value="live">live</option>
          </select>
        </label>
        <label className="check">
          <input type="checkbox" name="autopilot_enabled" defaultChecked={brokerDefaults.autopilot_enabled} />
          Enable autopilot execution
        </label>
        <button className="btn" disabled={working}>Save broker</button>
      </form>

      <form
        className="settings-form"
        onSubmit={(e) => {
          e.preventDefault()
          void saveRisk(new FormData(e.currentTarget))
        }}
      >
        <h3>Risk Rails</h3>
        <label>
          Max single order ($)
          <input name="max_single_order_notional" defaultValue={riskDefaults.max_single_order_notional} />
        </label>
        <label>
          Max daily notional ($)
          <input name="max_daily_notional" defaultValue={riskDefaults.max_daily_notional} />
        </label>
        <label>
          Max position weight (0-1)
          <input name="max_position_weight" defaultValue={riskDefaults.max_position_weight} />
        </label>
        <label>
          Duplicate window (sec)
          <input name="duplicate_order_window_seconds" defaultValue={riskDefaults.duplicate_order_window_seconds} />
        </label>
        <button className="btn" disabled={working}>Save risk</button>
      </form>

      <form
        className="settings-form"
        onSubmit={(e) => {
          e.preventDefault()
          void saveInvestCredentials(new FormData(e.currentTarget))
        }}
      >
        <h3>Invest Credentials</h3>
        <p className="muted">Configured: {config?.credentials?.invest?.configured ? 'yes' : 'no'}</p>
        <label className="check">
          <input type="checkbox" name="invest_enabled" defaultChecked={config?.credentials?.invest?.enabled ?? true} />
          Enable Invest account sync
        </label>
        <label>
          Invest API Key (optional if already configured)
          <input value={investKey} onChange={(e) => setInvestKey(e.target.value)} placeholder="Paste Invest API key" />
        </label>
        <label>
          Invest API Secret (optional if already configured)
          <input value={investSecret} onChange={(e) => setInvestSecret(e.target.value)} placeholder="Paste Invest API secret" />
        </label>
        <button className="btn" disabled={working}>Save Invest credentials</button>
      </form>

      <form
        className="settings-form"
        onSubmit={(e) => {
          e.preventDefault()
          void saveIsaCredentials(new FormData(e.currentTarget))
        }}
      >
        <h3>Stocks ISA Credentials</h3>
        <p className="muted">Configured: {config?.credentials?.stocks_isa?.configured ? 'yes' : 'no'}</p>
        <label className="check">
          <input type="checkbox" name="isa_enabled" defaultChecked={config?.credentials?.stocks_isa?.enabled ?? true} />
          Enable ISA account sync
        </label>
        <label>
          ISA API Key (optional if already configured)
          <input value={isaKey} onChange={(e) => setIsaKey(e.target.value)} placeholder="Paste ISA API key" />
        </label>
        <label>
          ISA API Secret (optional if already configured)
          <input value={isaSecret} onChange={(e) => setIsaSecret(e.target.value)} placeholder="Paste ISA API secret" />
        </label>
        <button className="btn" disabled={working}>Save ISA credentials</button>
      </form>

      <div className="settings-form">
        <h3>Watchlist</h3>
        <p className="muted">Current: {config?.watchlist?.join(', ') || 'none'}</p>
        <label>
          Symbols (comma-separated)
          <input value={watchlistText} onChange={(e) => setWatchlistText(e.target.value)} placeholder="SMCI, AVGO, META" />
        </label>
        <button className="btn" onClick={() => void saveWatchlist()} disabled={working}>
          Update watchlist
        </button>
      </div>

      <form
        className="settings-form"
        onSubmit={(e) => {
          e.preventDefault()
          void saveTelegram(new FormData(e.currentTarget))
        }}
      >
        <h3>Telegram Ops</h3>
        <p className="muted">Token configured: {config?.telegram.bot_token_configured ? 'yes' : 'no'}</p>
        <label className="check">
          <input type="checkbox" name="telegram_enabled" defaultChecked={config?.telegram.enabled ?? false} />
          Enable Telegram integration
        </label>
        <label className="check">
          <input type="checkbox" name="telegram_poll_enabled" defaultChecked={config?.telegram.poll_enabled ?? true} />
          Poll Telegram for commands
        </label>
        <label className="check">
          <input
            type="checkbox"
            name="telegram_notify_general_updates"
            defaultChecked={config?.telegram.notify_general_updates ?? true}
          />
          Send general updates
        </label>
        <label>
          Bot token (optional if already set)
          <input value={telegramToken} onChange={(e) => setTelegramToken(e.target.value)} placeholder="123456:AA..." />
        </label>
        <label>
          Chat ID
          <input
            value={telegramChatId}
            onChange={(e) => setTelegramChatId(e.target.value)}
            placeholder={config?.telegram.chat_id || 'e.g. 123456789'}
          />
        </label>
        <label>
          Allowed user IDs (comma-separated)
          <input
            value={telegramUsers}
            onChange={(e) => setTelegramUsers(e.target.value)}
            placeholder={(config?.telegram.allowed_user_ids || []).join(', ')}
          />
        </label>
        <label>
          High conviction threshold (0-1)
          <input
            name="telegram_high_conviction_threshold"
            defaultValue={config?.telegram.high_conviction_threshold ?? 0.68}
          />
        </label>
        <div className="intent-actions">
          <button className="btn" disabled={working}>Save Telegram</button>
          <button type="button" className="btn ghost" onClick={() => void triggerTelegramTest()} disabled={working}>
            Send test ping
          </button>
        </div>
      </form>
    </section>
  )
}
