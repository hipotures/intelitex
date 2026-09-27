from hashlib import sha256
from zipfile import ZipFile

import pytest

from bookpipe.languages import language_code
from test_library_groups import epub
from test_server_api import api
from test_runtime import request


@pytest.mark.parametrize(('raw', 'expected'), [
    ('en', 'en'), ('eng', 'en'), ('en-US', 'en'), (' EN_gb ', 'en'),
    ('pol', 'pl'), ('pl-PL', 'pl'), ('fre', 'fr'), ('fra-CA', 'fr'),
    ('deu', 'de'), ('ger', 'de'), ('zh-Hant-TW', 'zh'), ('haw', 'haw'),
    ('und', None), ('UND', None), ('zxx', None), ('unknown', None),
    ('', None), (None, None), ('not a code', None),
])
def test_language_labels(raw, expected):
    assert language_code(raw) == expected


def test_library_normalizes_labels_and_sorts_aliases_as_one_language_without_rewriting_sources(api):
    _, _, service, server = api
    root = service.imports.root
    declarations = [('one.epub', 'C English', 'en'), ('two.epub', 'A English', 'eng'),
                    ('three.epub', 'B English', 'en-US'), ('four.epub', 'D Polish', 'pol')]
    for filename, title, code in declarations:
        epub(root / filename, title=title, language=code)
    before = {name: sha256((root / name).read_bytes()).hexdigest() for name, _, _ in declarations}
    code, result = request(server, 'GET', '/api/library?sort=language&links=false&limit=40')
    assert code == 200
    known = [s for s in result['sources'] if s['language']]
    assert [s['title'] for s in known] == ['A English', 'B English', 'C English', 'D Polish']
    assert [s['language'] for s in known] == ['en', 'en', 'en', 'pl']
    with ZipFile(root / 'three.epub') as z:
        z.extractall(root / 'unpacked')
    assert service.catalog.source_metadata('unpacked')['language'] == 'en'
    assert before == {name: sha256((root / name).read_bytes()).hexdigest() for name, _, _ in declarations}


def test_preflight_preserves_declared_evidence_but_does_not_report_false_language_disagreement(api, monkeypatch):
    _, _, service, _ = api
    from bookpipe.application import source_preflight
    monkeypatch.setattr(source_preflight, 'detect_language', lambda _: ('en', .9))
    for declared in ['en', 'eng', 'en-US', 'EN_gb']:
        epub(service.imports.root / 'aliases.epub', language=declared)
        result = service.inspect_source('aliases.epub')
        assert result['declared_language'] == declared
        assert result['source_language'] == 'en'
        assert result['language_warning'] is None
    epub(service.imports.root / 'aliases.epub', language='pol')
    assert service.inspect_source('aliases.epub')['language_warning'] is not None
