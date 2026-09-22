import { useEffect, useState } from 'react'
import { Outlet, useLocation, useNavigate } from '@tanstack/react-router'
import { Scope, useApi } from '../api/client'
import { capabilitiesSchema, profilesSchema } from '../api/schema'
import { Realtime, useLive } from '../realtime/coordinator'
import { Button, ErrorNote, Overlay, ProfileSwatch } from '../components/ui/common'
import { preference, resetPreferences, savePreference } from './preferences'
export function Shell() {
  const capabilities = useApi('/api/capabilities', capabilitiesSchema)
  if (!capabilities.data) return <main className="main"><h1>Intelitex</h1><ErrorNote error={capabilities.error} retry={() => void capabilities.refetch()} />{!capabilities.error && <p>Connecting to the local server…</p>}</main>
  return <Scope.Provider value={capabilities.data.scope_id}><ConnectedShell key={capabilities.data.scope_id} scope={capabilities.data.scope_id} importEnabled={capabilities.data.import_enabled} /></Scope.Provider>
}
function ConnectedShell({ scope, importEnabled }: { scope: string; importEnabled: boolean }) {
  const location = useLocation()
  const navigate = useNavigate()
  const [settings, setSettings] = useState(false)
  const [notice,setNotice] = useState('')
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>
    const receive = (event: Event) => { setNotice((event as CustomEvent<string>).detail); clearTimeout(timer); timer = setTimeout(() => setNotice(''),4000) }
    window.addEventListener('intelitex:notice',receive)
    return () => { window.removeEventListener('intelitex:notice',receive); clearTimeout(timer) }
  }, [])
  const [theme, setTheme] = useState(() => preference(scope,'theme', window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'))
  const mode = location.pathname.startsWith('/reader') ? 'reader' : 'work'
  useEffect(() => { document.documentElement.dataset.theme = theme; savePreference(scope,'theme',theme) }, [scope,theme])
  useEffect(() => { savePreference(scope,`${mode}.route`,location.href) }, [scope,mode,location.href])
  function switchMode(next: 'work'|'reader') {
    const target = preference(scope,`${next}.route`, `/${next}`)
    void navigate({ href: target.startsWith(`/${next}`) && !target.startsWith('//') ? target : `/${next}` })
  }
  return <div className="app"><Realtime /><header className="topbar"><div className="brand"><div className="brand-mark">IX</div><div className="brand-name">Intelitex</div></div><div className="mode-switch" aria-label="Application mode"><button className={mode === 'work' ? 'active' : ''} onClick={() => switchMode('work')}>Work</button><button className={mode === 'reader' ? 'active' : ''} onClick={() => switchMode('reader')}>Reader</button></div><div className="top-actions"><ConnectionStatus /><Button variant="icon" aria-label="Toggle theme" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>◐</Button><Button variant="icon" aria-label="Settings" onClick={() => setSettings(true)}>⚙</Button></div></header><Outlet />{notice && <div className="toast show" role="status">{notice}</div>}{settings && <SettingsDialog scope={scope} importEnabled={importEnabled} theme={theme} setTheme={setTheme} close={() => setSettings(false)} />}</div>
}
function ConnectionStatus() { const live = useLive(); return <span className={`connection-status ${live.connection === 'Live' ? 'live' : ''}`} role="status">{live.connection}</span> }
function SettingsDialog({ scope, importEnabled, theme, setTheme, close }: { scope: string; importEnabled: boolean; theme: string; setTheme: (s: string) => void; close: () => void }) {
  const [tab,setTab] = useState(() => preference(scope,'settings.tab','paths'))
  const profiles = useApi('/api/profiles',profilesSchema,tab === 'models')
  const [reset,setReset] = useState(false)
  return <Overlay title="Settings" eyebrow="Application" close={close}><div className="settings-tabs" role="tablist">{['paths','models','interface'].map(t => <button role="tab" aria-selected={tab === t} className={tab === t ? 'active' : ''} key={t} onClick={() => { setTab(t); savePreference(scope,'settings.tab',t) }}>{t[0]!.toUpperCase() + t.slice(1)}</button>)}</div><div className="modal-body">
    {tab === 'paths' && <section className="settings-section"><h3>Server configuration</h3><dl className="phase-kv"><dt>Workspace directory</dt><dd>Configured by the running server</dd><dt>Source library</dt><dd>{importEnabled ? 'Configured by the running server' : 'Not configured'}</dd></dl><p className="subtitle">Directories are set when starting Intelitex. Restart the server to change them.</p></section>}
    {tab === 'models' && <section className="settings-section"><h3>Model profiles</h3><ErrorNote error={profiles.error} retry={() => void profiles.refetch()} /><div className="model-config-list">{profiles.data?.profiles.map(p => <div className="model-config-row" key={p.name}><div><div className="model-config-name"><ProfileSwatch index={p.stable_palette_index} name={p.name} /> {p.name}</div><div className="model-config-desc">{p.provider ?? '—'} · {p.model ?? '—'}</div></div><span className="health">{p.enabled ? 'Configured · not tested' : 'Disabled'}</span><Button disabled title="This backend does not expose model diagnostics">Test unavailable</Button></div>)}</div><p className="subtitle">Edit profile definitions in server configuration. Workspace and section pass assignments are available in each workspace.</p></section>}
    {tab === 'interface' && <section className="settings-section"><h3>Appearance</h3><label className="field">Theme<select value={theme} onChange={e => setTheme(e.target.value)}><option value="dark">Dark</option><option value="light">Light</option></select></label><h3>Browser preferences</h3><p className="subtitle">Reset theme, filters, panel preferences and reading positions for this server.</p><Button onClick={() => setReset(true)}>Reset interface preferences</Button>{reset && <div className="notice"><p>Reset these browser preferences?</p><Button onClick={() => { resetPreferences(scope); setTheme('dark'); setReset(false) }}>Reset preferences</Button><Button onClick={() => setReset(false)}>Cancel</Button></div>}</section>}
  </div><div className="modal-foot"><Button onClick={close}>Close</Button></div></Overlay>
}
