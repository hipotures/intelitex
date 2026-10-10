"""Explicit public projections. Never serialize project/provider objects wholesale."""
import re


def safe_text(value):
    """Defense for local path-valued model names and legacy retained metadata."""
    if not isinstance(value, str):
        return value
    if value.startswith(('/', '~/', '\\\\')) or re.match(r'^[A-Za-z]:[\\/]', value):
        return '[local path]'
    return re.sub(r'(?<![\w:])/(?:home|Users|tmp|var|root|etc|opt|mnt|private|srv|workspace)/[^\s\"<>]*',
                  '[local path]', value)


def safe_json(value):
    if isinstance(value, dict):
        return {safe_text(k): safe_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_json(v) for v in value]
    return safe_text(value)


def pick(value, fields):
    return {key: value[key] for key in fields.split() if key in value}


def publication(value):
    if value is None:
        return None
    return {'state': value.state, 'translation_complete': value.translation_complete,
            'current': value.current, 'target_language': value.target_language,
            'publication_fingerprint': value.publication_fingerprint,
            'generated_at': value.generated_at, 'generated_by': value.generated_by,
            'title': value.title, 'creators': list(value.creators), 'source_language': value.source_language,
            'last_error': 'Publication unavailable.' if value.last_error else None,
            'last_failure': 'Publication failed.' if value.last_failure else None,
            'filename': value.output_path.name if value.current and value.output_path else None,
            'size_bytes': value.output_path.stat().st_size if value.current and value.output_path else None,
            'checks': list(value.validation) if value.current else []}


def status(value):
    return {'title': value.title, 'series_id': value.series_id, 'series_volume': value.series_volume,
            'narrative_sections': value.narrative_sections,
            'chunks': [{'id': c.id, 'status': c.status} for c in value.chunks],
            'analysis_complete': value.analysis_complete, 'approved': value.approved,
            'term_count': value.term_count, 'retained_candidates': value.retained_candidates,
            'chapters': [{'id': c.id, 'title': c.title, 'completed': c.completed, 'total': c.total} for c in value.chapters],
            'translation_complete': value.translation_complete, 'publication': publication(value.publication)}


def term(value):
    result = pick(value, 'id source aliases category select custom reviewed user_notes review_method series_review_required')
    result.setdefault('reviewed', False)
    result.setdefault('user_notes', '')
    result['meaning_notes'] = [pick(v, 'text confidence evidence') for v in value.get('meaning_notes', [])]
    result['candidates'] = [pick(v, 'number text reason reasons confidence evidence') for v in value.get('candidates', [])]
    result['observations'] = [pick(v, 'id about kind statement confidence evidence available_from_order') for v in value.get('observations', [])]
    result['evidence'] = [pick(v, 'chapter_id block_id excerpt order') for v in value.get('evidence', [])]
    return result


def review(value):
    result = pick(value, '_revision revision confirmed book_fingerprint analysis_revision changed_count')
    if 'summary' in value:
        result['summary'] = pick(value['summary'], 'total reviewed unreviewed uncertain noted categories confirmed')
    if 'terms' in value:
        result['terms'] = [term(t) for t in value['terms']]
    if 'term' in value:
        result['term'] = term(value['term'])
    return result


def evidence(value):
    return {**pick(value, 'term_id warnings choice_pending_approval'),
            'entries': [{**pick(v, 'block_id chapter_id source_text source_kind polish_text stage status unit_ids'),
                         **({'message': 'Checkpoint evidence unavailable.'} if v.get('message') else {})}
                        for v in value['entries']]}


def markers(value):
    result = pick(value, 'format_version book_fingerprint _revision revision created deleted')
    fields = 'id chapter_id block_id start end text'
    if 'markers' in value:
        result['markers'] = [pick(v, fields) for v in value['markers']]
    if 'marker' in value:
        result['marker'] = pick(value['marker'], fields)
    return result


def reader(value, kind):
    # ReaderContext builds these DTOs deliberately from verified text and IDs;
    # project manifests, checkpoint paths and provider metadata are never returned.
    fields = {
        'metadata': 'book_fingerprint title chapters',
        'progress': 'total_words last_chapter chapters',
        'chapter': 'id title blocks complete stale unavailable warning',
        'context': 'recognized matched_text display_name title attributes statements earlier_mentions same_block_context range',
    }
    return pick(value, fields[kind])


# Usage types are already application DTOs, but freeze their public fields here
# so a future private dataclass field cannot silently expand the API.
USAGE_FIELDS = {
    'UsageByUnitResult': 'scope units warning',
    'UnitUsage': 'unit_id chapter_id chunk_id analysis_unit_id unit_index passes input_tokens cached_input_tokens cache_write_input_tokens output_tokens reasoning_output_tokens total_tokens elapsed_seconds',
    'PassUsage': 'pass_no task_key provider profile requested_model reported_model physical_attempt_count provider_call_count unknown_provider_call_count retry_count failed_attempt_count failed_before_submission_count accepted_attempt_id recovered_from_existing_attempt result_status usage_status usage_source preflight_input input_tokens cached_input_tokens cache_write_input_tokens output_tokens reasoning_output_tokens total_tokens elapsed_seconds cost attempts',
    'AttemptUsage': 'attempt_id physical_record_id attempt_number generation_status validation_status acceptance_status provider_contacted accepted_checkpoint checkpoint_source_for_recovery usage_status usage_source reported_model requested_model reported_effort requested_effort selection_status scope_id slot_id generation input_tokens cached_input_tokens cache_write_input_tokens output_tokens reasoning_output_tokens total_tokens elapsed_seconds preflight_input cost',
    'UsageAggregate': 'value known_attempts unknown_attempts',
    'PreflightInput': 'value unit quality method',
    'CostEstimate': 'status amount currency estimate_type note',
}


def usage(value):
    if isinstance(value, tuple):
        return [usage(v) for v in value]
    fields = USAGE_FIELDS.get(type(value).__name__)
    result = {key: usage(getattr(value, key)) for key in fields.split()} if fields else value
    if type(value).__name__ == 'PassUsage' and value.pass_no == 0:
        # Internal ledger identities can contain relative artifact paths. P0's
        # public drilldown uses stable opaque identities, keeping that layout private.
        from ..util import digest
        identifiers = {}
        for attempt in result['attempts']:
            opaque = 'pa_' + (attempt['physical_record_id'] or digest(attempt['attempt_id']))
            identifiers[attempt['attempt_id']] = opaque
            attempt['attempt_id'] = opaque
        result['accepted_attempt_id'] = identifiers.get(result['accepted_attempt_id'])
    return result


PRELOAD_SUMMARY = 'state source_scopes required accepted needing_execution pending running failed unverifiable not_applicable unresolved retained physical_attempts session_warnings unfinished_consumers cleanup_pending_sessions recovery_required_sessions denominator'
PRELOAD_TARGET = ('target_id chapter_id scope_id source_sha256 source_map_sha256 label source_words source_utf8_bytes source_blocks planning_state profiles '
                  'provider model effort baseline_state session_state relevance slot_id generation thread_id '
                  'accepted_at selection_verified_at last_verified_at reported_model reported_effort acknowledgement '
                  'verification_scope native_check can_run reason')


def preload_target(value):
    result = pick(value, PRELOAD_TARGET)
    result['consumers'] = [pick(c, 'pass_no unit_id saved_output') for c in value['consumers']]
    for field in ('scope_id', 'slot_id', 'thread_id'):
        identifier = result.get(field)
        if identifier is not None and (not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', identifier)):
            result[field] = None
    if type(result.get('generation')) is not int or result['generation'] < 1:
        result['generation'] = None
    for field in ('accepted_at', 'last_verified_at', 'selection_verified_at'):
        if not isinstance(result.get(field), str):
            result[field] = None
    return safe_json(result)


def source_preload(value):
    result = pick(value, 'format_version workspace_id execution_mode applicable prepared planning_state observed_at revision intent_revision reason history_truncated')
    result['summary'] = pick(value['summary'], PRELOAD_SUMMARY)
    result['views'] = {name: pick(summary, PRELOAD_SUMMARY) for name, summary in value.get('views', {}).items()
                       if name in ('analysis', 'translation')}
    result['chapters'] = [{**pick(c, 'chapter_id title processing reason'), 'summary': pick(c['summary'], PRELOAD_SUMMARY),
                           'targets': [preload_target(t) for t in c['targets']]} for c in value['chapters']]
    result['assignments'] = [pick(a, 'pass_no profile provider model effort') for a in value['assignments']]
    result['history'] = []
    for history in value['history']:
        row = pick(history, 'slot_id scope_id chapter_id generation model effort state reason')
        for field in ('slot_id', 'scope_id', 'chapter_id', 'model', 'effort'):
            if not isinstance(row.get(field), str):
                row[field] = None
        for field in ('slot_id', 'scope_id', 'chapter_id'):
            if row[field] is not None and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', row[field]):
                row[field] = None
        if type(row.get('generation')) is not int or row['generation'] < 1:
            row['generation'] = None
        result['history'].append(row)
    return safe_json(result)


def source_preload_preview(value):
    result = pick(value, 'target_id page next_page source_kind available reason truncated')
    result['source'] = [pick(b, 'id text') for b in value['source']]
    result['session'] = preload_target(value['session'])
    return result
