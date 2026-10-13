import { createContext, useContext, type ReactNode } from "react"

/**
 * Privacy mode for sensitive portfolio figures.
 *
 * - `off`      — real numbers (default).
 * - `scramble` — non-real / obfuscated numbers (the original "presentation
 *                mode"; figures are multiplied by fixed factors upstream so the
 *                shape is plausible but the values are fake).
 * - `blur`     — the *real* values rendered behind a CSS blur + `select-none`,
 *                so nothing is misread as a different real number.
 */
export type PrivacyMode = "off" | "scramble" | "blur"

// Cycle order (P key / sidebar toggle): Off → Blur → Scramble. Blur is the more
// useful privacy mode, so it's the first stop from Off.
export const PRIVACY_MODES: PrivacyMode[] = ["off", "blur", "scramble"]

const STORAGE_KEY = "openpf.presentation.mode"
// Pre-rename keys, read once and migrated: the old mode key, and the even older
// boolean toggle ('1' meant scramble).
const OLD_MODE_KEY = "mypf.presentation.mode"
const OLD_MASK_KEY = "mypf.presentation.mask"

function isMode(value: string | null): value is PrivacyMode {
  return value === "off" || value === "scramble" || value === "blur"
}

/** Read the persisted mode, migrating the pre-rename keys on first read. */
export function loadPrivacyMode(): PrivacyMode {
  if (typeof window === "undefined") return "off"
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (isMode(stored)) return stored
    const old = window.localStorage.getItem(OLD_MODE_KEY)
    const migrated: PrivacyMode = isMode(old)
      ? old
      : window.localStorage.getItem(OLD_MASK_KEY) === "1"
        ? "scramble"
        : "off"
    window.localStorage.setItem(STORAGE_KEY, migrated)
    window.localStorage.removeItem(OLD_MODE_KEY)
    window.localStorage.removeItem(OLD_MASK_KEY)
    return migrated
  } catch {
    return "off"
  }
}

/** Persist the mode. */
export function savePrivacyMode(mode: PrivacyMode): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.setItem(STORAGE_KEY, mode)
  } catch {
    /* storage unavailable — mode lasts for this page only */
  }
}

/** Off -> Scramble -> Blur -> Off. */
export function nextPrivacyMode(mode: PrivacyMode): PrivacyMode {
  const idx = PRIVACY_MODES.indexOf(mode)
  return PRIVACY_MODES[(idx + 1) % PRIVACY_MODES.length]
}

const PrivacyContext = createContext<PrivacyMode>("off")

export function PrivacyProvider({ mode, children }: { mode: PrivacyMode; children: ReactNode }) {
  return <PrivacyContext.Provider value={mode}>{children}</PrivacyContext.Provider>
}

export function usePrivacyMode(): PrivacyMode {
  return useContext(PrivacyContext)
}

/**
 * The class string to apply to a sensitive figure given the active mode. Only
 * `blur` adds styling (scramble already swapped the data; off is untouched).
 * Layout is preserved — blur + select-none don't change box size.
 */
export function privacyBlurClass(mode: PrivacyMode): string {
  return mode === "blur" ? "select-none blur-[6px]" : ""
}

/**
 * Deterministic scale applied to money figures in `scramble` mode — preserves
 * ratios and curve shape but changes absolute amounts so screenshots look real
 * without leaking values. Shared by `obfuscateSnapshot` (App) and any component
 * that renders its own un-scrambled figures (e.g. the portfolio value chart, which
 * fetches its own series), so both stay in lockstep.
 */
export const SCRAMBLE_MONEY_FACTOR = 1.11 * 1.23

export function scrambleMoney(value: number): number {
  return Number.isFinite(value) ? value * SCRAMBLE_MONEY_FACTOR : value
}

const MODE_META: Record<PrivacyMode, { label: string; description: string }> = {
  off: { label: "Off", description: "Show real figures" },
  scramble: { label: "Scramble", description: "Obfuscate with non-real numbers" },
  blur: { label: "Blur", description: "Blur real figures (full privacy)" },
}

export function privacyModeLabel(mode: PrivacyMode): string {
  return MODE_META[mode].label
}

export function privacyModeDescription(mode: PrivacyMode): string {
  return MODE_META[mode].description
}
