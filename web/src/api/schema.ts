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
const metadata = z.object({ title: text, creators: z.array(text), language: nullableText, word_count: count.nullable(),
  label: nullableText.optional(), source_language: nullableText.optional(), target_language: nullableText.optional(), format: text.optional(), lifecycle })
const progressCount = z.object({ completed: count, required: count, denominator: text })
const workflowProgress = z.object({ percent: count.nullable(), basis: text, analysis: progressCount, translation: progressCount })
export const workspaceSchema = z.object({ workspace_id: text, prepared: z.boolean(), metadata, progress: workflowProgress.optional(), active_job: jobSchema.nullable(), last_job: jobSchema.nullable(), source_id: text.optional() })
export const workspacesSchema = z.object({ workspaces: z.array(workspaceSchema) })
export const librarySchema = z.object({ configured: z.boolean(), sources: z.array(z.object({ source_id: text, title: text, creators: z.array(text), language: nullableText, word_count: count.nullable(), workspace_id: nullableText })) })
export const libraryPageSchema = librarySchema.extend({ next_cursor: nullableText })
export type LibraryPage = z.infer<typeof libraryPageSchema>
const action = z.object({ allowed: z.boolean(), reason: nullableText })
const pass = z.object({ state: text, runtime_state: z.literal('running').optional(), completed: count, required: count, retained: count, provenance: z.array(z.object({ profile: nullableText, provider: nullableText, model: nullableText, stable_palette_index: count.nullable() })) })
export const sectionSchema = z.object({ id: text, ordinal: count, title: nullableText, fallback_excerpt: text,
  content_type: text, processing: z.enum(['full','translate','excluded']), profiles: z.record(text, nullableText), passes: z.record(text, pass) })
export const configSchema = z.object({ revision: text, sections: z.record(text, z.unknown()), pass_profiles: z.record(text, nullableText) })
export const publicationSchema = z.object({ state: text, current: z.boolean(), translation_complete: z.boolean(), target_language: text,
  title: nullableText, creators: z.array(text), source_language: nullableText, generated_at: nullableText, generated_by: nullableText,
  last_error: nullableText, last_failure: nullableText, filename: nullableText, size_bytes: count.nullable(), checks: z.array(text) })
const summary = z.object({ total: count, reviewed: count, unreviewed: count, uncertain: count, confirmed: z.boolean(), categories: z.record(text, count) })
export const pipelineSchema = z.object({ workspace_id: text, stage: text, active_job: jobSchema.nullable(), last_job: jobSchema.nullable(), publishing: z.boolean(), busy: z.boolean(), metadata,
  artifacts: z.object({ terminology: z.boolean(), book_memory: z.boolean() }), preparation: z.object({ source_id: text, checks: z.array(text) }),
  progress: workflowProgress,
  analysis: z.object({ complete: z.boolean(), membership_locked: z.boolean(), planned: z.boolean(), units: z.array(z.object({ id: text, chapter_id: text, state: text,
    attempt_result: nullableText, failed_attempt_count: count })) }),
  review: z.object({ prepared: z.boolean(), current: z.boolean(), revision: nullableText, summary: summary.nullable() }),
  approved: z.boolean(), translation_complete: z.boolean(), sections: z.array(sectionSchema), config: configSchema,
  units: z.array(z.object({ id: text, chapter_id: text, status: text, passes: z.record(text, z.object({ checkpoint_state: text, retained_count: count,
    attempt_result: nullableText, failed_attempt_count: count })) })), publication: publicationSchema, actions: z.record(text, action) })
const note = z.object({ text, confidence: text.optional(), evidence: z.array(text).optional() })
export const termSchema = z.object({ id: text, source: text, aliases: z.array(text), category: text, select: z.number(), custom: text,
  reviewed: z.boolean(), user_notes: text, meaning_notes: z.array(note),
  candidates: z.array(z.object({ number: count, text, reason: text.optional(), reasons: z.array(text).optional(), confidence: text.optional() })),
  observations: z.array(z.object({ id: text.optional(), about: z.array(text).optional(), kind: text.optional(), statement: text, confidence: text.optional() })).default([]),
  evidence: z.array(z.object({ chapter_id: text, block_id: text, excerpt: text.optional() })).default([]) })
export const reviewSchema = z.object({ _revision: text, confirmed: z.boolean(), terms: z.array(termSchema) })
export const patchSchema = z.object({ revision: text, term: termSchema, summary })
export const approvalSchema = z.object({ approved_terms: count, stale_chunks: count, pipeline: pipelineSchema })
export const evidenceSchema = z.object({ term_id: text, warnings: z.array(text), choice_pending_approval: z.boolean(),
  entries: z.array(z.object({ block_id: text, chapter_id: text, source_text: text.optional(), polish_text: nullableText.optional(),
    status: text.optional(), message: text.optional() })) })
const languageSupport = z.union([z.literal('all'), z.array(text)]).nullable()
const profile = z.object({ name: text, stable_palette_index: count.nullable(), provider: nullableText, model: nullableText, enabled: z.boolean(), source: text.optional(), provenance: text.optional(),
  source_languages: languageSupport.optional(), target_languages: languageSupport.optional() })
export const profilesSchema = z.object({ source: text, revision: text, assignments: z.record(text, nullableText), profiles: z.array(profile),
  default_profile: text, resolved_passes: z.record(text, profile) })
export const previewSchema = z.object({ id: text, blocks: z.array(z.object({ id: text, text })), next_page: count.nullable() })
const aggregate = z.object({ value: z.number().nullable(), known_attempts: count, unknown_attempts: count })
const usagePass = z.object({ pass_no: count, profile: nullableText, provider: nullableText, requested_model: nullableText, reported_model: nullableText,
  input_tokens: aggregate, cached_input_tokens: aggregate, reasoning_output_tokens: aggregate, output_tokens: aggregate,
  elapsed_seconds: aggregate, attempts: z.array(z.object({attempt_id:text,attempt_number:count.nullable(),generation_status:text,validation_status:text,acceptance_status:text,reported_model:nullableText,elapsed_seconds:count.nullable()})),
  result_status: text, physical_attempt_count: count, failed_attempt_count: count, retry_count: count })
export const usageSchema = z.object({ scope: text, warning: nullableText, units: z.array(z.object({ unit_id: text, chapter_id: nullableText, passes: z.array(usagePass) })) })
export const readerSchema = z.object({ title: text, book_fingerprint: text, chapters: z.array(z.object({ id: text, title: text }).passthrough()) })
export const readerProgressSchema = z.object({ total_words: count, last_chapter: z.object({ id: text, title: text }).nullable() })
export const chapterSchema = z.object({ id: text, title: text, complete: z.boolean(), stale: z.boolean(), warning: nullableText.optional(),
  unavailable: z.object({ after_blocks: count, reason: text }).optional(),
  blocks: z.array(z.object({ id: text, kind: text, text, formatting: z.array(z.object({ start: count, end: count, style: z.enum(['em','strong']) })).optional() })) })
export const markerSchema = z.object({ id: text, chapter_id: text, block_id: text, start: count, end: count, text })
export const markersSchema = z.object({ _revision: text, book_fingerprint: text, markers: z.array(markerSchema) })
export const markerMutationSchema = z.object({ revision: text, marker: markerSchema.optional(), deleted: text.optional() })
export const contextSchema = z.object({ recognized: z.boolean(), matched_text: text.optional(), title: text.optional(),
  display_name: text.optional(), attributes: z.array(z.object({ label: text, value: text })).optional(), statements: z.array(text).optional() }).passthrough()
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
export type Section = z.infer<typeof sectionSchema>
export type Workspace = z.infer<typeof workspaceSchema>
export type Term = z.infer<typeof termSchema>
export type Review = z.infer<typeof reviewSchema>
export type Profiles = z.infer<typeof profilesSchema>
export type Usage = z.infer<typeof usageSchema>

export const preparationSchema = z.object({checks:z.array(text),unavailable:nullableText,reading_order:nullableText,source_id:text})
export const analysisResetSchema = z.object({ revision: text, has_data: z.boolean(), can_reset: z.boolean(),
  reason: nullableText, history_available: z.boolean() })
