"""Lossless, revision-bound exchange of terminology drafts with external editors."""
from __future__ import annotations

import copy

from ..util import PipelineError


class ReviewImportError(PipelineError):
    """Validation message contains field names/counts only, never book content."""


def export_document(review: dict, title: str) -> dict:
    return {
        'format': 'intelitex-review', 'version': 1, 'title': title,
        'base_revision': review['_revision'],
        'instructions': (
            'Return this complete JSON document. Edit term aliases, meaning_notes text/confidence, '
            'candidates text/reasons/confidence, select, custom '
            'and user_notes as needed. Keep all other fields unchanged. Keep the same terms, IDs, '
            'categories, candidate numbers and list lengths (except aliases and candidate reasons). '
            'Do not change source evidence or review status. custom overrides select; use an empty '
            'custom to select a candidate. Do not add prose or Markdown around the JSON.'),
        'review': {k: copy.deepcopy(v) for k, v in review.items() if k != '_revision'},
    }


def validate_document(current: dict, document: dict) -> tuple[dict, dict]:
    def fail(message):
        raise ReviewImportError(message)

    if not isinstance(document, dict) or document.get('format') != 'intelitex-review' or type(document.get('version')) is not int or document['version'] != 1:
        fail('Invalid Review export format or version.')
    if document.get('base_revision') != current['_revision']:
        fail('Review changed since export. Export the current version before editing again.')
    incoming = document.get('review')
    original = {k: v for k, v in current.items() if k != '_revision'}
    if not isinstance(incoming, dict) or set(incoming) != set(original):
        fail('Review fields were added or removed.')
    for key in original.keys() - {'terms'}:
        if incoming[key] != original[key] or type(incoming[key]) is not type(original[key]):
            fail(f'Protected Review field changed: {key}.')
    terms = incoming.get('terms')
    if not isinstance(terms, list) or len(terms) != len(current['terms']):
        fail(f'Term count must remain {len(current["terms"])}.')
    indexed = {}
    for term in terms:
        if not isinstance(term, dict) or not isinstance(term.get('id'), str) or term['id'] in indexed:
            fail('Each term must have a unique existing ID.')
        indexed[term['id']] = term
    if set(indexed) != {t['id'] for t in current['terms']}:
        fail('Term IDs were added, removed or changed.')

    def compare(old, new, path):
        # Only these descriptive/decision leaves may change. Everything else is protected.
        leaf = path[-1] if path else "term"
        if path == ('observations',):
            if new != old:
                fail('Protected source observations changed.')
            return
        if leaf in {'aliases', 'reasons'}:
            if not isinstance(new, list) or any(not isinstance(v, str) or len(v) > 100_000 for v in new):
                fail(f'Invalid string list: {leaf}.')
            return
        if type(new) is not type(old):
            fail(f'Invalid field type: {leaf}.')
        if isinstance(old, dict):
            if set(new) != set(old):
                fail('Term fields were added or removed.')
            for key in old:
                compare(old[key], new[key], (*path, key))
        elif isinstance(old, list):
            if len(old) != len(new):
                fail(f'List count changed: {leaf}.')
            for a, b in zip(old, new):
                compare(a, b, path)
        else:
            editable = path in {('custom',), ('select',), ('user_notes',)} or (
                len(path) == 2 and (
                    path[0] == 'meaning_notes' and leaf in {'text', 'confidence'} or
                    path[0] == 'candidates' and leaf in {'text', 'reason', 'confidence'}))
            if editable:
                if isinstance(new, str) and len(new) > 100_000:
                    fail(f'Field too long: {leaf}.')
                if leaf == 'confidence' and new not in {'low', 'medium', 'high'}:
                    fail('Invalid confidence; use low, medium or high.')
            elif old != new:
                fail(f'Protected term field changed: {leaf}.')

    categories = {}
    changed = 0
    ordered = []
    for old in current['terms']:
        new = indexed[old['id']]
        compare(old, new, ())
        if not new['custom'].strip() and not any(c['number'] == new['select'] and c['text'].strip() for c in new['candidates']):
            fail('Every term needs a valid selected candidate or custom form.')
        if any(not c['text'].strip() for c in new['candidates']):
            fail('Candidate text must not be empty.')
        entry = categories.setdefault(old['category'], {'total': 0, 'changed': 0})
        entry['total'] += 1
        different = old != new
        entry['changed'] += different
        changed += different
        ordered.append(copy.deepcopy(new))
    result = copy.deepcopy(original)
    result['terms'] = ordered
    return result, {'total': len(terms), 'changed_count': changed, 'categories': categories}
