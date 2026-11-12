import copy
import json

import pytest

from bookpipe.application.review import ReviewRepository
from bookpipe.application.review_model import (
    ReviewModelError, ReviewModelRun, apply_decisions, plan_batches, validate_decisions,
)
from bookpipe.client import ContextFull
from bookpipe.runtime.models import JobSpec
from bookpipe.util import atomic_json
from test_review import sample_review


class Progress:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class FakeProvider:
    """Canonical JSON transport that answers with a scripted decision function."""
    provider = 'openai'
    profile_name = 'fake'
    model = 'fake-model'
    timeout = 5
    preflight_input_unit = 'tokens'

    def __init__(self, decide, context=200_000, limit=None):
        self.decide, self.context, self.limit = decide, context, limit
        self.resolved_profile = {'planning_output_reserve': 4000}
        self.requests = []

    def body(self, prompt, inputs, schema, pass_no):
        return {'prompt': prompt, 'inputs': inputs, 'schema': schema}

    def preflight(self, body, recorder=None):
        count = len(body['inputs']['review']['terms'])
        if self.limit is not None and count > self.limit:
            raise ContextFull('too large')
        return count

    def generate(self, body, directory, recorder=None):
        self.requests.append(body['inputs'])
        return json.dumps(self.decide(body['inputs'], len(self.requests))), {'usage_status': 'unavailable'}


SETTINGS = {'passes': {'1': {'max_tokens': 4000, 'temperature': 0.2}}, 'json_retries': 1, 'request_timeout': 5}


def keep(inputs, _call=0):
    return {'review': {'terms': [{'id': t['id'], 'select': t['select'], 'custom': t['custom'], 'user_notes': ''}
                                 for t in inputs['review']['terms']]}}


@pytest.fixture
def repository(tmp_path):
    path = tmp_path / 'terms.review.json'
    atomic_json(path, sample_review())
    return ReviewRepository(path)


def run(repository, provider, progress=None):
    review = repository.load()
    return review, ReviewModelRun(repository.path.parent, review, 'Book', provider, SETTINGS, progress or Progress()).run()


def test_model_decisions_import_through_the_review_transfer_path(repository):
    def decide(inputs, _call):
        value = keep(inputs)
        value['review']['terms'][0].update(custom='Kocur', user_notes='Lepiej oddaje rejestr.')
        return value

    provider = FakeProvider(decide)
    progress = Progress()
    review, document = run(repository, provider, progress)
    assert document['format'] == 'intelitex-review' and document['base_revision'] == review['_revision']
    assert provider.requests[0]['glossary_overview'][0]['source'] == review['terms'][0]['source']
    summary = repository.import_document(document, review['_revision'], apply=True)
    assert summary['changed_count'] == 1 and summary['applied']
    updated = repository.load()
    assert updated['terms'][0]['custom'] == 'Kocur'
    assert updated['terms'][0]['user_notes'] == 'Lepiej oddaje rejestr.'
    assert updated['terms'][0]['evidence'] == review['terms'][0]['evidence']
    assert all(term['reviewed'] and term['review_method'] == 'bulk' for term in updated['terms'])
    assert [e.current for e in progress.events if e.kind == 'review_model_progress'] == [0, len(review['terms'])]


def test_invalid_answer_is_retried_then_fails_without_a_document(repository):
    def partial_then_valid(inputs, call):
        value = keep(inputs)
        if call == 1:
            value['review']['terms'].pop()
        return value

    provider = FakeProvider(partial_then_valid)
    _, document = run(repository, provider)
    assert len(provider.requests) == 2 and 'VALIDATION_ERROR' in provider.requests[1]
    assert document['review']['terms']

    def always_partial(inputs, _call):
        value = keep(inputs)
        value['review']['terms'][0]['select'] = 99
        return value

    before = repository.path.read_bytes()
    with pytest.raises(ReviewModelError):
        other = FakeProvider(always_partial)
        other.resolved_profile = {'planning_output_reserve': 4000, 'model': 'other'}  # No shared checkpoint.
        run(repository, other)
    assert repository.path.read_bytes() == before


def test_context_overflow_splits_the_batch_and_completed_batches_are_reused(repository):
    provider = FakeProvider(keep, limit=1)
    review, document = run(repository, provider)
    assert [len(r['review']['terms']) for r in provider.requests] == [1] * len(review['terms'])
    # The second request sees the first decision in its overview.
    assert provider.requests[1]['glossary_overview'][0]['decided'] is True
    again = FakeProvider(keep, limit=1)
    _, repeated = run(repository, again)
    assert again.requests == [] and repeated == document


def test_validation_and_merge_rules():
    terms = sample_review()['terms']
    rows = validate_decisions({'review': {'terms': [
        {'id': t['id'], 'select': 99, 'custom': ' Własna ', 'user_notes': ''} for t in terms]}}, terms)
    assert rows[0]['custom'] == 'Własna' and rows[0]['select'] == terms[0]['select']
    with pytest.raises(ReviewModelError):
        validate_decisions({'review': {'terms': [
            {'id': t['id'], 'select': 1, 'custom': 'a\nb', 'user_notes': ''} for t in terms]}}, terms)
    document = {'review': {'terms': copy.deepcopy(terms)}}
    document['review']['terms'][0]['user_notes'] = 'mine'
    merged = apply_decisions(document, {rows[0]['id']: rows[0]})
    assert merged['review']['terms'][0]['custom'] == 'Własna'
    assert merged['review']['terms'][0]['user_notes'] == 'mine'
    assert [len(batch) for batch in plan_batches(terms * 3, 10**9, 4)] == [4, 2]
    assert len(plan_batches(terms * 3, 1, 4)) == 6


def test_job_spec_accepts_review_model(tmp_path):
    (tmp_path / 'book').mkdir()
    spec = JobSpec(str(tmp_path), 'book', str(tmp_path / 'book'), 'review_model', profile='codex-sol-medium')
    assert spec.operation == 'review_model'


def test_service_runs_the_selected_profile_and_replaces_the_review(tmp_path):
    from bookpipe.application.commands import ImportBookCommand, ReviewModelCommand
    from bookpipe.bootstrap import create_application
    from bookpipe.util import read_json
    from test_server_api import analyze

    source, root = tmp_path / 'source', tmp_path / 'book'
    source.mkdir()
    (source / 'chapter.html').write_text('<h1>Chapter 1</h1><p>Ada visits the town.</p>')
    selected = []

    def decide(inputs, _call):
        value = keep(inputs)
        value['review']['terms'][0]['custom'] = 'Adela'
        return value

    class Pool:
        def __init__(self, settings, ui, project, *, command_profile=None, command_pass_profiles=None):
            self.assignments = command_pass_profiles or {}

        def for_pass(self, pass_no):
            selected.append(self.assignments.get(pass_no))
            return FakeProvider(decide)

        def count(self, text):
            return len(text) // 4

        tokenizer_identity = {'provider': 'fake'}
        context = 200_000

        def close(self):
            pass

    progress = Progress()
    app = create_application(progress, provider_factory=Pool)
    app.projects.import_book(ImportBookCommand(root, source, chapter_mode='file', local_token_estimate=True))
    analyze(root)
    before = app.review.prepare(root)
    summary = app.review.model_review(ReviewModelCommand(root, profile='codex-sol-medium'))
    assert selected == ['codex-sol-medium'] and summary['changed_count'] == 1 and summary['applied']
    current = read_json(root / 'terms.review.json')
    assert current['terms'][0]['custom'] == 'Adela' and current['terms'][0]['review_method'] == 'bulk'
    assert current['confirmed'] is False and before['terms'][0]['custom'] == ''
    assert list((root / 'history').glob('review_before_import_*.json'))
    assert list((root / 'review-model').glob('*/*/attempt_001/attempt.json'))
    done = [e for e in progress.events if e.kind == 'review_model_completed']
    assert (done[0].current, done[0].total) == (1, 1)
