import copy
import json
from contextlib import closing

from bookpipe.store import Store


def term(source, aliases=()):
    return {'source': source, 'aliases': list(aliases), 'category': 'name',
            'meaning': 'A character.', 'confidence': 'high',
            'candidates': [{'text': source, 'reason': 'Keep the source name.'}], 'evidence': ['B1']}


def test_alias_bridge_preserves_identities_and_records_review_evidence(tmp_path):
    blocks = [{'id': 'B1', 'text': 'Glinda calls Ashe Jack.', 'order': 1}]
    with closing(Store(tmp_path)) as store:
        store.merge_analysis('pass1/first', 'first', {
            'terms': [term('Pedro Ashe', ['Ashe', 'Jack Ashe']), term('Jack')], 'observations': []}, blocks, 'ch1')
        before = copy.deepcopy(store.terms())
        proposal = term('Ashe', ['Jack', 'Jack Ashe'])
        delta = {'terms': [proposal, term('Glinda')], 'observations': []}
        original = copy.deepcopy(delta)
        store.merge_analysis('pass1/next', 'next', delta, blocks, 'ch2')
        assert store.terms()[:2] == before
        assert len(store.terms()) == 3
        assert store.terms()[2]['source'] == 'Glinda'
        conflicts = [fact for fact in store.facts() if 'Unresolved identity:' in fact['statement']]
        assert len(conflicts) == 1
        conflict = conflicts[0]
        assert set(conflict['about']) == {'Pedro Ashe', 'Ashe', 'Jack Ashe', 'Jack'}
        assert conflict['chapter_id'] == 'ch2'
        assert conflict['evidence'] == ['B1']
        assert conflict['confidence'] == 'low'
        history = store.db.execute("SELECT data FROM history WHERE kind='analysis_alias_conflict'").fetchall()
        assert len(history) == 1
        assert json.loads(history[0][0])['proposal'] == proposal
        assert delta == original
        store.merge_analysis('pass1/next', 'next', delta, blocks, 'ch2')
        assert len(store.facts()) == 1
        assert len(store.terms()) == 3
