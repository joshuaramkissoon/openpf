/**
 * Tiny static previews for preset cards — a hint of each page's main picture,
 * drawn with the viz tokens so they follow light/dark.
 */
const G = 'var(--viz-gain)'
const L = 'var(--viz-loss)'
const M = 'var(--viz-mid)'
const B = 'var(--viz-1)'
const mix = (c: string, p: number) => `color-mix(in oklab, ${c} ${p}%, var(--viz-mid))`

function Tiles({ tiles }: { tiles: [number, number, number, number, string][] }) {
  return (
    <>
      {tiles.map(([x, y, w, h, f], i) => (
        <rect key={i} x={x + 0.75} y={y + 0.75} width={w - 1.5} height={h - 1.5} rx={2} fill={f} />
      ))}
    </>
  )
}

export function PresetGlyph({ slug }: { slug: string }) {
  let body: React.ReactNode
  switch (slug) {
    case 'today':
    case 'plays-map':
      body = (
        <Tiles
          tiles={[
            [0, 0, 44, 30, mix(L, 55)],
            [0, 30, 44, 18, mix(L, 95)],
            [44, 0, 28, 24, mix(G, 45)],
            [44, 24, 28, 24, mix(L, 35)],
            [72, 0, 24, 18, mix(G, 95)],
            [72, 18, 24, 16, mix(G, 30)],
            [72, 34, 12, 14, mix(L, 70)],
            [84, 34, 12, 14, mix(G, 60)],
          ]}
        />
      )
      break
    case 'risk':
      body = [40, 30, 24, 20, 16].map((w, i) => <rect key={i} x={0} y={4 + i * 9} width={w * 2} height={5} rx={2} fill={i === 0 ? B : mix(B, 55)} />)
      break
    case 'currency':
      body = (
        <g transform="translate(48 24)">
          <circle r={17} fill="none" stroke={B} strokeWidth={7} strokeDasharray="88 107" transform="rotate(-90)" />
          <circle r={17} fill="none" stroke="var(--viz-2)" strokeWidth={7} strokeDasharray="15 107" strokeDashoffset={-90} transform="rotate(-90)" />
        </g>
      )
      break
    case 'income':
      body = [8, 14, 6, 20, 10, 24, 12, 28].map((h, i) => <rect key={i} x={6 + i * 11} y={44 - h} width={6} height={h} rx={2} fill={B} />)
      break
    case 'winners-losers':
      body = [26, 18, 10, 6, -6, -12, -20].map((v, i) => (
        <rect key={i} x={v >= 0 ? 48 : 48 + v * 1.5} y={2 + i * 6.5} width={Math.abs(v) * 1.5} height={4} rx={1.5} fill={v >= 0 ? G : L} />
      ))
      break
    case 'cash-drag':
      body = (
        <>
          <path d="M0,10 L14,8 L28,14 L44,16 L60,26 L76,30 L96,38 L96,48 L0,48 Z" fill={mix(B, 22)} />
          <path d="M0,10 L14,8 L28,14 L44,16 L60,26 L76,30 L96,38" fill="none" stroke={B} strokeWidth={2} strokeLinejoin="round" />
        </>
      )
      break
    case 'isa-allowance':
      body = (
        <>
          <rect x={0} y={20} width={96} height={8} rx={4} fill={mix(B, 18)} />
          <rect x={0} y={20} width={58} height={8} rx={4} fill={B} />
          <rect x={66} y={15} width={2} height={18} rx={1} fill="var(--foreground)" opacity={0.5} />
        </>
      )
      break
    default:
      body = <rect x={0} y={0} width={96} height={48} rx={4} fill={M} />
  }
  return (
    <svg viewBox="0 0 96 48" className="h-12 w-24" aria-hidden>
      {body}
    </svg>
  )
}
