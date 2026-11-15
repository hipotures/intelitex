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
