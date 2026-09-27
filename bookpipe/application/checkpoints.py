"""Validate saved pass inputs without constructing a provider or counting tokens."""
from ..engine import source_blocks
from ..util import PipelineError, read_json


def translation_checkpoints(root, store, chunk, choices):
    states, values, common = {}, {}, None
    saved = store.chunk(chunk['id'])
    for number in range(2, 6):
        key = f"pass{number}/{chunk['id']}"
        if store.get('invalidated:' + key):
            states[str(number)] = 'stale'
            continue
        selected = store.get('selected_pass:' + key)
        try:
            job = (store.job_for_path(key, saved['final_path']) if number == 5 and saved['final_path'] else
                   store.job(key, selected['fingerprint']) if isinstance(selected, dict) else
                   store.latest_job(key))
            if job is None:
                states[str(number)] = 'pending'
                continue
            path = root / job['path']
            if not (path.parent / 'inputs.json').is_file():
                # Legacy artifacts with no input receipt cannot prove P2–P4 currency.
                states[str(number)] = ('completed' if saved['status'] == 'done' else 'stale') if number == 5 else 'retained'
                continue
            # Completed chunks are committed by the writer only after P2–P5
            # validation. Edits invalidate that receipt transactionally. Verify
            # artifact hashes above, but do not reparse all historical model
            # inputs merely to render a completed section on every navigation.
            if saved['status'] == 'done':
                states[str(number)] = 'completed'
                continue
            if saved['status'] == 'pending' and isinstance(selected, dict):
                states[str(number)] = 'completed'
                continue
            inputs = read_json(path.parent / 'inputs.json')
            current_common = {k: inputs[k] for k in ('SOURCE_BLOCKS', 'APPROVED_LEXICON', 'OBSERVATIONS', 'PREVIOUS_CONTEXT')}
            # Validate the recorded chain, not a fingerprint recomputed with a
            # newer response schema. Schema evolution does not erase accepted work.
            valid = (inputs['SOURCE_BLOCKS'] == source_blocks(chunk['blocks'])
                     and all(choices.get(t['id']) == t['polish'] for t in inputs['APPROVED_LEXICON'])
                     and (common is None or common == current_common))
            for dependency, field in ((2, 'SEMANTIC_AUDIT'), (3, 'POLISH_DRAFT'), (4, 'CORRECTION_LEDGER')):
                if field in inputs:
                    valid = valid and states.get(str(dependency)) == 'completed' and inputs[field] == values.get(dependency)
            if number == 5:
                valid = valid and saved['status'] == 'done' and saved['final_path'] == job['path']
            states[str(number)] = 'completed' if valid else 'stale'
            values[number] = job['value']
            if common is None:
                common = current_common
        except (PipelineError, OSError, ValueError, KeyError, TypeError):
            states[str(number)] = 'error'
    return states
