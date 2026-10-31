/**
 * Copy ``value`` to the system clipboard. Returns true on success.
 *
 * Uses the async Clipboard API when available and falls back to the legacy
 * hidden-textarea + ``execCommand('copy')`` path for non-secure contexts
 * (plain-HTTP LAN deployments) where ``navigator.clipboard`` is undefined.
 * Same pattern as SearchResultList's title copy.
 */
export const copyText = async (value: string): Promise<boolean> => {
  if (!value) return false
  try {
    await navigator.clipboard.writeText(value)
    return true
  } catch {
    // fall through to the legacy path
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = value
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}
