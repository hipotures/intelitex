# Design system (`web/src/styles/ds/`)

Plain CSS with no framework, no JavaScript and no build step. Intelitex imports it through `web/src/styles.css`,
and any other page can link the files directly.

| File | Contents |
| --- | --- |
| `tokens.css` | Scale tokens with the `--ds-` prefix: fonts, text sizes, weights, spacing (`--ds-space-1…11`), radii, shadow, focus ring, z-index, motion, layout widths. |
| `themes.css` | Color themes. Dark is the default on `:root`, and light applies with `<html data-theme="light">`. Semantic aliases `--success`, `--danger` and `--warning` (plus `-soft` variants) are provided. Accent hooks are listed below. |
| `base.css` | Element defaults: box sizing, body, headings, tables, native `<progress>`, scrollbars. |
| `components.css` | Primitives: `.ds-btn` (+ `-primary`, `-ghost`, `-danger`, `-compact`), `.ds-icon-btn`, `.ds-panel` (+ `-head`, `-title`, `-body`), `.ds-table-card`, `.ds-badge[data-tone]`, `.ds-count`, `.ds-status-dot[data-status]`, `.ds-notice[data-tone]`, `.ds-field` (+ `-row`, `-hint`, `-error`), `.ds-segmented`, `.ds-empty`. |
| `state.css` | The keyboard focus ring (`:focus-visible`). Load it after component CSS. |
| `gallery.html` | Static gallery of every primitive in dark and light, with keyboard states and the accent hook. |

Load order: `tokens.css`, `themes.css`, `base.css`, `components.css`, then any application CSS, then `state.css`.

## Accent hook

Set the accent per theme without writing theme selectors:

```css
:root { --ds-accent-dark: #9a4fd6; --ds-accent-soft-dark: rgba(154,79,214,.14);
        --ds-accent-light: #7d3bb8; --ds-accent-soft-light: rgba(125,59,184,.12); }
```

If these are not set, the Intelitex blue is used.

## State conventions

States use native attributes and ARIA instead of state classes:

- `:disabled` for disabled controls.
- `aria-pressed="true"` for a selected segmented option.
- `aria-invalid="true"` for an invalid field.
- `data-tone="success|accent|danger"` for badges and notices.
- `data-status="running|waiting|error|done"` for status dots.

Status dots are decorative, so always put a text label next to one.

## Reviewing the gallery

Open `web/src/styles/ds/gallery.html` directly in a browser (`file://`). No dev server is needed. To check it:

- Use the Dark/Light toggle at the top, or add `?theme=light` to the URL.
- Press `Tab` to see focus rings.
- Use the color input to try the accent hook.

`node --test tests/ds-gallery.test.mjs` (run from `web/`) does the following:

- loads the gallery from disk;
- checks that there are no console errors or failed requests and that every control has an accessible name;
- checks keyboard focus and theme switching, and that the accent hook works;
- renders every primitive next to the Intelitex component it came from and compares all computed styles in both themes. The two must be identical.

## Differences from the Intelitex originals

These differences are intentional:

- Hover never applies to a disabled control.
- Danger and icon buttons also get the disabled look.
- The primary-button hover lift is turned off under `prefers-reduced-motion`.
- `.ds-field` styles only its own child `<label>`, and adds disabled and `aria-invalid` styles.
- `.ds-segmented` marks the selected option with `aria-pressed="true"`.

## How Intelitex uses it

Intelitex renders these primitives for buttons, panels, the sections table card, status badges, count badges, status
dots, notices, empty states, form fields and the Sections filter. `../intelitex/ds-compat.css` (loaded right after
`components.css`) keeps the production behavior where the primitives differ:

- hover still styles disabled buttons;
- disabled danger and icon buttons keep full opacity;
- the hover lift stays under reduced motion;
- every label inside a field gets the field-label style;
- disabled field controls are not dimmed;
- `.active` marks the Sections filter option, because adding `aria-pressed` there would change what assistive
  technologies announce.

Deleting a block from that file is how one of those improvements would ship.

`tests/ds-states.test.mjs` forces hover, focus-visible and active through the DevTools protocol, in every theme,
both motion preferences and at 1280 and 390px. It pins the plain ds behavior and the compatibility layer, and it
compares every migrated component with the frozen legacy CSS in `tests/ds-legacy/`.
