export function preference(scope: string, key: string, fallback = '') {
  try { return localStorage.getItem(`intelitex.ui.v1.${scope}.${key}`) ?? fallback } catch { return fallback }
}
export function savePreference(scope: string, key: string, value: string) {
  try { localStorage.setItem(`intelitex.ui.v1.${scope}.${key}`, value) } catch { /* Storage is optional. */ }
}
export function resetPreferences(scope: string) {
  try { for (const key of Object.keys(localStorage)) if (key.startsWith(`intelitex.ui.v1.${scope}.`)) localStorage.removeItem(key) } catch { /* Storage is optional. */ }
}
