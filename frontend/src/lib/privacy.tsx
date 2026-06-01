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

export const PRIVACY_MODES: PrivacyMode[] = ["off", "scramble", "blur"]

const STORAGE_KEY = "mypf.presentation.mode"
const LEGACY_KEY = "mypf.presentation.mask"

/** Read the persisted mode, migrating the old boolean key (`'1'` -> scramble). */
export function loadPrivacyMode(): PrivacyMode {
  if (typeof window === "undefined") return "off"
  const stored = window.localStorage.getItem(STORAGE_KEY)
  if (stored === "off" || stored === "scramble" || stored === "blur") return stored
  // Migrate the legacy boolean toggle: a truthy mask meant the scramble mode.
  if (window.localStorage.getItem(LEGACY_KEY) === "1") return "scramble"
  return "off"
}

/** Persist the mode and keep the legacy boolean key roughly in sync. */
export function savePrivacyMode(mode: PrivacyMode): void {
  if (typeof window === "undefined") return
  window.localStorage.setItem(STORAGE_KEY, mode)
  window.localStorage.setItem(LEGACY_KEY, mode === "off" ? "0" : "1")
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
