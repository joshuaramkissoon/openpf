import { useState } from "react"
import { Loader2, Send } from "lucide-react"
import { toast } from "sonner"

import { testTelegram, updateTelegram } from "@/api/client"
import { Segmented } from "@/components/kit"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { parseApiError } from "@/lib/api-error"
import type { AppConfig, TelegramConfig } from "@/types"
import { AutoNumberField, Disclosure, SettingRow, SettingsGroup, useSaver } from "./ui"

const DEFAULTS: TelegramConfig = {
  enabled: false,
  poll_enabled: true,
  chat_id: "",
  high_conviction_threshold: 0.68,
  notify_general_updates: true,
  allowed_user_ids: [],
  bot_token_configured: false,
}

/** Settings → Notifications: Telegram, set up once, then how chatty it is. */
export function NotificationsSettings({ config, onConfigChanged }: { config: AppConfig | null; onConfigChanged: () => void }) {
  const tg: TelegramConfig = { ...DEFAULTS, ...(config?.telegram ?? {}) }
  const ready = tg.bot_token_configured && Boolean(tg.chat_id)
  const savers = { on: useSaver(), bot: useSaver(), chat: useSaver(), volume: useSaver(), cmds: useSaver() }
  const [token, setToken] = useState("")
  const [chatId, setChatId] = useState("")
  const [users, setUsers] = useState("")
  const [testing, setTesting] = useState(false)

  async function save(saver: ReturnType<typeof useSaver>, patch: Partial<TelegramConfig> & { bot_token?: string }) {
    const body = {
      enabled: tg.enabled,
      poll_enabled: tg.poll_enabled,
      chat_id: tg.chat_id,
      high_conviction_threshold: tg.high_conviction_threshold,
      notify_general_updates: tg.notify_general_updates,
      allowed_user_ids: tg.allowed_user_ids,
      ...patch,
    }
    if (await saver.run(() => updateTelegram(body))) {
      onConfigChanged()
      return true
    }
    return false
  }

  async function sendTest() {
    setTesting(true)
    try {
      await testTelegram("OpenPF test: Telegram alerts are working.")
      toast.success("Test message sent — check Telegram")
    } catch (e) {
      toast.error(parseApiError(e).message || "Couldn't send the test message")
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="space-y-10">
      <SettingsGroup
        title="Telegram"
        description="Archie's alerts on your phone, and quick commands back."
        action={
          ready ? (
            <Button size="sm" variant="outline" disabled={testing} onClick={() => void sendTest()}>
              {testing ? <Loader2 className="size-3.5 animate-spin" /> : <Send className="size-3.5" />}
              Send a test
            </Button>
          ) : null
        }
      >
        <SettingRow
          label="Send alerts to Telegram"
          htmlFor="telegram_enabled"
          help={ready ? "Your bot and chat are set." : "Add a bot token and chat ID below first."}
          status={savers.on.status}
          control={
            <Switch
              id="telegram_enabled"
              checked={tg.enabled}
              disabled={!config || (!ready && !tg.enabled)}
              onCheckedChange={(v) => void save(savers.on, { enabled: Boolean(v) })}
            />
          }
        />
        <SettingRow
          label="Bot token"
          htmlFor="telegram_bot_token"
          stack
          status={savers.bot.status}
          help={
            tg.bot_token_configured
              ? "Saved. Paste a new one to replace it."
              : "In Telegram, message @BotFather → /newbot, then paste the token it gives you."
          }
        >
          <form
            className="mt-2.5 flex gap-2"
            onSubmit={async (e) => {
              e.preventDefault()
              if (token.trim() && (await save(savers.bot, { bot_token: token.trim() }))) setToken("")
            }}
          >
            <Input
              id="telegram_bot_token"
              type="password"
              autoComplete="off"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder={tg.bot_token_configured ? "•••••••• saved" : "123456:ABC-DEF…"}
              className="h-8 font-mono text-xs"
            />
            <Button type="submit" size="sm" variant="outline" disabled={!token.trim()}>
              Save
            </Button>
          </form>
        </SettingRow>
        <SettingRow
          label="Chat ID"
          htmlFor="telegram_chat_id"
          status={savers.chat.status}
          help="Where alerts go. Message your bot once, then ask @userinfobot for your numeric ID."
          control={
            <Input
              id="telegram_chat_id"
              inputMode="numeric"
              autoComplete="off"
              value={chatId}
              onChange={(e) => setChatId(e.target.value)}
              onBlur={() => {
                const v = chatId.trim()
                if (v && v !== tg.chat_id) void save(savers.chat, { chat_id: v }).then((ok) => ok && setChatId(""))
              }}
              onKeyDown={(e) => e.key === "Enter" && (e.currentTarget as HTMLInputElement).blur()}
              placeholder={tg.chat_id || "e.g. 123456789"}
              className="h-8 w-44 text-right font-mono text-sm tabular-nums"
            />
          }
        />
      </SettingsGroup>

      <SettingsGroup title="How chatty" description="Fewer, better messages by default.">
        <SettingRow
          label="Send me"
          status={savers.volume.status}
          help={
            tg.notify_general_updates
              ? "Strong ideas, plus a short update after every scheduled run."
              : "Only ideas Archie rates highly. Quiet otherwise."
          }
          control={
            <Segmented<"quiet" | "all">
              ariaLabel="How chatty"
              value={tg.notify_general_updates ? "all" : "quiet"}
              onChange={(v) => void save(savers.volume, { notify_general_updates: v === "all" })}
              options={[
                { value: "quiet", label: "Strong ideas only" },
                { value: "all", label: "Every run" },
              ]}
            />
          }
        />
      </SettingsGroup>

      <Disclosure label="Fine-tune" hint="what counts as strong, commands">
        <div className="space-y-6">
          <SettingsGroup>
            <SettingRow
              label="A strong idea is at least"
              htmlFor="telegram_threshold"
              status={savers.volume.status}
              help="Archie's own confidence in a proposal. Higher means fewer alerts."
              control={
                <AutoNumberField
                  id="telegram_threshold"
                  suffix="%"
                  className="w-28"
                  value={Math.round(tg.high_conviction_threshold * 100)}
                  parse={(raw) => {
                    const v = Number(raw.replace(/[%\s]/g, ""))
                    return Number.isFinite(v) && v >= 0 && v <= 100 ? v / 100 : "From 0 to 100."
                  }}
                  onCommit={(v) => void save(savers.volume, { high_conviction_threshold: v })}
                />
              }
            />
          </SettingsGroup>
          <SettingsGroup>
            <SettingRow
              label="Accept commands from Telegram"
              htmlFor="telegram_poll"
              status={savers.cmds.status}
              help="Lets you ask for status or approve from the chat. Only the user IDs below are listened to."
              control={
                <Switch
                  id="telegram_poll"
                  checked={tg.poll_enabled}
                  disabled={!config}
                  onCheckedChange={(v) => void save(savers.cmds, { poll_enabled: Boolean(v) })}
                />
              }
            />
            <SettingRow
              label="Allowed user IDs"
              htmlFor="telegram_users"
              help="Numbers, comma-separated (not @usernames)."
              control={
                <Input
                  id="telegram_users"
                  value={users}
                  onChange={(e) => setUsers(e.target.value)}
                  onBlur={() => {
                    const raw = users.split(",").map((x) => x.trim()).filter(Boolean)
                    if (!raw.length) return
                    const bad = raw.filter((x) => !/^\d{1,15}$/.test(x))
                    if (bad.length) return savers.cmds.fail(`Not numeric: ${bad.join(", ")}`)
                    void save(savers.cmds, { allowed_user_ids: raw.map(Number) }).then((ok) => ok && setUsers(""))
                  }}
                  onKeyDown={(e) => e.key === "Enter" && (e.currentTarget as HTMLInputElement).blur()}
                  placeholder={tg.allowed_user_ids.join(", ") || "123456789"}
                  className="h-8 w-44 text-right font-mono text-sm tabular-nums"
                />
              }
            />
          </SettingsGroup>
        </div>
      </Disclosure>
    </div>
  )
}
