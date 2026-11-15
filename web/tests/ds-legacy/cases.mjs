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
