import { useMemo, useState } from 'react'
import { EyeOff, Send, ShieldCheck } from 'lucide-react'

import { testTelegram, updateAccountCredentials, updateBroker, updateRisk, updateTelegram, updateWatchlist } from '../api/client'
import type { AppConfig } from '../types'

import { SectionCard } from '@/components/kit'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Switch } from '@/components/ui/switch'

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
    <div className="min-w-0 space-y-5">
      {!hideHeader && (
        <div className="space-y-1">
          <h2 className="text-2xl font-semibold tracking-tight">Control Tower</h2>
          <p className="text-sm text-muted-foreground">
            Broker mode, guardrails, dual-account credentials, Telegram ops
          </p>
        </div>
      )}

      {/* Presentation — prominent, full width */}
      <SectionCard title="Presentation" description="Mask sensitive figures for safer screen sharing">
        <div className="flex items-start justify-between gap-4 rounded-lg border border-border/60 bg-muted/25 px-3.5 py-3">
          <div className="flex min-w-0 gap-3">
            <EyeOff className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
            <div className="min-w-0 space-y-1">
              <Label htmlFor="presentation-mask" className="text-sm font-medium">
                Obfuscate portfolio values (demo mode)
              </Label>
              <p className="text-xs text-muted-foreground">
                Masks cash, totals, invested, P/L, prices, and quantities for safer screen sharing.
              </p>
            </div>
          </div>
          <Switch
            id="presentation-mask"
            checked={presentationMask}
            onCheckedChange={(checked) => onTogglePresentationMask?.(checked)}
          />
        </div>
      </SectionCard>

      <div className="grid gap-5 sm:grid-cols-2">
        {/* Broker */}
        <SectionCard title="Broker" description="Execution mode and Trading 212 environment">
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault()
              void saveBroker(new FormData(e.currentTarget))
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="broker_mode">Mode</Label>
              <Select name="broker_mode" defaultValue={brokerDefaults.broker_mode}>
                <SelectTrigger id="broker_mode" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="paper">Paper</SelectItem>
                  <SelectItem value="live">Live</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="t212_base_env">T212 environment</Label>
              <Select name="t212_base_env" defaultValue={brokerDefaults.t212_base_env}>
                <SelectTrigger id="t212_base_env" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="demo">Demo</SelectItem>
                  <SelectItem value="live">Live</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0 space-y-0.5">
                <Label htmlFor="autopilot_enabled" className="text-sm font-medium">
                  Autopilot execution
                </Label>
                <p className="text-xs text-muted-foreground">Allow the agent to place orders automatically.</p>
              </div>
              <Switch id="autopilot_enabled" name="autopilot_enabled" defaultChecked={brokerDefaults.autopilot_enabled} />
            </div>
            <Separator />
            <Button type="submit" size="sm" disabled={working}>
              Save broker
            </Button>
          </form>
        </SectionCard>

        {/* Risk Rails */}
        <SectionCard title="Risk Rails" description="Hard limits enforced on every order">
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault()
              void saveRisk(new FormData(e.currentTarget))
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="max_single_order_notional">Max single order ($)</Label>
              <Input
                id="max_single_order_notional"
                name="max_single_order_notional"
                defaultValue={riskDefaults.max_single_order_notional}
                className="font-mono tabular-nums"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="max_daily_notional">Max daily notional ($)</Label>
              <Input
                id="max_daily_notional"
                name="max_daily_notional"
                defaultValue={riskDefaults.max_daily_notional}
                className="font-mono tabular-nums"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="max_position_weight">Max position weight (0-1)</Label>
              <Input
                id="max_position_weight"
                name="max_position_weight"
                defaultValue={riskDefaults.max_position_weight}
                className="font-mono tabular-nums"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="duplicate_order_window_seconds">Duplicate window (sec)</Label>
              <Input
                id="duplicate_order_window_seconds"
                name="duplicate_order_window_seconds"
                defaultValue={riskDefaults.duplicate_order_window_seconds}
                className="font-mono tabular-nums"
              />
            </div>
            <Separator />
            <Button type="submit" size="sm" disabled={working}>
              <ShieldCheck className="size-3.5" />
              Save risk
            </Button>
          </form>
        </SectionCard>

        {/* Invest Credentials */}
        <SectionCard
          title="Invest Credentials"
          description="Trading 212 Invest account"
          action={
            <Badge variant={config?.credentials?.invest?.configured ? 'default' : 'outline'}>
              {config?.credentials?.invest?.configured ? 'Configured' : 'Not configured'}
            </Badge>
          }
        >
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault()
              void saveInvestCredentials(new FormData(e.currentTarget))
            }}
          >
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="invest_enabled" className="text-sm font-medium">
                Enable Invest account sync
              </Label>
              <Switch
                id="invest_enabled"
                name="invest_enabled"
                defaultChecked={config?.credentials?.invest?.enabled ?? true}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="invest_api_key">Invest API key</Label>
              <Input
                id="invest_api_key"
                type="password"
                value={investKey}
                onChange={(e) => setInvestKey(e.target.value)}
                placeholder="Leave blank to keep existing"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="invest_api_secret">Invest API secret</Label>
              <Input
                id="invest_api_secret"
                type="password"
                value={investSecret}
                onChange={(e) => setInvestSecret(e.target.value)}
                placeholder="Leave blank to keep existing"
              />
            </div>
            <Separator />
            <Button type="submit" size="sm" disabled={working}>
              Save Invest credentials
            </Button>
          </form>
        </SectionCard>

        {/* Stocks ISA Credentials */}
        <SectionCard
          title="Stocks ISA Credentials"
          description="Trading 212 Stocks ISA account"
          action={
            <Badge variant={config?.credentials?.stocks_isa?.configured ? 'default' : 'outline'}>
              {config?.credentials?.stocks_isa?.configured ? 'Configured' : 'Not configured'}
            </Badge>
          }
        >
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault()
              void saveIsaCredentials(new FormData(e.currentTarget))
            }}
          >
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="isa_enabled" className="text-sm font-medium">
                Enable ISA account sync
              </Label>
              <Switch
                id="isa_enabled"
                name="isa_enabled"
                defaultChecked={config?.credentials?.stocks_isa?.enabled ?? true}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="isa_api_key">ISA API key</Label>
              <Input
                id="isa_api_key"
                type="password"
                value={isaKey}
                onChange={(e) => setIsaKey(e.target.value)}
                placeholder="Leave blank to keep existing"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="isa_api_secret">ISA API secret</Label>
              <Input
                id="isa_api_secret"
                type="password"
                value={isaSecret}
                onChange={(e) => setIsaSecret(e.target.value)}
                placeholder="Leave blank to keep existing"
              />
            </div>
            <Separator />
            <Button type="submit" size="sm" disabled={working}>
              Save ISA credentials
            </Button>
          </form>
        </SectionCard>
      </div>

      {/* Watchlist — full width */}
      <SectionCard
        title="Watchlist"
        description={`Current: ${config?.watchlist?.join(', ') || 'none'}`}
      >
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="watchlist_symbols">Symbols (comma-separated)</Label>
            <Input
              id="watchlist_symbols"
              value={watchlistText}
              onChange={(e) => setWatchlistText(e.target.value)}
              placeholder="SMCI, AVGO, META"
            />
          </div>
          <Button type="button" size="sm" onClick={() => void saveWatchlist()} disabled={working}>
            Update watchlist
          </Button>
        </div>
      </SectionCard>

      {/* Telegram Ops — full width */}
      <SectionCard
        title="Telegram Ops"
        description="Alerts and command polling"
        action={
          <Badge variant={config?.telegram.bot_token_configured ? 'default' : 'outline'}>
            {config?.telegram.bot_token_configured ? 'Token set' : 'No token'}
          </Badge>
        }
      >
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault()
            void saveTelegram(new FormData(e.currentTarget))
          }}
        >
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="flex items-center justify-between gap-3 rounded-lg border border-border/60 bg-muted/25 px-3 py-2.5">
              <Label htmlFor="telegram_enabled" className="text-sm font-medium">
                Integration
              </Label>
              <Switch id="telegram_enabled" name="telegram_enabled" defaultChecked={config?.telegram.enabled ?? false} />
            </div>
            <div className="flex items-center justify-between gap-3 rounded-lg border border-border/60 bg-muted/25 px-3 py-2.5">
              <Label htmlFor="telegram_poll_enabled" className="text-sm font-medium">
                Poll commands
              </Label>
              <Switch
                id="telegram_poll_enabled"
                name="telegram_poll_enabled"
                defaultChecked={config?.telegram.poll_enabled ?? true}
              />
            </div>
            <div className="flex items-center justify-between gap-3 rounded-lg border border-border/60 bg-muted/25 px-3 py-2.5">
              <Label htmlFor="telegram_notify_general_updates" className="text-sm font-medium">
                General updates
              </Label>
              <Switch
                id="telegram_notify_general_updates"
                name="telegram_notify_general_updates"
                defaultChecked={config?.telegram.notify_general_updates ?? true}
              />
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="telegram_bot_token">Bot token</Label>
              <Input
                id="telegram_bot_token"
                type="password"
                value={telegramToken}
                onChange={(e) => setTelegramToken(e.target.value)}
                placeholder="Leave blank to keep existing"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="telegram_chat_id">Chat ID</Label>
              <Input
                id="telegram_chat_id"
                value={telegramChatId}
                onChange={(e) => setTelegramChatId(e.target.value)}
                placeholder={config?.telegram.chat_id || 'e.g. 123456789'}
                className="font-mono tabular-nums"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="telegram_users">Allowed user IDs (comma-separated)</Label>
              <Input
                id="telegram_users"
                value={telegramUsers}
                onChange={(e) => setTelegramUsers(e.target.value)}
                placeholder={(config?.telegram.allowed_user_ids || []).join(', ')}
                className="font-mono tabular-nums"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="telegram_high_conviction_threshold">High conviction threshold (0-1)</Label>
              <Input
                id="telegram_high_conviction_threshold"
                name="telegram_high_conviction_threshold"
                defaultValue={config?.telegram.high_conviction_threshold ?? 0.68}
                className="font-mono tabular-nums"
              />
            </div>
          </div>

          <Separator />
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" size="sm" disabled={working}>
              Save Telegram
            </Button>
            <Button type="button" variant="outline" size="sm" onClick={() => void triggerTelegramTest()} disabled={working}>
              <Send className="size-3.5" />
              Send test ping
            </Button>
          </div>
        </form>
      </SectionCard>
    </div>
  )
}
