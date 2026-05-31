# MyPF Dashboard — Redesign Conventions (shadcn + Tailwind v4)

This is the single source of truth for the redesign. Every screen must look like
it came from one designer. **Do not invent new visual patterns.** Compose the
shared kit + shadcn primitives only.

## Hard rules
1. **No bespoke CSS.** No new classes in `styles.css`, no inline `style={...}`
   except for dynamic values that cannot be a Tailwind class (e.g. a chart
   colour from data, a computed width %). Style with Tailwind utilities + the
   theme tokens.
2. **Use the theme tokens, never raw colours.** Never `#3ad98f`, `text-white`,
   `bg-[#111]`, `text-gray-400`. Use: `bg-background bg-card bg-muted bg-popover`,
   `text-foreground text-muted-foreground`, `border-border`, `bg-primary
   text-primary-foreground`, and the finance semantics below.
3. **Finance semantics:** gains use `text-positive`, losses use `text-negative`,
   caution uses `text-warning`. There are matching `bg-positive/10` etc. for
   chips. Risk flags → use `<RiskBadge flag={...} />`.
4. **All numbers are mono + tabular.** Use the kit `<Money>`, `<MoneyDelta>`,
   `<Pct>`, `<PctDelta>` components, or add `font-mono tabular-nums` yourself.
   Figures must align in columns.
5. **Type scale:** page title `text-2xl font-semibold tracking-tight`; section
   title `text-sm font-semibold`; body `text-sm`; meta/labels `text-xs
   text-muted-foreground`; eyebrow/stat-label `text-[11px] uppercase
   tracking-wider text-muted-foreground`. Font is Geist (sans) / Geist Mono.
6. **Spacing rhythm:** screens use `space-y-6`; cards use `p-5` body padding;
   grids use `gap-4`. Radius via tokens (`rounded-xl` cards, `rounded-lg`
   controls). Keep it calm — generous negative space, not dense walls.
7. **Avoid AI-slop tells (impeccable):** no purple→blue gradients, no Inter, no
   nested cards-in-cards, no drop-shadow stacks (use `shadow-none` on cards;
   rely on borders + subtle bg elevation), no bouncy/elastic easing, no emoji as
   UI icons. Use `lucide-react` icons (already installed), 16px, `text-muted-
   foreground`.

## Components you compose with
- **shadcn primitives** (`@/components/ui/*`): `Button`, `Card`, `Table`
  (+TableHeader/Row/Head/Body/Cell), `Dialog`, `Sheet`, `Tabs`, `Select`,
  `Badge`, `Input`, `Label`, `DropdownMenu`, `Tooltip`, `ScrollArea`,
  `Skeleton`, `Separator`, `Switch`, `Checkbox`, `Progress`, `Alert`,
  `Popover`, and `toast` from `sonner`.
- **The kit** (`@/components/kit`):
  - `<PageHeader eyebrow title description actions />`
  - `<SectionCard title description action contentClassName noPadding>…</SectionCard>`
  - `<StatCard label value hint footer />`
  - `<Money value currency compact />`, `<MoneyDelta value currency />`,
    `<Pct value />`, `<PctDelta value />`
  - `<RiskBadge flag />`
- **Formatters** (`@/utils/format`): `formatMoney`, `formatCompactMoney`,
  `formatSignedMoney`, `formatPercent`, `formatSignedPercent`, `formatNumber`,
  `accountLabel`, `accountTag`.

## Patterns
- **A screen** = `<div className="space-y-6">` containing a stat grid and/or
  `SectionCard`s. Do not add an `<h1>` per screen (the shell renders the page
  header) unless told to — screens render their content sections.
- **Stat grids:** `grid gap-4 sm:grid-cols-2 lg:grid-cols-4` of `<StatCard>`.
- **Tables:** use shadcn `<Table>` inside a `<SectionCard noPadding>`; wrap in
  `<ScrollArea>` if many rows. Right-align numeric columns (`text-right`).
  Header cells `text-xs text-muted-foreground`. Hover row `hover:bg-muted/40`.
- **Buttons:** primary action `<Button>`; secondary `<Button variant="outline">`;
  quiet `<Button variant="ghost">`; destructive `<Button variant="destructive">`.
  Sizes `sm` for in-card actions.
- **Empty states:** centered, `text-sm text-muted-foreground`, a lucide icon
  above, one line of guidance. Never a bare "No data".
- **Loading:** `<Skeleton>` blocks matching the final layout, not spinners.
- **Markdown** (agent briefs/reports): keep using `RichMarkdown`.

## Data & logic
- **Do not change data fetching, props, or business logic.** Keep the component's
  existing props/API calls; only replace the markup/styling and improve UX
  structure (grouping, hierarchy, empty/loading states). If a prop shape is
  unclear, read `@/types` and `@/api/client`.
- Keep behaviour identical: same actions, same handlers, same data shown.

## Reference
`src/components/portfolio/` (PortfolioOverview, PositionsTable, AllocationCard)
is the gold-standard implementation. Match its density, spacing, and tone.
