/** A series that mostly holds still between jumps (cash, contributions): ≥ 60% of moves are exactly zero. */
export function isStepSeries(values: (number | null)[]): boolean {
  const v = values.filter((x): x is number => x != null)
  if (v.length < 8) return false
  let flat = 0
  for (let i = 1; i < v.length; i++) if (v[i] === v[i - 1]) flat++
  return flat / (v.length - 1) >= 0.6
}
