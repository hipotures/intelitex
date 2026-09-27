"""Offline regression coverage for edition and dependency preservation."""
import json

import pytest

from bookpipe.application.checkpoints import translation_checkpoints
from bookpipe.application.projects import load_valid_book
from bookpipe.application.review import review_impact
from bookpipe.bootstrap import create_application
from bookpipe.infrastructure.read_store import ReadStore
from bookpipe.store import Store
from bookpipe.util import PipelineError, atomic_json, digest, read_json
from test_publishing import _import_project, _complete_translations, _change_first_final
from bookpipe.application import PublishCommand
from test_pipeline import project, server  # offline HTTP provider fixtures
from bookpipe.application import AnalyzeCommand, ApproveCommand, TranslateCommand


def test_publication_versions_preserve_old_bytes_and_receipts(tmp_path):
    app, root, _ = _import_project(tmp_path)
    _complete_translations(root)
    first = app.publishing.publish(PublishCommand(root)).status.output_path
    original = first.read_bytes()
    _change_first_final(root)
    second = app.publishing.publish(PublishCommand(root)).status.output_path
    assert first != second
    assert first.read_bytes() == original
    assert second.read_bytes() != original
    assert not app.publishing.publish(PublishCommand(root)).built
    assert first.read_bytes() == original


def test_library_delivery_is_separate_and_preserves_previous_editions(tmp_path):
    app, root, _ = _import_project(tmp_path)
    _complete_translations(root)
    library = tmp_path / 'books'
    library.mkdir()
    app.publishing.publish(PublishCommand(root))
    assert app.publishing.library_snapshot(root, library, 'book')['library_current'] is False
    first = app.publishing.export_to_library(root, library, 'book')
    original = (library / first).read_bytes()
    assert app.publishing.library_snapshot(root, library, 'book')['library_current'] is True
    _change_first_final(root)
    app.publishing.publish(PublishCommand(root))
    assert app.publishing.library_snapshot(root, library, 'book')['library_current'] is False
    second = app.publishing.export_to_library(root, library, 'book')
    assert first != second
    assert (library / first).read_bytes() == original
    assert app.publishing.export_to_library(root, library, 'book') == second


def test_adopting_legacy_cached_pass_does_not_invalidate_final(tmp_path):
    _, root, _ = _import_project(tmp_path)
    _complete_translations(root)
    store = Store(root)
    try:
        chunk = read_json(root / 'book.json')['chunks'][0]['id']
        store.record_translation_pass(f'pass2/{chunk}', 'base', 'base', {'APPROVED_LEXICON': []})
        assert store.chunk(chunk)['status'] == 'done'
        assert store.get(f'invalidated:pass5/{chunk}') is None
    finally:
        store.close()


def test_manifest_snapshot_is_invalidated_and_mutable_callers_are_isolated(tmp_path):
    app, root, _ = _import_project(tmp_path)
    files = app.web.dependencies.files
    book = load_valid_book(root, files=files)
    book['chunks'][0]['blocks'][0]['text'] = 'caller mutation'
    assert app.web.book(root)['chunks'][0]['blocks'][0]['text'] != 'caller mutation'
    disk = read_json(root / 'book.json')
    disk['chunks'][0]['blocks'][0]['text'] = 'changed on disk'
    atomic_json(root / 'book.json', disk)
    with pytest.raises(PipelineError, match='manifest was modified'):
        app.web.book(root)


def test_only_consumers_of_changed_continuity_become_stale(tmp_path):
    app, root, _ = _import_project(tmp_path)
    _complete_translations(root)
    book = read_json(root / 'book.json')
    first, second = book['chunks'][:2]
    store = Store(root)
    try:
        first_state = store.chunk(first['id'])
        second_state = store.chunk(second['id'])
        first_result = store.checked_result(first_state['final_path'])
        tail = first_result['translations'][-1]['text'][-12:]
        # Distinct artifact directories reflect real Runner storage.
        path = root / 'artifacts' / 'consumer' / 'result.json'
        atomic_json(path, store.checked_result(second_state['final_path']))
        store.save_job('pass5/' + second['id'], 'consumer', path, {})
        atomic_json(path.parent / 'inputs.json', {'PREVIOUS_CONTEXT': {
            'source_chunk_id': first['id'], 'polish': tail}})
        store.finish_chunk(second['id'], str(path.relative_to(root)), [], digest([]))
        store.finish_chunk(first['id'], first_state['final_path'], [], digest([]))
        assert store.chunk(second['id'])['status'] == 'done'
        revised = root / 'artifacts' / 'revision' / 'result.json'
        first_result['translations'][-1]['text'] += ' changed tail'
        atomic_json(revised, first_result)
        store.save_job('pass5/' + first['id'], 'revision', revised, {})
        store.finish_chunk(first['id'], str(revised.relative_to(root)), [], digest([]))
        assert store.chunk(second['id'])['status'] == 'stale'
        assert store.chunk(second['id'])['final_path'] == str(path.relative_to(root))
        assert store.chunk(first['id'])['status'] == 'done'
    finally:
        store.close()


def test_review_impact_matches_committed_lexical_dependencies(tmp_path):
    app, root, _ = _import_project(tmp_path)
    _complete_translations(root)
    store = Store(root)
    try:
        with store.db:
            store.db.execute('INSERT INTO terms(data,choice,approved) VALUES (?,?,1)',
                (json.dumps({'source': 'Relay', 'aliases': [], 'candidates': [{'text': 'Relay'}, {'text': 'Przekaźnik'}]}), 'Relay'))
            first = store.db.execute('SELECT id FROM chunks ORDER BY id LIMIT 1').fetchone()[0]
            store.db.execute('UPDATE chunks SET deps=? WHERE id=?', (json.dumps(['T000001']), first))
        review = {'terms': [{'id': 'T000001', 'custom': 'Przekaźnik'}]}
        impact = review_impact(store, review)
        assert impact == {'changed_term_ids': ['T000001'], 'affected_chunks': [
            {'chunk_id': first, 'term_ids': ['T000001']}]}
        assert store.commit_approval([(store.terms()[0], 'Przekaźnik')], {'T000001'}) == 1
        assert [r[0] for r in store.db.execute("SELECT id FROM chunks WHERE status='stale'")] == [first]
    finally:
        store.close()


def test_changed_continuity_invalidates_partial_consumer(tmp_path):
    _, root, _ = _import_project(tmp_path)
    _complete_translations(root)
    first, second = read_json(root / 'book.json')['chunks'][:2]
    store = Store(root)
    try:
        path = root / 'artifacts' / 'partial-consumer' / 'result.json'
        atomic_json(path, {'saved': True})
        inputs = {'PREVIOUS_CONTEXT': {'source_chunk_id': first['id'], 'polish': 'old tail'}}
        atomic_json(path.parent / 'inputs.json', inputs)
        key = f"pass2/{second['id']}"
        store.save_job(key, 'partial', path, {})
        with store.db:
            store.db.execute("UPDATE chunks SET status='pending',final_path=NULL WHERE id=?", (second['id'],))
        store.record_translation_pass(key, 'partial', 'partial', inputs)
        saved = store.chunk(first['id'])
        store.finish_chunk(first['id'], saved['final_path'], [], digest([]))
        assert store.get('invalidated:' + key)['reason'] == 'context_changed'
        assert path.is_file()
    finally:
        store.close()


def test_current_pass_projection_tracks_reruns_without_losing_finals(project):
    root, _, state = project
    app = create_application()
    app.pipeline.analyze(AnalyzeCommand(root))
    app.review.approve(ApproveCommand(root, accept_defaults=True))
    app.pipeline.translate(TranslateCommand(root, chunk_limit=1))
    first = app.workflow.pipeline(root)['units'][0]
    assert all(p['checkpoint_state'] == 'completed' for p in first['passes'].values())
    old_final = ReadStore(root)
    try:
        final_path = old_final.chunk(first['id'])['final_path']
        original = (root / final_path).read_bytes()
    finally:
        old_final.close()
    app.pipeline.translate(TranslateCommand(root, chunk_id=first['id'], pass_no=2, rerun=True))
    updated = app.workflow.pipeline(root)['units'][0]
    assert updated['passes']['2']['checkpoint_state'] == 'completed'
    assert all(updated['passes'][str(n)]['checkpoint_state'] == 'stale' for n in (3, 4, 5))
    assert (root / final_path).read_bytes() == original
