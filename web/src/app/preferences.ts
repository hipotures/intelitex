export function preference(scope: string, key: string, fallback = '') {
  try { return localStorage.getItem(`intelitex.ui.v1.${scope}.${key}`) ?? fallback } catch { return fallback }
}
export function savePreference(scope: string, key: string, value: string) {
  try { localStorage.setItem(`intelitex.ui.v1.${scope}.${key}`, value) } catch { /* Storage is optional. */ }
  window.dispatchEvent(new CustomEvent('intelitex:preferences', { detail: scope }))
}
export function resetPreferences(scope: string) {
  try { for (const key of Object.keys(localStorage)) if (key.startsWith(`intelitex.ui.v1.${scope}.`)) localStorage.removeItem(key) } catch { /* Storage is optional. */ }
  window.dispatchEvent(new CustomEvent('intelitex:preferences', { detail: scope }))
}
export function subscribePreferences(scope: string, listener: () => void) {
  const changed = (event: Event) => {
    if (event instanceof StorageEvent) {
      if (event.key === null || event.key.startsWith(`intelitex.ui.v1.${scope}.`)) listener()
    } else if ((event as CustomEvent<string>).detail === scope) listener()
  }
  window.addEventListener('intelitex:preferences', changed)
  window.addEventListener('storage', changed)
  return () => {
    window.removeEventListener('intelitex:preferences', changed)
    window.removeEventListener('storage', changed)
  }
}
