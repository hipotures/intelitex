import { useEffect, useRef, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { z } from 'zod'
import { request, useApi } from '../../api/client'
import { compatibilitySchema, draftSchema, preflightSchema, profilesSchema, type LibraryPage } from '../../api/schema'
import { useCommand } from '../../api/mutations'
import { useConnection } from '../../realtime/coordinator'
import { Button, Cover, Empty, ErrorNote, Overlay, ProfileSwatch } from '../../components/ui/common'
import { debugTag } from '../../debug/regions'

type Source = LibraryPage['sources'][number]
const sourcePath = (source: Source, operation: 'preflight' | 'inspect') =>
  `/api/library/sources/${encodeURIComponent(source.source_id)}/${operation}`

export function SourceDetails({ source, close, add }: { source: Source; close: () => void; add: () => void }) {
  const [inspecting, setInspecting] = useState(false)
  const inspect = useApi(sourcePath(source, 'inspect'), preflightSchema, inspecting)
  return <Overlay drawer title={source.title} eyebrow="Library source" debugId="LSD" close={close}>
    <div className="drawer-body source-details"><div className="source-detail-head"><Cover title={source.title} large /><div><div className="source-detail-title">{source.title}</div><div className="subtitle">{source.creators.join(', ') || 'Unknown author'}</div><div className="subtitle">{source.language ? `Declared language: ${source.language}` : 'Language not declared'}</div></div></div>
      <p className="subtitle">This source stays in Library when you create a workspace.</p>
      <div className="source-detail-actions"><Button onClick={() => { if (inspecting) void inspect.refetch(); else setInspecting(true) }} disabled={inspect.isFetching}>Inspect source</Button><Button variant="primary" onClick={add}>Add to workspace</Button></div>
      {inspecting && <section className="source-inspect" {...debugTag('LSI')}><h3>Source inspection</h3><ErrorNote error={inspect.error} retry={() => void inspect.refetch()} />{inspect.isPending ? <Empty>Inspecting source locally…</Empty> : inspect.data && <>
        <div className="source-inspect-facts"><div><strong>{inspect.data.document_count ?? '—'}</strong><span>reading-order files, not necessarily chapters</span></div><div><strong>{inspect.data.sampled_documents ?? '—'}</strong><span>files sampled</span></div></div>
        <p className="source-inspect-note">{inspect.data.sample_word_count?.toLocaleString() ?? 'Unknown number of'} words read from those samples. The full book has not been counted.</p>
        <p className="source-inspect-note">{inspect.data.detected_language ? `Sampled text suggests ${inspect.data.detected_language.toUpperCase()} (local heuristic). Verify it before Save.` : 'The sampled text did not identify a language reliably. Choose it before Save.'}</p>
        {inspect.data.language_warning && <p className="notice">{inspect.data.language_warning}</p>}
        <h4>Representative text</h4><p className="subtitle">Short excerpts from the sampled reading-order files.</p>
        <div className="source-inspect-samples">{inspect.data.sample_previews?.length ? inspect.data.sample_previews.map(sample => <article key={sample.position}><div className="source-sample-label">File {sample.position} of {inspect.data?.document_count ?? '—'}</div>{sample.heading && <strong>{sample.heading}</strong>}<p>{sample.excerpt}{sample.excerpt.length === 320 ? '…' : ''}</p></article>) : <Empty>No readable text was found in the sampled files.</Empty>}</div>
      </>}</section>}
    </div>
  </Overlay>
}

export function SetupWorkspace({ source, close }: { source: Source; close: () => void }) {
  const preflight = useApi(sourcePath(source, 'preflight'), preflightSchema)
  const profiles = useApi('/api/profiles', profilesSchema)
  return <Overlay title={`Add ${source.title} to workspace`} eyebrow="One-time setup" debugId="WCM" close={close}>
    <ErrorNote error={preflight.error ?? profiles.error} retry={() => { void preflight.refetch(); void profiles.refetch() }} />
    {!preflight.data || !profiles.data ? <div className="modal-body"><Empty>{preflight.error || profiles.error ? 'Setup is unavailable until source preflight and profiles load.' : 'Checking source and configured profiles locally…'}</Empty></div> :
      <SetupForm key={source.source_id} source={source} preflight={preflight.data} profiles={profiles.data} close={close} />}
  </Overlay>
}

function SetupForm({ source, preflight, profiles, close }: {
  source: Source; preflight: z.infer<typeof preflightSchema>; profiles: z.infer<typeof profilesSchema>; close: () => void
}) {
  const pendingKey = `intelitex.setup-request.${source.source_id}`
  const restoreAttempt = () => {
    try {
      const value = JSON.parse(sessionStorage.getItem(pendingKey) ?? 'null') as { key?: unknown; body?: Record<string, unknown> } | null
      return value && typeof value.key === 'string' && value.body?.source_id === source.source_id && value.body.request_key === value.key
        ? { key: value.key, body: value.body } : null
    } catch { return null }
  }
  const [restored] = useState(restoreAttempt)
  const [label, setLabel] = useState('')
  const [sourceLanguage, setSourceLanguage] = useState(preflight.source_language ?? '')
  const [targetLanguage, setTargetLanguage] = useState('pl')
  const [assignments, setAssignments] = useState<Record<string, string>>(() =>
    Object.fromEntries([1, 2, 3, 4, 5].map(number => [String(number), profiles.resolved_passes[String(number)]?.name ?? profiles.default_profile])))
  const [compatibility, setCompatibility] = useState<z.infer<typeof compatibilitySchema> | null>(null)
  const [checkedSelection, setCheckedSelection] = useState<string | null>(null)
  const [compatibilityError, setCompatibilityError] = useState<unknown>(null)
  const [unknownOutcome, setUnknownOutcome] = useState(!!restored)
  const attempt = useRef<{ key: string; body: Record<string, unknown> } | null>(restored)
  const command = useCommand()
  const connection = useConnection()
  const navigate = useNavigate()
  const languagesValid = /^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/.test(sourceLanguage) && /^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/.test(targetLanguage)
  const selection = JSON.stringify([sourceLanguage, targetLanguage, assignments])
  const currentCompatibility = checkedSelection === selection ? compatibility : null
  useEffect(() => {
    if (!languagesValid) return
    const controller = new AbortController()
    void request('/api/library/compatibility', compatibilitySchema, { method: 'POST', signal: controller.signal,
      body: { source_language: sourceLanguage, target_language: targetLanguage, pass_profiles: assignments } })
      .then(value => { setCompatibility(value); setCheckedSelection(selection); setCompatibilityError(null) })
      .catch(error => { if (!(error instanceof DOMException && error.name === 'AbortError')) setCompatibilityError(error) })
    return () => controller.abort()
  }, [sourceLanguage, targetLanguage, assignments, languagesValid, selection])
  const edit = () => { if (!unknownOutcome) attempt.current = null }
  async function save() {
    if (!attempt.current) attempt.current = { key: crypto.randomUUID(), body: { source_id: source.source_id,
      source_fingerprint: preflight.source_fingerprint, source_language: sourceLanguage, target_language: targetLanguage,
      label: label.trim() || null, pass_profiles: assignments, request_key: '' } }
    const pending = attempt.current
    pending.body.request_key = pending.key
    sessionStorage.setItem(pendingKey, JSON.stringify(pending))
    const result = await command.send('/api/workspaces/setup', draftSchema, pending.body)
    if (result) { sessionStorage.removeItem(pendingKey); close(); await navigate({ to: '/work/workspaces/$workspaceId', params: { workspaceId: result.workspace_id } }) }
    else if (command.unknownOutcome()) setUnknownOutcome(true)
    else { sessionStorage.removeItem(pendingKey); attempt.current = null; setUnknownOutcome(false) }
  }
  const locked = command.pending || unknownOutcome
  return <><div className="modal-body setup-body">
    <p className="subtitle">Review these values before saving. Cancel leaves no workspace behind.</p>
    <div className="field"><label htmlFor="workspace-label">Workspace label · optional</label><input id="workspace-label" maxLength={64} value={label} disabled={locked} onChange={event => { edit(); setLabel(event.target.value) }} placeholder="For example, Astra High" /></div>
    <div className="field-row"><div className="field"><label htmlFor="source-language">Source language</label><input id="source-language" value={sourceLanguage} disabled={locked} onChange={event => { edit(); setSourceLanguage(event.target.value) }} placeholder="e.g. en" /><small>{preflight.detected_language ? `Local detection: ${preflight.detected_language}` : preflight.declared_language ? `EPUB/HTML metadata: ${preflight.declared_language}; verify it` : 'Detection uncertain; enter a language code.'}</small></div>
      <div className="field"><label htmlFor="target-language">Target language</label><select id="target-language" value={targetLanguage} disabled={locked} onChange={event => { edit(); setTargetLanguage(event.target.value) }}>{(currentCompatibility?.target_choices.length ? currentCompatibility.target_choices : ['pl']).map(language => <option key={language} value={language}>{language === 'pl' ? 'Polish (pl)' : language}</option>)}</select><small>Available for the current translation pipeline and selected profiles.</small></div></div>
    {preflight.language_warning && <div className="notice">{preflight.language_warning}</div>}
    <div className="setup-profile-list" {...debugTag('WMP')}><h3>Pipeline models</h3><p className="subtitle">Saved assignments for P1–P5. You can change models later under the workspace rules.</p>{[1, 2, 3, 4, 5].map(number => <div className="setup-profile-row" key={number}><label htmlFor={`setup-pass-${number}`}><ProfileSwatch index={profiles.profiles.find(profile => profile.name === assignments[String(number)])?.stable_palette_index} name={assignments[String(number)] ?? 'Unknown profile'} /> Pass {number}</label><select id={`setup-pass-${number}`} value={assignments[String(number)]} disabled={locked} onChange={event => { edit(); setAssignments(old => ({ ...old, [String(number)]: event.target.value })) }}>{profiles.profiles.map(profile => <option key={profile.name} value={profile.name} disabled={!profile.enabled}>{profile.name}</option>)}</select></div>)}</div>
    {!languagesValid && <div className="notice">Enter valid source and target language codes.</div>}
    {currentCompatibility?.warnings.map(message => <div className="setup-warning" key={message}>{message}</div>)}
    {currentCompatibility?.compatible && currentCompatibility.warnings.length > 0 && <div className="setup-warning">Missing language declarations are profile metadata, not setup fields. They do not prevent saving this supported language pair.</div>}
    {currentCompatibility && !currentCompatibility.compatible && <div className="notice error">This language pair is unavailable with the current pipeline or selected profiles.</div>}
    {connection !== 'Live' && <div className="notice" role="status">Save is waiting for live synchronization ({connection}). It will be available when the connection recovers.</div>}
    {unknownOutcome && <div className="notice">Save may have succeeded. Retry the same request to resolve its result; no second workspace will be created.</div>}
    <ErrorNote error={compatibilityError ?? command.error} />
  </div><div className="modal-foot"><Button onClick={close} disabled={command.pending}>{unknownOutcome ? 'Close' : 'Cancel'}</Button><Button variant="primary" onClick={() => void save()} disabled={command.disabled || (!unknownOutcome && (!languagesValid || !currentCompatibility?.compatible || !!compatibilityError))}>{unknownOutcome ? 'Retry Save' : command.pending ? 'Saving…' : connection !== 'Live' ? 'Waiting for sync…' : 'Save'}</Button></div></>
}
