import { useCallback, useRef, useState } from 'react'
import type { SearchState } from '@/components/ResultList'
import { ApiError, search } from './api'
import type { SearchRequest } from './types'

/**
 * Runs searches and exposes their state as one discriminated union.
 *
 * Guards against out-of-order responses: a slow request that resolves after a
 * newer one has been issued is dropped, so the list never flickers back to a
 * stale result.
 */
export function useSearch() {
  const [state, setState] = useState<SearchState>({ status: 'idle' })
  const latest = useRef(0)

  const run = useCallback(async (request: SearchRequest) => {
    const ticket = ++latest.current
    setState({ status: 'loading' })
    try {
      const data = await search(request)
      if (ticket === latest.current) setState({ status: 'success', data })
    } catch (error) {
      if (ticket !== latest.current) return
      const isAuth = error instanceof ApiError && error.isAuth
      const message = error instanceof Error ? error.message : 'Unbekannter Fehler'
      setState({ status: 'error', message, isAuth })
    }
  }, [])

  return { state, run }
}
