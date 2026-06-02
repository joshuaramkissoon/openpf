/** Convert a 5-field cron expression to a short human-readable schedule. */
export function parseCronHuman(expr: string): string {
  const parts = expr.trim().split(/\s+/)
  if (parts.length !== 5) return expr
  const [min, hour, , , dow] = parts

  if (min.startsWith('*/') && hour === '*') {
    const n = min.slice(2)
    return `Every ${n} min`
  }

  if (min === '0' && hour.startsWith('*/')) {
    const n = hour.slice(2)
    return `Every ${n} hours`
  }

  const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

  if (/^\d+$/.test(min) && /^\d+$/.test(hour)) {
    const hh = hour.padStart(2, '0')
    const mm = min.padStart(2, '0')
    const time = `${hh}:${mm}`

    if (dow === '1-5') return `Weekdays at ${time}`

    if (/^\d$/.test(dow)) {
      const dayIdx = parseInt(dow, 10)
      const dayName = DAY_NAMES[dayIdx] ?? dow
      return `Weekly on ${dayName} at ${time}`
    }

    return `Daily at ${time}`
  }

  if (min === '0' && hour.includes(',')) {
    const times = hour
      .split(',')
      .map((h) => `${h.padStart(2, '0')}:00`)
      .join(', ')
    return `Daily at ${times}`
  }

  return expr
}
