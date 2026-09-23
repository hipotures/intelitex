from pathlib import Path

from bookpipe.catalog import apply_estimate, load_catalog, pricing_snapshot
from bookpipe.profiles import builtin_codex_profiles, resolve_profile, with_profiles
from bookpipe.util import read_json


def test_bundled_codex_rates_cover_builtin_profiles_and_choose_context_tier():
    catalog, path = load_catalog()
    models = {profile['model'] for profile in builtin_codex_profiles().values()}
    assert models == {entry['id'] for entry in catalog['models'] if entry['provider'] == 'codex'}
    settings, _ = with_profiles(read_json(Path(__file__).parents[1] / 'settings.default.json'))
    for name in builtin_codex_profiles():
        assert resolve_profile(settings, 1, command_profile=name)[0] == name
    snapshot = pricing_snapshot(catalog, path, 'codex', 'gpt-5.6-sol')
    usage = {'status': 'reported', 'input_tokens': 100_000, 'cached_input_tokens': 20_000,
             'cache_write_input_tokens': 10_000, 'output_tokens': 10_000,
             'reasoning_output_tokens': 2_000}
    short = apply_estimate(snapshot, usage)['estimate']
    assert short['status'] == 'complete'
    assert short['amount'] == (70_000 * 4 + 20_000 * .4 + 10_000 * 5 + 10_000 * 20) / 1_000_000
    assert 'reasoning_output' not in short['unknown_components']
    usage['input_tokens'] = 300_000
    long = apply_estimate(snapshot, usage)['estimate']
    assert long['status'] == 'complete'
    assert long['amount'] == (270_000 * 8 + 20_000 * .8 + 10_000 * 10 + 10_000 * 30) / 1_000_000


def test_missing_cache_breakdown_does_not_claim_complete_or_reprice_unknown_input():
    catalog, path = load_catalog()
    snapshot = pricing_snapshot(catalog, path, 'codex', 'gpt-5.6-sol')
    estimate = apply_estimate(snapshot, {'status': 'reported', 'input_tokens': 100_000,
        'cached_input_tokens': None, 'cache_write_input_tokens': None,
        'output_tokens': 10_000, 'reasoning_output_tokens': None})['estimate']
    assert estimate['status'] == 'partial'
    assert estimate['amount'] == .2
    assert estimate['unknown_components'] == ['input_breakdown']
    assert apply_estimate(snapshot, {'status': 'reported', 'input_tokens': None,
        'output_tokens': 10_000})['estimate']['status'] == 'unknown'
