import { z } from 'zod'
const text = z.string()
const count = z.number().nonnegative()
const nullableText = text.nullable()
export const eventSchema = z.object({ id: count, job_id: text, workspace_id: text, sequence: count, timestamp: text,
  event: z.object({ kind: text, current: z.number().nullable().optional(), total: z.number().nullable().optional(),
    values: z.record(text, z.unknown()).default({}) }) })
export const jobSchema = z.object({ job_id: text, workspace_id: text, operation: text,
  state: z.enum(['starting', 'running', 'stopping', 'succeeded', 'failed', 'cancelled', 'abandoned']),
  sequence: count, started_at: nullableText, finished_at: nullableText, last_event: eventSchema.nullable(),
  error: z.object({ type: text, message: text }).nullable() })
export const jobsSchema = z.object({ jobs: z.array(jobSchema), cursor: count })
export const capabilitiesSchema = z.object({ scope_id: text, import_enabled: z.boolean(), review: z.boolean(), reader: z.boolean(),
  sse: z.boolean(), multi_workspace: z.boolean(), drafts: z.boolean(), archive: z.boolean(), section_configuration: z.boolean(), diagnostics: z.boolean() })
const lifecycle = z.object({ archived: z.boolean(), revision: nullableText, updated_at: text.optional() })
const metadata = z.object({ book_id: text.optional(), title: text, creators: z.array(text), language: nullableText, word_count: count.nullable(),
  label: nullableText.optional(), source_language: nullableText.optional(), target_language: nullableText.optional(), format: text.optional(), lifecycle })
const progressCount = z.object({ completed: count, required: count, denominator: text })
const workflowProgress = z.object({ percent: count.nullable(), basis: text, analysis: progressCount, translation: progressCount })
export const workspaceSchema = z.object({ workspace_id: text, prepared: z.boolean(), metadata, progress: workflowProgress.optional(), active_job: jobSchema.nullable(), last_job: jobSchema.nullable(), source_id: text.optional() })
export const workspacesSchema = z.object({ workspaces: z.array(workspaceSchema) })
const libraryGroupSchema = z.object({ group_id: text, name: text, kind: z.enum(['author', 'series', 'category']).nullable(), warning: text.optional() })
export const librarySchema = z.object({ configured: z.boolean(), sources: z.array(z.object({ source_id: text, title: text, creators: z.array(text), language: nullableText, word_count: count.nullable(), workspace_id: nullableText, groups: z.array(libraryGroupSchema).default([]) })) })
export const libraryPageSchema = librarySchema.extend({ next_cursor: nullableText })
export type LibraryPage = z.infer<typeof libraryPageSchema>
const action = z.object({ allowed: z.boolean(), reason: nullableText })
const pass = z.object({ state: text, runtime_state: z.literal('running').optional(), completed: count, required: count, retained: count,
  unresolved: count.optional(), session_warnings: count.optional(), denominator: text.optional(),
  provenance: z.array(z.object({ profile: nullableText, provider: nullableText, model: nullableText, stable_palette_index: count.nullable() })) })
export const sectionSchema = z.object({ id: text, ordinal: count, title: nullableText, fallback_excerpt: text,
  content_type: text, processing: z.enum(['full','translate','excluded']), profiles: z.record(text, nullableText), passes: z.record(text, pass) })
export const configSchema = z.object({ revision: text, sections: z.record(text, z.unknown()), pass_profiles: z.record(text, nullableText) })
export const publicationSchema = z.object({ state: text, current: z.boolean(), translation_complete: z.boolean(), target_language: text,
  library_current: z.boolean().nullable().optional(), library_filename: nullableText.optional(),
  title: nullableText, creators: z.array(text), source_language: nullableText, generated_at: nullableText, generated_by: nullableText,
  last_error: nullableText, last_failure: nullableText, filename: nullableText, size_bytes: count.nullable(), checks: z.array(text) })
export const publicationSelectionSchema = z.object({ revision: text, excluded_section_ids: z.array(text),
  groups: z.array(z.object({ id: text, section_ids: z.array(text), titles: z.array(text) })),
  diagnostic: z.object({ section_ids: z.array(text), message: text }).nullable().optional() })
const summary = z.object({ total: count, reviewed: count, unreviewed: count, uncertain: count, confirmed: z.boolean(), categories: z.record(text, count) })
const reviewImpact = z.object({ changed_term_ids: z.array(text), affected_chunks: z.array(z.object({ chunk_id: text, term_ids: z.array(text) })) })
export const preloadSummarySchema = z.object({ state: text, source_scopes: count, required: count, accepted: count,
  needing_execution: count, pending: count, running: count, failed: count, unverifiable: count, not_applicable: count,
  unresolved: count, retained: count, physical_attempts: count, session_warnings: count, denominator: text,
  unfinished_consumers: count.optional(), cleanup_pending_sessions: count.optional(), recovery_required_sessions: count.optional() })
export const preloadTargetSchema = z.object({ target_id: text, chapter_id: text, scope_id: nullableText,
  source_sha256: nullableText, source_map_sha256: nullableText, label: text, planning_state: text,
  source_words: count, source_utf8_bytes: count, source_blocks: count,
  profiles: z.array(text), provider: nullableText, model: nullableText, effort: nullableText,
  consumers: z.array(z.object({ pass_no: count, unit_id: text, saved_output: z.boolean() })),
  baseline_state: text, session_state: text, relevance: text, slot_id: nullableText, generation: count.nullable(),
  thread_id: nullableText, accepted_at: nullableText, selection_verified_at: nullableText, last_verified_at: nullableText,
  reported_model: nullableText, reported_effort: nullableText, acknowledgement: nullableText,
  verification_scope: text, native_check: text, can_run: z.boolean(), reason: nullableText })
export const sourcePreloadSchema = z.object({ format_version: z.literal(1), workspace_id: text, execution_mode: text,
  applicable: z.boolean(), prepared: z.boolean(), planning_state: text, observed_at: text, revision: text,
  intent_revision: text, reason: nullableText, summary: preloadSummarySchema,
  views: z.object({ analysis: preloadSummarySchema.optional(), translation: preloadSummarySchema.optional() }).optional(),
  chapters: z.array(z.object({ chapter_id: text, title: nullableText, processing: text, reason: nullableText, summary: preloadSummarySchema,
    targets: z.array(preloadTargetSchema) })),
  assignments: z.array(z.object({ pass_no: count, profile: text, provider: text, model: nullableText, effort: nullableText })),
  history: z.array(z.object({ slot_id: nullableText, scope_id: nullableText, chapter_id: nullableText, generation: count.nullable(),
    model: nullableText, effort: nullableText, state: text, reason: text })), history_truncated: z.boolean() })
export const preloadPreviewSchema = z.object({ target_id: text, page: count, next_page: count.nullable(),
  source_kind: z.enum(['planned','recorded']), available: z.boolean(), reason: nullableText, truncated: z.boolean(),
  source: z.array(z.object({ id: text, text })), session: preloadTargetSchema })
export type SourcePreload = z.infer<typeof sourcePreloadSchema>
export type PreloadTarget = z.infer<typeof preloadTargetSchema>
export const pipelineSchema = z.object({ workspace_id: text, stage: text, active_job: jobSchema.nullable(), last_job: jobSchema.nullable(), publishing: z.boolean(), busy: z.boolean(), metadata,
  source_preload: preloadSummarySchema.optional(),
  artifacts: z.object({ terminology: z.boolean(), book_memory: z.boolean() }), preparation: z.object({ source_id: text, checks: z.array(text) }),
  progress: workflowProgress,
  analysis: z.object({ complete: z.boolean(), membership_locked: z.boolean(), planned: z.boolean(), units: z.array(z.object({ id: text, chapter_id: text, state: text,
    attempt_result: nullableText, failed_attempt_count: count })) }),
  review: z.object({ prepared: z.boolean(), current: z.boolean(), revision: nullableText, summary: summary.nullable(), impact: reviewImpact.nullable().optional() }),
  approved: z.boolean(), translation_complete: z.boolean(), sections: z.array(sectionSchema), config: configSchema,
  units: z.array(z.object({ id: text, chapter_id: text, source_words: count.nullable().optional(), status: text, passes: z.record(text, z.object({ checkpoint_state: text, retained_count: count,
    attempt_result: nullableText, failed_attempt_count: count })) })), publication: publicationSchema, actions: z.record(text, action) })
export const pipelineSummarySchema = z.object({ workspace_id: text, stage: text, progress: workflowProgress,
  analysis: z.object({ complete: z.boolean() }), approved: z.boolean(),
  publication: z.object({ current: z.boolean(), last_failure: nullableText, library_current: z.boolean().nullable().optional(), library_filename: nullableText.optional() }),
  actions: z.record(text, action), metadata: z.object({ lifecycle }), publishing: z.boolean(),
  active_job: jobSchema.nullable(), last_job: jobSchema.nullable() })
const note = z.object({ text, confidence: text.optional(), evidence: z.array(text).optional() })
export const termSchema = z.object({ id: text, source: text, aliases: z.array(text), category: text, select: z.number(), custom: text,
  reviewed: z.boolean(), review_method: text.optional(), user_notes: text, meaning_notes: z.array(note),
  candidates: z.array(z.object({ number: count, text, reason: text.optional(), reasons: z.array(text).optional(), confidence: text.optional() })),
  observations: z.array(z.object({ id: text.optional(), about: z.array(text).optional(), kind: text.optional(), statement: text, confidence: text.optional() })).default([]),
  evidence: z.array(z.object({ chapter_id: text, block_id: text, excerpt: text.optional() })).default([]) })
export const reviewSchema = z.object({ _revision: text, confirmed: z.boolean(), terms: z.array(termSchema) })
export const patchSchema = z.object({ revision: text, term: termSchema, summary })
export const bulkReviewSchema = z.object({ revision: text, terms: z.array(termSchema), changed_count: count, summary })
export const approvalSchema = z.object({ approved_terms: count, stale_chunks: count, pipeline: pipelineSchema })
export const evidenceSchema = z.object({ term_id: text, warnings: z.array(text), choice_pending_approval: z.boolean(),
  entries: z.array(z.object({ block_id: text, chapter_id: text, source_text: text.optional(), polish_text: nullableText.optional(),
    status: text.optional(), message: text.optional() })) })
const languageSupport = z.union([z.literal('all'), z.array(text)]).nullable()
const profile = z.object({ name: text, stable_palette_index: count.nullable(), provider: nullableText, model: nullableText, enabled: z.boolean(), source: text.optional(), provenance: text.optional(),
  reasoning_effort: nullableText.optional(),
  source_languages: languageSupport.optional(), target_languages: languageSupport.optional() })
export const profilesSchema = z.object({ source: text, revision: text, assignments: z.record(text, nullableText), profiles: z.array(profile),
  default_profile: text, resolved_passes: z.record(text, profile) })
export const previewSchema = z.object({ id: text, blocks: z.array(z.object({ id: text, text })), next_page: count.nullable() })
export const translationPassPreviewSchema = z.object({ chunk_id: text, pass_no: count,
  page: count, next_page: count.nullable(), available: z.boolean(), current: z.boolean(), truncated: z.boolean(),
  source: z.array(z.object({ id: text, text })), translations: z.array(z.object({ id: text, text })),
  sentences: z.array(z.object({ id: text, block_id: text, text })),
  checks: z.array(z.record(text, z.unknown())), findings: z.array(z.record(text, z.unknown())) })
export type TranslationPassPreview = z.infer<typeof translationPassPreviewSchema>
export const analysisUnitPreviewSchema = z.object({ unit_id: text, page: count, next_page: count.nullable(),
  available: z.boolean(), truncated: z.boolean(), source: z.array(z.object({ id: text, text })),
  terms: z.array(z.object({ source: text, aliases: z.array(text), category: text, meaning: text,
    confidence: text, candidates: z.array(z.object({ text, reason: text })), evidence: z.array(text) })),
  observations: z.array(z.object({ about: z.array(text), kind: text, statement: text,
    confidence: text, evidence: z.array(text) })) })
export type AnalysisUnitPreview = z.infer<typeof analysisUnitPreviewSchema>
const aggregate = z.object({ value: z.number().nullable(), known_attempts: count, unknown_attempts: count })
const costEstimate = z.object({ status: text, amount: count.nullable(), currency: nullableText,
  estimate_type: nullableText, note: nullableText })
const usagePass = z.object({ pass_no: count, profile: nullableText, provider: nullableText, requested_model: nullableText, reported_model: nullableText,
  input_tokens: aggregate, cached_input_tokens: aggregate, reasoning_output_tokens: aggregate, output_tokens: aggregate,
  task_key: text.optional(), elapsed_seconds: aggregate, attempts: z.array(z.object({attempt_id:text,attempt_number:count.nullable(),generation_status:text,validation_status:text,acceptance_status:text,reported_model:nullableText,elapsed_seconds:count.nullable(),
    physical_record_id: nullableText.optional(), requested_model: nullableText.optional(), requested_effort: nullableText.optional(),
    reported_effort: nullableText.optional(), selection_status: nullableText.optional(), scope_id: nullableText.optional(),
    slot_id: nullableText.optional(), generation: count.nullable().optional(), provider_contacted: z.boolean().nullable().optional(),
    usage_status: text.optional(), input_tokens: count.nullable().optional(), cached_input_tokens: count.nullable().optional(),
    cache_write_input_tokens: count.nullable().optional(), output_tokens: count.nullable().optional(),
    reasoning_output_tokens: count.nullable().optional(), total_tokens: count.nullable().optional(), cost: costEstimate.nullable().optional() })),
  result_status: text, physical_attempt_count: count, provider_call_count: count, unknown_provider_call_count: count,
  failed_attempt_count: count, retry_count: count, cost: costEstimate.nullable() })
export const usageSchema = z.object({ scope: text, warning: nullableText, units: z.array(z.object({ unit_id: text, chapter_id: nullableText, passes: z.array(usagePass) })) })
export const readerSchema = z.object({ title: text, book_fingerprint: text, chapters: z.array(z.object({ id: text, title: text }).passthrough()) })
export const readerProgressSchema = z.object({ total_words: count, last_chapter: z.object({ id: text, title: text }).nullable(),
  chapters: z.array(z.object({ id: text, start: count, words: count, blocks: z.array(z.object({ id: text, start: count, words: count })) })) })
export const chapterSchema = z.object({ id: text, title: text, complete: z.boolean(), stale: z.boolean(), warning: nullableText.optional(),
  unavailable: z.object({ after_blocks: count, reason: text }).optional(),
  blocks: z.array(z.object({ id: text, kind: text, text, formatting: z.array(z.object({ start: count, end: count, style: z.enum(['em','strong']) })).optional() })) })
export const markerSchema = z.object({ id: text, chapter_id: text, block_id: text, start: count, end: count, text })
export const markersSchema = z.object({ _revision: text, book_fingerprint: text, markers: z.array(markerSchema) })
export const markerMutationSchema = z.object({ revision: text, marker: markerSchema.optional(), deleted: text.optional() })
export const contextSchema = z.object({ recognized: z.boolean(), matched_text: text.optional(), title: text.optional(),
  display_name: text.optional(), attributes: z.array(z.object({ label: text, value: text })).optional(), statements: z.array(text).optional(),
  earlier_mentions: z.array(z.object({ text, chapter_title: text })).optional(), same_block_context: z.array(z.object({ text })).optional() }).passthrough()
export const activitySchema = z.object({ events: z.array(eventSchema) })
export const draftSchema = z.object({ workspace_id: text, source_id: text })
export const preflightSchema = z.object({ source_id: text, title: text, creators: z.array(text), declared_language: nullableText,
  detected_language: nullableText, detection_confidence: count, source_language: nullableText, source_fingerprint: text,
  language_warning: nullableText, sample_word_count: count.optional(), sampled_documents: count.optional(),
  document_count: count.optional(), sample_previews: z.array(z.object({ position: count, heading: nullableText, excerpt: text })).optional() })
export const compatibilitySchema = z.object({ compatible: z.boolean(), warnings: z.array(text), target_choices: z.array(text) })
export const lifecycleSchema = lifecycle
export type Job = z.infer<typeof jobSchema>
export type Envelope = z.infer<typeof eventSchema>
export type Pipeline = z.infer<typeof pipelineSchema>
export type PipelineSummary = z.infer<typeof pipelineSummarySchema>
export type Section = z.infer<typeof sectionSchema>
export type Workspace = z.infer<typeof workspaceSchema>
export type Term = z.infer<typeof termSchema>
export type Review = z.infer<typeof reviewSchema>
export type Profiles = z.infer<typeof profilesSchema>
export type Usage = z.infer<typeof usageSchema>

export const preparationSchema = z.object({checks:z.array(text),unavailable:nullableText,reading_order:nullableText,source_id:text})
export const analysisResetSchema = z.object({ revision: text, has_data: z.boolean(), can_reset: z.boolean(),
  reason: nullableText, history_available: z.boolean() })

const bibliographicMetadata = z.object({ title: text, creators: z.array(text), language: nullableText })
export const bookMetadataSchema = z.object({ book_id: text, source_fingerprint: text, original: bibliographicMetadata, effective: bibliographicMetadata, corrections: bibliographicMetadata.partial(), revision: text, updated_at: nullableText })
