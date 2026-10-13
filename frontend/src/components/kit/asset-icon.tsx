import { cn } from "@/lib/utils"

/**
 * One mark per asset: a bundled coin icon for common crypto, otherwise a
 * deterministic monogram tile tinted from the ticker. Nothing is fetched from a
 * third party: a logo CDN would learn what the owner holds and trades.
 */

const COIN_URLS: Record<string, string> = Object.fromEntries(
  Object.entries(
    import.meta.glob<string>("../../assets/crypto/*.svg", { eager: true, query: "?url", import: "default" }),
  ).map(([path, url]) => [path.split("/").pop()!.replace(".svg", "").toUpperCase(), url]),
)

/** Names for the coins we bundle, for when the API didn't send one. */
export const COIN_NAMES: Record<string, string> = {
  BTC: "Bitcoin",
  ETH: "Ether",
  SOL: "Solana",
  USDC: "USD Coin",
  USDT: "Tether",
  XRP: "XRP",
  ADA: "Cardano",
  LINK: "Chainlink",
  LTC: "Litecoin",
  DOT: "Polkadot",
  DOGE: "Dogecoin",
  BNB: "BNB",
  AVAX: "Avalanche",
  MATIC: "Polygon",
}

function hue(ticker: string) {
  let h = 0
  for (const ch of ticker) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return h % 360
}

/** "AAPL" → "A", "BP" → "BP", "VUSA.L" → "V", "BRK.B" → "B". */
function monogram(ticker: string) {
  const base = ticker.split(/[._\s-]/)[0].replace(/[^A-Za-z0-9]/g, "").toUpperCase()
  if (!base) return "?"
  return base.length <= 2 ? base : base[0]
}

export function AssetIcon({
  symbol,
  crypto = false,
  size = 32,
  className,
}: {
  symbol: string
  /** Crypto assets use the bundled coin mark when there is one. */
  crypto?: boolean
  /** Pixel size of the square tile. */
  size?: number
  className?: string
}) {
  const sym = symbol.toUpperCase()
  const coin = crypto ? COIN_URLS[sym] : undefined
  if (coin) {
    return (
      <img
        src={coin}
        alt=""
        aria-hidden
        width={size}
        height={size}
        draggable={false}
        className={cn("shrink-0 select-none rounded-full", className)}
        style={{ width: size, height: size }}
      />
    )
  }
  const text = monogram(sym)
  return (
    <span
      aria-hidden
      className={cn(
        "asset-mono inline-flex shrink-0 select-none items-center justify-center font-semibold leading-none tracking-tight",
        crypto ? "rounded-full" : "rounded-[28%]",
        className,
      )}
      style={
        {
          width: size,
          height: size,
          fontSize: Math.round(size * (text.length > 1 ? 0.36 : 0.44)),
          "--asset-h": hue(sym),
        } as React.CSSProperties
      }
    >
      {text}
    </span>
  )
}
