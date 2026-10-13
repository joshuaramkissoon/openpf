import {
  Atom,
  Bitcoin,
  Coins,
  Cpu,
  Droplets,
  Globe,
  HeartPulse,
  type LucideIcon,
  Pill,
  Shield,
  Zap,
} from 'lucide-react'

import type { Conviction, PlayAccount } from './types'

/**
 * Starter plays for the New play page. Each one only prefills the form: the
 * user still presses Draft, and Archie still researches and sizes it against
 * their own book. Keep pitches factual: no returns, no promises.
 *
 * `kind: 'starter_crypto'` opens the built-in crypto plan (its own flow on the
 * connected exchange) instead of the normal draft.
 */
export type PlayTemplate = {
  id: string
  name: string
  /** One line on the card. */
  pitch: string
  /** The thesis sentence that goes into the box. */
  thesis: string
  Icon: LucideIcon
  /** Index into the chart palette (`--chart-N`), so templates feel like plays. */
  color: number
  risk: 'lower' | 'medium' | 'higher'
  horizonMonths: number
  conviction: Conviction | null
  account?: PlayAccount
  kind?: 'draft' | 'starter_crypto'
  /** Shown instead of the horizon chip when the template has its own schedule. */
  horizonLabel?: string
}

export const PLAY_TEMPLATES: PlayTemplate[] = [
  {
    id: 'global-core',
    name: 'Global index core',
    pitch: 'One low-cost world fund as the steady centre of your book.',
    thesis:
      'I want a simple, low-cost core: one global all-world index fund I can keep adding to for years, instead of trying to pick winners.',
    Icon: Globe,
    color: 1,
    risk: 'lower',
    horizonMonths: 60,
    conviction: 'high',
    account: 'stocks_isa',
  },
  {
    id: 'dividend-income',
    name: 'Dividend income',
    pitch: 'Companies with a long record of paying, and raising, their dividends.',
    thesis:
      'I want a growing stream of income: companies and funds with a long record of paying and raising dividends, without chasing the highest yields.',
    Icon: Coins,
    color: 2,
    risk: 'lower',
    horizonMonths: 60,
    conviction: 'medium',
    account: 'stocks_isa',
  },
  {
    id: 'ai-infrastructure',
    name: 'AI infrastructure',
    pitch: 'The chips, networking and power that AI datacentres run on.',
    thesis:
      'AI spending keeps growing for years. I want the picks and shovels (chipmakers, networking, datacentre power and cooling), not the model labs themselves.',
    Icon: Cpu,
    color: 3,
    risk: 'higher',
    horizonMonths: 24,
    conviction: 'medium',
  },
  {
    id: 'clean-power',
    name: 'Clean energy & nuclear',
    pitch: 'Firm, low-carbon power for grids that need much more of it.',
    thesis:
      'Electricity demand is rising again. Nuclear, grid equipment and renewables suppliers should benefit as countries build firm, low-carbon power.',
    Icon: Atom,
    color: 4,
    risk: 'higher',
    horizonMonths: 60,
    conviction: 'medium',
  },
  {
    id: 'healthcare-innovation',
    name: 'Healthcare innovation',
    pitch: 'New treatments and the toolmakers behind them, spread across many names.',
    thesis:
      'Healthcare is in a burst of innovation: obesity drugs, gene and cell therapies, and the toolmakers behind them. I want a spread, not one drug trial.',
    Icon: HeartPulse,
    color: 5,
    risk: 'medium',
    horizonMonths: 24,
    conviction: 'medium',
  },
  {
    id: 'defence',
    name: 'Defence',
    pitch: 'Europe and its allies are rebuilding their armed forces.',
    thesis:
      'Europe is rearming for a decade. Defence primes and their suppliers should see years of steady orders as budgets rise.',
    Icon: Shield,
    color: 6,
    risk: 'medium',
    horizonMonths: 24,
    conviction: 'medium',
  },
  {
    id: 'starter-crypto',
    name: 'Starter crypto',
    pitch: 'A little Bitcoin each week, capped at a small slice of your book.',
    thesis: '',
    Icon: Bitcoin,
    color: 7,
    risk: 'higher',
    horizonMonths: 3,
    horizonLabel: '8–12 weeks',
    conviction: 'low',
    account: 'crypto',
    kind: 'starter_crypto',
  },
]

/** Quick ideas under the box: a short theme on the chip, a full sentence when clicked. */
export const EXAMPLE_IDEAS: { title: string; idea: string; Icon: LucideIcon }[] = [
  {
    title: 'Nuclear comeback',
    Icon: Atom,
    idea: 'Nuclear is back: uranium supply is tight, small reactors are getting funded, and AI datacentres need firm, carbon-free power.',
  },
  {
    title: 'AI power & cooling',
    Icon: Zap,
    idea: 'The AI build-out is held back by power and cooling, not chips. Grid equipment, cooling and power suppliers win whichever model lab does.',
  },
  {
    title: 'GLP-1 winners',
    Icon: Pill,
    idea: 'GLP-1 obesity drugs keep expanding. Back the makers and their supply chain (fill-finish, injector pens), and avoid the obvious losers.',
  },
  {
    title: 'Water scarcity',
    Icon: Droplets,
    idea: 'Clean water is getting scarcer and pricier. Utilities, pipe and treatment makers, and metering companies should see steady demand.',
  },
]

/** Cycled through as the placeholder while the box is empty. */
export const PLACEHOLDER_IDEAS = [
  'Europe is rearming for a decade, and its defence suppliers will be busy…',
  'Japanese companies are finally rewarding shareholders…',
  'Ageing populations will spend far more on care and medical devices…',
  'Copper is the bottleneck for electrification…',
  'Payments keep moving from cash to cards and phones…',
]

export const HORIZONS: { months: number; short: string; long: string }[] = [
  { months: 6, short: '6m', long: '6 months' },
  { months: 12, short: '1y', long: '1 year' },
  { months: 24, short: '2y', long: '2 years' },
  { months: 60, short: '5y', long: '5 years' },
]

export const RISK_LABEL: Record<PlayTemplate['risk'], string> = {
  lower: 'Lower risk',
  medium: 'Medium risk',
  higher: 'Higher risk',
}

export function horizonLabel(months: number): string {
  return HORIZONS.find((h) => h.months === months)?.long ?? `${months} months`
}
