import { useContext, useRef, useState } from 'react'
import { useIsMutating, useMutation } from '@tanstack/react-query'
import { z } from 'zod'
import { ApiError, request, reconcile, Scope } from './client'
import { useConnection } from '../realtime/coordinator'
export function useCommand(workspace = 'global') {
  const scope = useContext(Scope)
  const connection = useConnection()
  const [error, setError] = useState<unknown>(null)
  const latch = useRef(false)
  const failureRef = useRef<unknown>(null)
  const pending = useIsMutating({ mutationKey: [scope, workspace] }) > 0
  const mutation = useMutation({ mutationKey: [scope, workspace], scope: { id: `${scope}:${workspace}` },
    mutationFn: async ({ run, deferReconcile }: { run: () => Promise<unknown>; deferReconcile: boolean }) => {
      try { return await run() } finally {
        if (!deferReconcile) await reconcile(workspace === 'global' ? undefined : workspace)
      }
    } })
  async function send<T>(path: string, schema: z.ZodType<T>, body: unknown, method = 'POST', deferReconcile = false): Promise<T | undefined> {
    if (latch.current || connection !== 'Live') return undefined
    latch.current = true; failureRef.current = null; setError(null)
    try {
      const value = await mutation.mutateAsync({ run: () => request(path, schema, { method, body }), deferReconcile }) as T
      return value
    } catch (failure) { failureRef.current = failure; setError(failure); return undefined }
    finally { latch.current = false }
  }
  return { send, pending, error, unknownOutcome: () => failureRef.current instanceof ApiError && (failureRef.current.status === 0 || failureRef.current.status >= 500), clearError: () => setError(null), disabled: pending || connection !== 'Live' }
}
