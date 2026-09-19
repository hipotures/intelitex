from __future__ import annotations

import copy
import json
import shutil
import subprocess
import threading
from pathlib import Path

import httpx
import pytest

from bookpipe.review import ReviewConflict, ReviewRepository, ReviewServer
from bookpipe.review_context import EvidenceReader
from bookpipe.store import Store
from bookpipe.util import PipelineError, atomic_json, digest, read_json


def document():
    return {
        "format_version": 1, "book_fingerprint": "book", "analysis_revision": "analysis", "confirmed": False,
        "terms": [{"id": f"T{i:06}", "source": f"Person {i}", "category": "people", "aliases": [],
                   "meaning_notes": [], "candidates": [{"number":1, "text":f"Name {i}"}],
                   "select":1, "custom":"", "reviewed":False,
                   "evidence":[{"block_id": f"B{i}", "chapter_id":"ch1", "excerpt":"A stored excerpt."}]}
                  for i in range(1,4)],
    }


@pytest.fixture
def repo(tmp_path):
    path=tmp_path/'terms.review.json'
    atomic_json(path,document())
    repo=ReviewRepository(path)
    repo.load()
    return repo


def test_bulk_reviews_only_ids_preserving_custom_notes_and_decisions(repo):
    repo.patch_term('T000001', {'custom':'A special name', 'user_notes':'Check in context'})
    before=read_json(repo.path)
    revision=repo.load()['_revision']
    result=repo.review_terms(['T000001','T000002'],revision)
    after=read_json(repo.path)
    assert result['changed_count']==2
    assert after['terms'][0]['custom']=='A special name'
    assert after['terms'][0]['user_notes']=='Check in context'
    assert after['terms'][0]['candidates']==before['terms'][0]['candidates']
    assert after['terms'][0]['review_method']=='bulk'
    assert after['terms'][2]==before['terms'][2]
    assert after['confirmed'] is False
    backups=list(repo.path.parent.glob('history/review_before_bulk_*.json'))
    assert len(backups)==1 and read_json(backups[0])==before
    assert '_revision' not in after


def test_bulk_rejects_stale_browser_revision_without_overwriting(repo):
    revision=repo.load()['_revision']
    repo.patch_term('T000002', {'custom':'New choice'})
    before=repo.path.read_bytes()
    with pytest.raises(ReviewConflict):
        repo.review_terms(['T000001'],revision)
    assert repo.path.read_bytes()==before


@pytest.mark.parametrize('ids',[[],['T000001','T000001'],['MISSING'],None,[10]])
def test_bulk_validates_entire_selection_before_writing(repo,ids):
    before=repo.path.read_bytes()
    with pytest.raises(PipelineError):
        repo.review_terms(ids,repo.load()['_revision'])
    assert repo.path.read_bytes()==before


def test_bulk_invalid_choice_does_not_approve_earlier_targets(repo):
    review=read_json(repo.path)
    review['terms'][1]['select']=99
    atomic_json(repo.path,review)
    before=repo.path.read_bytes()
    with pytest.raises(PipelineError,match='No valid'):
        repo.review_terms(['T000001','T000002'],repo.load()['_revision'])
    assert repo.path.read_bytes()==before


def test_bulk_does_not_touch_already_reviewed_target(repo):
    repo.patch_term('T000001',{'reviewed':True})
    before=read_json(repo.path)['terms'][0]
    repo.review_terms(['T000001','T000002'],repo.load()['_revision'])
    assert read_json(repo.path)['terms'][0]==before


def test_notes_still_allow_approval_and_reopening_clears_confirmation(repo):
    repo.patch_term('T000001',{'user_notes':'Not sure; inspect translated context.'})
    repo.review_terms([t['id'] for t in repo.load()['terms']],repo.load()['_revision'])
    repo.set_confirmed(True)
    assert read_json(repo.path)['confirmed'] is True
    repo.patch_term('T000001',{'reviewed':False})
    assert read_json(repo.path)['confirmed'] is False


def test_patch_revision_guard_is_opt_in_for_legacy_callers(repo):
    rev=repo.load()['_revision']
    repo.patch_term('T000001',{'custom':'First'},rev)
    with pytest.raises(ReviewConflict):
        repo.patch_term('T000002',{'custom':'Second'},rev)
    repo.patch_term('T000002',{'custom':'Second'})


def test_context_without_translation_does_not_create_database(repo):
    result=repo.evidence('T000001')
    assert result['entries'][0]['status']=='not_translated'
    assert result['entries'][0]['source_kind']=='stored_excerpt'
    assert not (repo.path.parent/'state.sqlite3').exists()


def prepare_final(root):
    block={'id':'B1','parent_id':'B1','text':'The Silver Keep rose above the valley.'}
    book={'chapters':[{'id':'ch1','blocks':[block]}],
          'chunks':[{'id':'ch1_c1','chapter_id':'ch1','blocks':[block]}]}
    atomic_json(root/'book.json',book)
    final=root/'artifacts/pass5/ch1_c1/fp/result.json'
    atomic_json(final,{'translations':[{'id':'B1','text':'A simulated translated paragraph with an inflected name.'}]})
    store=Store(root)
    store.register_chunks(book)
    store.save_job('pass5/ch1_c1','fp',final,{})
    store.finish_chunk('ch1_c1',str(final.relative_to(root)),['T000001'],'lexicon')
    with store.db:
        store.db.execute('INSERT INTO terms (id,data,choice,approved) VALUES (1,?,?,1)',('{}','Name 1'))
    store.close()
    return final


def test_context_aligns_full_source_with_committed_p5_by_id(repo):
    prepare_final(repo.path.parent)
    out=repo.evidence('T000001')['entries'][0]
    assert out['source_text']=='The Silver Keep rose above the valley.'
    assert out['source_kind']=='full_block'
    assert out['polish_text']=='A simulated translated paragraph with an inflected name.'
    assert out['stage']=='P5' and out['status']=='available'
    assert out['unit_ids']==['ch1_c1']


def test_context_warns_for_changed_choice_before_approve(repo):
    prepare_final(repo.path.parent)
    repo.patch_term('T000001',{'custom':'Replacement name'})
    out=repo.evidence('T000001')
    assert out['choice_pending_approval'] is True
    assert out['warnings']
    assert out['entries'][0]['polish_text']


def test_context_displays_old_stale_final_explicitly(repo):
    prepare_final(repo.path.parent)
    store=Store(repo.path.parent)
    with store.db:
        store.db.execute("UPDATE chunks SET status='stale'")
    store.close()
    out=repo.evidence('T000001')['entries'][0]
    assert out['status']=='stale' and out['polish_text']


def test_context_does_not_read_unregistered_newer_result(repo):
    prepare_final(repo.path.parent)
    atomic_json(repo.path.parent/'artifacts/pass5/ch1_c1/new/result.json',
                {'translations':[{'id':'B1','text':'Wrong uncommitted draft.'}]})
    assert repo.evidence('T000001')['entries'][0]['polish_text']!='Wrong uncommitted draft.'


def test_context_refuses_modified_artifact(repo):
    final=prepare_final(repo.path.parent)
    final.write_text('{"translations":[{"id":"B1","text":"Tampered"}]}')
    out=repo.evidence('T000001')['entries'][0]
    assert out['status']=='error' and out['polish_text'] is None
    assert 'checksum' in out['message']


def test_context_path_escape_is_rejected(repo,tmp_path):
    prepare_final(repo.path.parent)
    store=Store(repo.path.parent)
    with store.db:
        store.db.execute("UPDATE chunks SET final_path='../outside.json'")
    store.close()
    out=repo.evidence('T000001')['entries'][0]
    assert out['status']=='error' and 'escapes' in out['message']


def test_context_unknown_block_does_not_guess_alignment(repo):
    prepare_final(repo.path.parent)
    out=repo.evidence('T000002')['entries'][0]
    assert out['status']=='not_translated' and out['polish_text'] is None


def test_new_review_http_api(repo):
    server=ReviewServer(('127.0.0.1',0),repo)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}') as client:
            assert client.get('/assets/review_filters.js').status_code==200
            data=client.get('/api/review').json()
            assert '_revision' in data
            assert client.get('/api/terms/T000001/evidence').json()['entries'][0]['polish_text'] is None
            result=client.post('/api/review/bulk',json={'term_ids':['T000001'], 'revision':data['_revision']})
            assert result.status_code==200 and result.json()['changed_count']==1
            bad=client.post('/api/review/bulk',json={'term_ids':['T000002'], 'revision':data['_revision']})
            assert bad.status_code==409
    finally:
        server.shutdown();server.server_close();thread.join()


def test_javascript_scope_regressions():
    node=shutil.which('node')
    if not node:
        pytest.skip('Node is optional; install it to run the pure browser-logic tests.')
    path=Path(__file__).with_name('test_review_filters.cjs')
    proc=subprocess.run([node,'--test',str(path)],capture_output=True,text=True)
    assert proc.returncode==0,proc.stdout+proc.stderr
