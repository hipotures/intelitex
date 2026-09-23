import { useEffect, useState } from 'react'
import { Outlet, useLocation, useNavigate } from '@tanstack/react-router'
import { Scope, useApi } from '../api/client'
import { capabilitiesSchema, profilesSchema } from '../api/schema'
import { Realtime, useLive } from '../realtime/coordinator'
import { Button, ErrorNote, Overlay, ProfileSwatch } from '../components/ui/common'
import { preference, resetPreferences, savePreference } from './preferences'
import { debugTag } from '../debug/regions'
export function Shell() {
  const capabilities = useApi('/api/capabilities', capabilitiesSchema)
  if (!capabilities.data) return <div className="app"><header className="topbar" {...debugTag('HDR')}><div className="brand"><div className="brand-mark">IX</div><div className="brand-name">Intelitex</div></div></header><main className="main connecting-screen" {...debugTag('BLS')} aria-busy={!capabilities.error}><div className="eyebrow">Intelitex</div><h1>Connecting to the local server</h1><p className="subtitle">Waiting for the application state before opening this workspace.</p><ErrorNote error={capabilities.error} retry={() => void capabilities.refetch()} /><div className="connecting-panel" aria-hidden="true"><span /><span /><span /></div></main></div>
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
  const [debug, setDebug] = useState(() => preference(scope, 'debug', 'false') === 'true')
  const mode = location.pathname.startsWith('/reader') ? 'reader' : 'work'
  useEffect(() => { document.documentElement.dataset.theme = theme; savePreference(scope,'theme',theme) }, [scope,theme])
  useEffect(() => { document.documentElement.dataset.uiDebug = debug ? 'on' : 'off'; savePreference(scope, 'debug', String(debug)) }, [scope,debug])
  useEffect(() => { savePreference(scope,`${mode}.route`,location.href) }, [scope,mode,location.href])
  function switchMode(next: 'work'|'reader') {
    const target = preference(scope,`${next}.route`, `/${next}`)
    void navigate({ href: target.startsWith(`/${next}`) && !target.startsWith('//') ? target : `/${next}` })
  }
  return <div className="app"><Realtime /><header className="topbar" {...debugTag('HDR')}><div className="brand"><div className="brand-mark">IX</div><div className="brand-name">Intelitex</div></div><div className="mode-switch" aria-label="Application mode"><button className={mode === 'work' ? 'active' : ''} onClick={() => switchMode('work')}>Work</button><button className={mode === 'reader' ? 'active' : ''} onClick={() => switchMode('reader')}>Reader</button></div><div className="top-actions"><ConnectionStatus /><Button variant="icon" aria-label="Toggle theme" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>◐</Button><Button variant="icon" aria-label="Settings" onClick={() => setSettings(true)}>⚙</Button></div></header><Outlet />{notice && <div className="toast show" role="status">{notice}</div>}{settings && <SettingsDialog scope={scope} importEnabled={importEnabled} theme={theme} setTheme={setTheme} debug={debug} setDebug={setDebug} close={() => setSettings(false)} />}</div>
}
function ConnectionStatus() { const live = useLive(); return <span className={`connection-status ${live.connection === 'Live' ? 'live' : ''}`} role="status">{live.connection}</span> }
function SettingsDialog({ scope, importEnabled, theme, setTheme, debug, setDebug, close }: { scope: string; importEnabled: boolean; theme: string; setTheme: (s: string) => void; debug: boolean; setDebug: (value: boolean) => void; close: () => void }) {
  const [tab,setTab] = useState(() => preference(scope,'settings.tab','paths'))
  const profiles = useApi('/api/profiles',profilesSchema,tab === 'models')
  const [reset,setReset] = useState(false)
  return <Overlay title="Settings" eyebrow="Application" debugId="SET" close={close}><div className="settings-tabs" role="tablist" {...debugTag('STB')}>{['paths','models','interface','debug'].map(t => <button role="tab" aria-selected={tab === t} className={tab === t ? 'active' : ''} key={t} onClick={() => { setTab(t); savePreference(scope,'settings.tab',t) }}>{t[0]!.toUpperCase() + t.slice(1)}</button>)}</div><div className="modal-body">
    {tab === 'paths' && <section className="settings-section" {...debugTag('SPA')}><h3>Server configuration</h3><dl className="phase-kv"><dt>Workspace directory</dt><dd>Configured by the running server</dd><dt>Source library</dt><dd>{importEnabled ? 'Configured by the running server' : 'Not configured'}</dd></dl><p className="subtitle">Directories are set when starting Intelitex. Restart the server to change them.</p></section>}
    {tab === 'models' && <section className="settings-section" {...debugTag('SPM')}><h3>Model profiles</h3><ErrorNote error={profiles.error} retry={() => void profiles.refetch()} /><div className="model-config-list">{profiles.data?.profiles.map(p => <div className="model-config-row" key={p.name} {...debugTag('MPR', p.name)}><div><div className="model-config-name"><ProfileSwatch index={p.stable_palette_index} name={p.name} /> {p.name}</div><div className="model-config-desc">{p.provider ?? '—'} · {p.model ?? '—'}</div></div><span className="health">{p.enabled ? 'Configured · not tested' : 'Disabled'}</span><Button disabled title="This backend does not expose model diagnostics">Test unavailable</Button></div>)}</div><p className="subtitle">Edit profile definitions in server configuration. Workspace and section pass assignments are available in each workspace.</p></section>}
    {tab === 'interface' && <section className="settings-section" {...debugTag('SPI')}><h3>Appearance</h3><label className="field">Theme<select value={theme} onChange={e => setTheme(e.target.value)}><option value="dark">Dark</option><option value="light">Light</option></select></label><h3>Browser preferences</h3><p className="subtitle">Reset theme, filters, panel preferences and reading positions for this server.</p><Button onClick={() => setReset(true)}>Reset interface preferences</Button>{reset && <div className="notice"><p>Reset these browser preferences?</p><Button onClick={() => { resetPreferences(scope); setTheme('dark'); setDebug(false); setReset(false) }}>Reset preferences</Button><Button onClick={() => setReset(false)}>Cancel</Button></div>}</section>}
    {tab === 'debug' && <section className="settings-section" {...debugTag('SPD')}><h3>UI Debug identifiers</h3><label className="field debug-setting"><input type="checkbox" checked={debug} onChange={e => setDebug(e.target.checked)} />Show panel identifiers</label><p className="subtitle">Identifiers appear on major interface regions in this browser only.</p></section>}
  </div><div className="modal-foot"><Button onClick={close}>Close</Button></div></Overlay>
}
