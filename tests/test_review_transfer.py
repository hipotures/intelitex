import copy

import pytest

from bookpipe.application.review import ReviewRepository, ReviewConflict
from bookpipe.application.review_transfer import ReviewImportError
from bookpipe.util import atomic_json, read_json
from test_review import sample_review


@pytest.fixture
def repository(tmp_path):
    path = tmp_path / 'terms.review.json'
    atomic_json(path, sample_review())
    return ReviewRepository(path)


def test_preview_and_atomic_import_preserve_evidence_and_back_up(repository):
    original = repository.load()
    document = repository.export_document('Książka')
    assert document['review']['book_fingerprint'] == 'book'
    document['review']['terms'][0]['custom'] = 'Kocur'
    document['review']['terms'][0]['meaning_notes'][0]['text'] = 'Poprawione znaczenie'
    document['review']['terms'].reverse()
    preview = repository.import_document(document, original['_revision'])
    assert preview['changed_count'] == 1
    assert preview['categories'] == {'people': {'changed': 1, 'total': 1}, 'place': {'changed': 0, 'total': 1}}
    assert repository.load() == original
    result = repository.import_document(document, original['_revision'], apply=True)
    assert result['applied']
    updated = repository.load()
    assert updated['terms'][0]['custom'] == 'Kocur'
    assert updated['terms'][0]['evidence'] == original['terms'][0]['evidence']
    assert updated['terms'][0]['reviewed'] is True
    assert updated['terms'][0]['review_method'] == 'bulk'
    assert {k: v for k, v in updated['terms'][1].items() if k not in {'reviewed', 'review_method'}} == {k: v for k, v in original['terms'][1].items() if k != 'reviewed'}
    assert read_json(next((repository.path.parent / 'history').glob('review_before_import_*.json'))) == {k: v for k, v in original.items() if k != '_revision'}
    with pytest.raises(ReviewConflict):
        repository.import_document(document, original['_revision'], apply=True)


@pytest.mark.parametrize('change', [
    lambda d: d['review']['terms'].pop(),
    lambda d: d['review']['terms'].append(copy.deepcopy(d['review']['terms'][0])),
    lambda d: d['review']['terms'][0].update(id='T999999'),
    lambda d: d['review']['terms'][0].update(category='place'),
    lambda d: d['review']['terms'][0].update(source='Changed'),
    lambda d: d['review']['terms'][0].update(select=True),
    lambda d: d['review']['terms'][0].update(custom=123),
    lambda d: d['review']['terms'][0]['candidates'].clear(),
    lambda d: d['review']['terms'][0]['candidates'][0].update(number=2),
    lambda d: d['review']['terms'][0]['evidence'][0].update(block_id='bad'),
    lambda d: d['review']['terms'][0].update(reviewed=True),
    lambda d: d['review']['terms'][0]['candidates'][0].update(confidence='certain'),
    lambda d: d['review'].update(book_fingerprint='another'),
    lambda d: d.update(base_revision='stale'),
])
def test_invalid_import_does_not_write(repository, change):
    document = repository.export_document('Book')
    before = repository.path.read_bytes()
    change(document)
    with pytest.raises(ReviewImportError):
        repository.import_document(document, repository.load()['_revision'], apply=True)
    assert repository.path.read_bytes() == before
    assert not (repository.path.parent / 'history').exists()


def test_unchanged_accepted_import_has_no_side_effects(repository):
    repository.review_terms([t['id'] for t in repository.load()['terms']], repository.load()['_revision'])
    document = repository.export_document('Book')
    before = repository.path.read_bytes()
    result = repository.import_document(document, document['base_revision'], apply=True)
    assert result['changed_count'] == 0
    assert repository.path.read_bytes() == before


def test_imported_candidate_aliases_and_meanings_reach_translation_memory(tmp_path):
    from contextlib import closing
    from bookpipe.application.review import execute_approval
    from bookpipe.store import Store
    from test_analysis_alias_conflicts import term

    with closing(Store(tmp_path)) as store:
        blocks = [{'id': 'B1', 'text': 'Jack spoke.', 'order': 1}]
        store.merge_analysis('pass1/one', 'one', {'terms': [term('Jack')], 'observations': []}, blocks, 'ch1')
        store.write_review('book')
        repository = ReviewRepository(tmp_path / 'terms.review.json')
        document = repository.export_document('Book')
        entry = document['review']['terms'][0]
        entry['candidates'][0]['text'] = 'Jacek'
        entry['aliases'] = ['Captain Jack']
        entry['meaning_notes'][0]['text'] = 'Corrected interpretation'
        repository.import_document(document, document['base_revision'], apply=True)
        repository.review_terms(['T000001'], repository.load()['_revision'])
        execute_approval(store, 'book', False, expected_revision=repository.load()['_revision'], confirm_review=True)
        approved = store.terms()[0]
        assert approved['choice'] == 'Jacek'
        assert approved['aliases'] == ['Captain Jack']
        assert approved['meanings'][0]['text'] == 'Corrected interpretation'
        memory, _ = store.translation_memory({'blocks': blocks, 'chapter_id': 'ch1'}, '', lambda _: 0, 1000)
        assert memory['APPROVED_LEXICON'][0]['polish'] == 'Jacek'
        assert memory['APPROVED_LEXICON'][0]['meaning_notes'][0]['text'] == 'Corrected interpretation'
