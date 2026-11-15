// Legacy markup → migrated markup pairs for tests/ds-states.test.mjs. `context` wraps both ('{}' is replaced),
// `data-target` marks the elements whose pseudo-classes are forced.
const swap = (html, d) => html.replace('data-target', 'data-target' + d)
const button = (cls, tag = 'button') => `<${tag} ${tag === 'a' ? 'href="#"' : 'type="button"'} class="${cls}" data-target>Label</${tag}>`
const both = (name, legacy, migrated, context) => ['', ' disabled'].map(d => ({ name: name + d, legacy: swap(legacy, d), migrated: swap(migrated, d), context }))

export const buttons = [
  ...both('primary', button('primary-btn'), button('ds-btn ds-btn-primary')),
  ...both('secondary', button('secondary-btn'), button('ds-btn')),
  ...both('ghost', button('ghost-btn'), button('ds-btn ds-btn-ghost')),
  ...both('ghost compact', button('ghost-btn compact library-refresh'), button('ds-btn ds-btn-ghost ds-btn-compact library-refresh')),
  ...both('danger', button('danger-btn'), button('ds-btn ds-btn-danger')),
  ...both('icon', button('icon-btn'), button('ds-icon-btn')),
  ...both('reader controls ghost', button('ghost-btn'), button('ds-btn ds-btn-ghost'), '<div class="reader-reading-area"><div class="reader-controls">{}</div></div>'),
  ...both('reader status ghost', button('ghost-btn'), button('ds-btn ds-btn-ghost'), '<div class="reader-status">{}</div>'),
  ...both('reader delete marker', button('secondary-btn reader-delete-marker'), button('ds-btn reader-delete-marker')),
  ...both('dialog close', button('icon-btn'), button('ds-icon-btn'), '<div class="modal-head">{}</div>'),
  { name: 'download link', legacy: button('secondary-btn download', 'a'), migrated: button('ds-btn download', 'a') },
]

const panel = (card, head, title, body) => `<section class="${card}" data-target><div class="${head}"><h2${title ? ` class="${title}"` : ''}>Title</h2></div><div class="${body}"><p>Body</p></div></section>`
export const panels = [
  { name: 'panel', legacy: panel('phase-detail-card', 'phase-detail-card-head', '', 'phase-detail-card-body'), migrated: panel('ds-panel', 'ds-panel-head', 'ds-panel-title', 'ds-panel-body') },
  // Phase-page context rules (pipeline.css) were renamed with the markup; the ui-snapshot views cover them.
  { name: 'panel with table', legacy: '<div class="phase-detail-card-body"><table><tbody><tr data-target><td>1</td></tr></tbody></table></div>',
    migrated: '<div class="ds-panel-body"><table><tbody><tr data-target><td>1</td></tr></tbody></table></div>' },
  { name: 'activity panel head', legacy: '<section class="phase-detail-card"><button class="phase-detail-card-head activity-card-head" aria-expanded="true" data-target><span>Log</span></button></section>',
    migrated: '<section class="ds-panel"><button class="ds-panel-head activity-card-head" aria-expanded="true" data-target><span>Log</span></button></section>' },
  { name: 'table card', legacy: '<div class="table-card"><table class="sections-table"><tbody><tr data-target><td>1</td></tr></tbody></table></div>',
    migrated: '<div class="ds-table-card"><table class="sections-table"><tbody><tr data-target><td>1</td></tr></tbody></table></div>' },
]

const phaseStates = ['done', 'active', 'error', 'partial', 'blocked', 'ready', '']
const tone = s => ({ done: 'success', active: 'accent', error: 'danger' })[s]
export const status = [
  ...phaseStates.map(s => ({ name: `phase badge ${s || 'none'}`, context: '<div class="phase-detail-head">{}</div>',
    legacy: `<span class="phase-status-badge ${s}" data-target>State</span>`,
    migrated: `<span class="ds-badge"${tone(s) ? ` data-tone="${tone(s)}"` : ''} data-state="${s}" data-target>State</span>` })),
  { name: 'count badge', context: '<div class="section-header"><div class="section-title"><h2>T</h2>{}</div></div>',
    legacy: '<span class="count-badge" data-target>3</span>', migrated: '<span class="ds-count" data-target>3</span>' },
  ...['', 'running'].map(s => ({ name: `status dot ${s || 'idle'}`, context: '<div class="workspace-state">{}Idle</div>',
    legacy: `<span class="status-dot ${s}" data-target></span>`, migrated: `<span class="ds-status-dot"${s ? ` data-status="${s}"` : ''} data-target></span>` })),
]

export const notices = [
  { name: 'notice with action', legacy: '<div class="notice"><span>Analysis is complete.</span><button type="button" class="primary-btn" data-target>Open</button></div>',
    migrated: '<div class="ds-notice"><span>Analysis is complete.</span><button type="button" class="ds-btn ds-btn-primary" data-target>Open</button></div>' },
  { name: 'error notice', legacy: '<div class="notice error" role="alert" data-target><strong>Failed</strong><p>Retry later.</p></div>',
    migrated: '<div class="ds-notice" data-tone="danger" role="alert" data-target><strong>Failed</strong><p>Retry later.</p></div>' },
  { name: 'paragraph notice', legacy: '<p class="notice preload-session-explanation" role="status" data-target>Text</p>',
    migrated: '<p class="ds-notice preload-session-explanation" role="status" data-target>Text</p>' },
  { name: 'publish problem', legacy: '<p class="notice error publish-selection-problem" role="alert" data-target>Text</p>',
    migrated: '<p class="ds-notice publish-selection-problem" data-tone="danger" role="alert" data-target>Text</p>' },
  { name: 'command notice', context: '<div class="command">{}</div>', legacy: '<div class="notice" data-target>Text</div>', migrated: '<div class="ds-notice" data-target>Text</div>' },
  { name: 'empty note', legacy: '<div class="empty-note" data-target>None</div>', migrated: '<div class="ds-empty" data-target>None</div>' },
  { name: 'source preview empty', context: '<div class="prepare-source-preview">{}</div>', legacy: '<div class="empty-note" data-target>None</div>', migrated: '<div class="ds-empty" data-target>None</div>' },
]

const setupForm = (field, row, hint, d) => `<div class="modal-body setup-body"><div class="${field}"><label for="l">Workspace label</label><input id="l" value="Astra"${d} data-target></div>
  <div class="${row}"><div class="${field}"><label for="s">Source language</label><input id="s" value="en"${d} data-target><small${hint}>Local detection: en</small></div>
  <div class="${field}"><label for="t">Target language</label><select id="t"${d} data-target><option>Polish (pl)</option></select><small${hint}>Available.</small></div></div></div>`
const drawerField = (field, d) => `<dialog open class="drawer open overlay-dialog"><div class="drawer-body"><div class="${field}"><label>Model overrides · optional</label>
  <div class="drawer-model-grid"><div class="drawer-model-slot"><label class="drawer-model-label" for="o1"><span class="profile-swatch" data-palette="0"></span>Pass 1</label>
  <select id="o1"${d} data-target><option>local</option></select></div></div></div></div></dialog>`
export const fields = [
  ...['', ' disabled'].flatMap(d => [
    { name: 'setup form' + d, legacy: setupForm('field', 'field-row', '', d), migrated: setupForm('ds-field', 'ds-field-row', ' class="ds-field-hint"', d) },
    { name: 'drawer model field' + d, legacy: drawerField('field', d), migrated: drawerField('ds-field', d) },
    { name: 'review model select' + d, context: '<div class="modal-body">{}</div>',
      legacy: `<div class="field"><label for="m">Model</label><select id="m"${d} data-target><option>Choose a model…</option></select></div>`,
      migrated: `<div class="ds-field"><label for="m">Model</label><select id="m"${d} data-target><option>Choose a model…</option></select></div>` },
  ]),
  { name: 'settings theme label', context: '<section class="settings-section">{}</section>',
    legacy: '<label class="field">Theme<select data-target><option>Dark</option></select></label>', migrated: '<label class="ds-field">Theme<select data-target><option>Dark</option></select></label>' },
  { name: 'settings checkbox', context: '<section class="settings-section">{}</section>',
    legacy: '<label class="field debug-setting"><input type="checkbox" data-target>Show panel identifiers</label>', migrated: '<label class="ds-field debug-setting"><input type="checkbox" data-target>Show panel identifiers</label>' },
  { name: 'readonly field', legacy: '<div class="field-row"><div class="field"><label>Processing</label><div class="readonly-field" data-target>Full</div></div></div>',
    migrated: '<div class="ds-field-row"><div class="ds-field"><label>Processing</label><div class="readonly-field" data-target>Full</div></div></div>' },
]
