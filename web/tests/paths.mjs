import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
export const testEvidence = name => resolve(process.env.INTELITEX_TEST_EVIDENCE_ROOT ?? tmpdir(), name)
