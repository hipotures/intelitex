"""Versioned section eligibility over the immutable imported source plan."""
import copy
from pathlib import Path

from .importer import _scenes_for_section, sentence_spans
from .util import PipelineError, digest, read_json


class ConfigConflict(PipelineError):
    pass


class AnalysisMembershipLocked(PipelineError):
    pass


class ModelChangeRequired(PipelineError):
    pass


def configuration(root: Path):
    path = root / 'web.config.json'
    if path.is_symlink():
        raise PipelineError('Unsafe workspace configuration.')
    value = read_json(path) if path.is_file() else {'sections': {}, 'pass_profiles': {}}
    if not isinstance(value, dict) or set(value) - {'sections', 'pass_profiles', 'accepted_settings_digest'} or not isinstance(value.get('sections'), dict) or not isinstance(value.get('pass_profiles'), dict):
        raise PipelineError('Invalid workspace configuration.')
    for section in value['sections'].values():
        if not isinstance(section, dict) or set(section) - {'processing', 'content_type', 'profiles'}:
            raise PipelineError('Invalid section configuration.')
    return value


def accepts_web_model_change(root, command):
    if command.profile is not None or command.pass_profiles or command.model is not None:
        return False
    accepted = configuration(root).get('accepted_settings_digest')
    return bool(accepted and accepted == digest(read_json(root / 'settings.json')))


def processing(book, config, ident):
    default = 'full' if any(c['id'] == ident for c in book['chapters']) else 'excluded'
    return config.get('sections', {}).get(ident, {}).get('processing', default)


def dormant_section(section, limit):
    """Use the importer's natural section/scene boundaries without token estimation."""
    chapter = copy.deepcopy(section)
    chapter['pieces'] = [{**b, 'parent_id': b['id'], 'start': 0, 'end': len(b['text'])} for b in chapter['blocks']]
    chapter['source_chars'] = len('\n\n'.join(b['text'] for b in chapter['blocks']))
    chapter['source_words'] = sum(len(b['text'].split()) for b in chapter['blocks'])
    chapter['source_tokens'] = None
    chapter['scenes'] = _scenes_for_section(chapter)
    chapter['chunk_ids'] = []
    by_id = {b['id']: b for b in chapter['pieces']}
    groups = [(chapter['scenes'], chapter['pieces'], 'whole_section')] if chapter['source_chars'] <= limit else [
        ([scene], [by_id[ident] for ident in scene['block_ids']], 'scene') for scene in chapter['scenes']]
    chunks = []
    for index, (scenes, blocks, kind) in enumerate(groups, 1):
        ident = f"{chapter['id']}_c{index:04d}"
        chapter['chunk_ids'].append(ident)
        chunks.append({'id': ident, 'chapter_id': chapter['id'], 'index_in_chapter': index,
                       'number': index, 'unit_kind': kind, 'scene_ids': [s['id'] for s in scenes],
                       'blocks': blocks, 'source_chars': len('\n\n'.join(b['text'] for b in blocks)),
                       'source_words': sum(len(b['text'].split()) for b in blocks), 'source_tokens': None,
                       'sentences': [{'id': f"{b['id']}:S{k:03d}", 'block_id': b['id'], 'text': b['text'][a:z].strip()}
                                     for b in blocks for k, (a, z) in enumerate(sentence_spans(b['text']), 1)]})
    return chapter, chunks


def effective_book(book, root, *, phase='translation', include_dormant=False, config=None):
    config = config if config is not None else configuration(root)
    if not config['sections'] and not config.get('pass_profiles'):
        return book
    result = copy.deepcopy(book)
    chapters = list(result['chapters'])
    chunks = list(result['chunks'])
    for section in result.get('non_narrative_sections', []):
        if section['id'] in config['sections'] and section['id'] not in {c['id'] for c in chapters}:
            chapter, planned = dormant_section(section, book.get('planning_settings', {}).get('whole_section_char_limit', 10000))
            chapters.append(chapter)
            chunks.extend(planned)
    eligible = {c['id'] for c in chapters if include_dormant or processing(book, config, c['id']) in (
        {'full'} if phase == 'analysis' else {'full', 'translate'})}
    chapters.sort(key=lambda c: min(b['order'] for b in c['blocks']))
    result['chapters'] = [c for c in chapters if c['id'] in eligible]
    for index, chapter in enumerate(result['chapters'], 1):
        chapter['number'] = index
    result['chunks'] = sorted([c for c in chunks if c['chapter_id'] in eligible],
                              key=lambda c: min(b['order'] for b in c['blocks']))
    for index, chunk in enumerate(result['chunks'], 1):
        chunk['number'] = index
    result['non_narrative_sections'] = [c for c in [*book['chapters'], *book.get('non_narrative_sections', [])]
                                         if c['id'] not in eligible]
    result['processing_revision'] = digest({c['id']: processing(book, config, c['id'])
                                          for c in [*book['chapters'], *book.get('non_narrative_sections', [])]})
    return result


def reconcile_membership(store, before, after):
    """Validate reuse against the existing same-section/thread continuity semantics.

    Eligibility changes do not delete evidence. A changed predecessor requires the
    existing chunk pipeline to validate/reuse individual pass fingerprints on Run.
    A re-included final needs its original checked receipt and current inputs.
    """
    from .engine import source_blocks, validate_result, response_schema
    def predecessors(book):
        chapters = {c['id']: c for c in book['chapters']}
        result = {}
        previous = []
        for chunk in book['chunks']:
            chapter = chapters[chunk['chapter_id']]
            candidates = [p for p in previous if p['chapter_id'] == chunk['chapter_id'] or
                          chapter.get('thread_id') and chapter['thread_id'] == chapters[p['chapter_id']].get('thread_id')]
            result[chunk['id']] = candidates[-1]['id'] if candidates else None
            previous.append(chunk)
        return result
    old, new = predecessors(before), predecessors(after)
    stale = []
    choices = {t['id']: t['choice'] for t in store.terms()}
    for chunk in after['chunks']:
        ident = chunk['id']
        saved = store.chunk(ident)
        if saved['status'] != 'done':
            continue
        if ident in old:
            if old[ident] != new[ident]:
                stale.append(ident)
            continue
        try:
            final = (store.root / saved['final_path']).resolve(strict=True)
            if not final.is_relative_to(store.root):
                raise ValueError('Unsafe retained checkpoint.')
            inputs = read_json(final.parent / 'inputs.json')
            prompt = (store.root / 'prompts' / 'pass5.txt').read_text(encoding='utf-8')
            fingerprint = digest({'prompt': prompt, 'inputs': inputs, 'schema': response_schema(5, inputs)})
            current = (inputs['SOURCE_BLOCKS'] == source_blocks(chunk['blocks'])
                       and inputs['PREVIOUS_CONTEXT']['source_chunk_id'] == new[ident]
                       and all(choices.get(t['id']) == t['polish'] for t in inputs['APPROVED_LEXICON'])
                       and store.job(f'pass5/{ident}', fingerprint) is not None)
            validate_result(5, store.checked_result(saved['final_path']), inputs)
            if not current:
                stale.append(ident)
        except (PipelineError, OSError, ValueError, KeyError, TypeError):
            stale.append(ident)
    store.mark_chunks_stale(stale)
