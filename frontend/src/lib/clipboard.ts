/**
 * Copy text to the clipboard with a fallback for insecure contexts.
 *
 * `navigator.clipboard` only exists in a secure context (HTTPS or localhost).
 * The dashboard is often reached over plain HTTP on a LAN IP/hostname, where it
 * is undefined — so we fall back to a hidden <textarea> + execCommand('copy').
 * Returns true on success, false if both paths fail.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* fall through to the legacy path */
  }

  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.top = '-1000px'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}
