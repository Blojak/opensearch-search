/**
 * Thin, typed client for the search API.
 *
 * Talks to `/api/*`, which the Vite dev server proxies to the Flask API on
 * :5002 (see vite.config.ts) — so the browser stays same-origin and there is no
 * CORS in development.
 *
 * Auth is deliberately a stopgap: the bearer token is read from
 * `VITE_DEV_TOKEN` (copied from Keycloak into `.env.local`). The real
 * authorization-code/PKCE login lives in a later step; nothing else in the app
 * needs to change when it lands, because it only has to make `authHeader()`
 * return a live token.
 */

import type { SearchRequest, SearchResponse } from './types'

const DEV_TOKEN = import.meta.env.VITE_DEV_TOKEN as string | undefined

/** An API call that came back with a non-2xx status. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }

  /** The token was missing, expired or rejected — distinct from a 400. */
  get isAuth(): boolean {
    return this.status === 401
  }
}

function authHeader(): Record<string, string> {
  if (!DEV_TOKEN) {
    throw new ApiError(
      401,
      'No dev token set. Put VITE_DEV_TOKEN=<bearer token> in frontend/.env.local (see .env.example).',
    )
  }
  return { Authorization: `Bearer ${DEV_TOKEN}` }
}

/** Read `{ error }` from a failed response, falling back to the status text. */
async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { error?: string }
    if (body?.error) return body.error
  } catch {
    // non-JSON body — fall through to the status text
  }
  return response.statusText || `request failed (${response.status})`
}

export async function search(request: SearchRequest): Promise<SearchResponse> {
  let response: Response
  try {
    response = await fetch('/api/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeader() },
      body: JSON.stringify(request),
    })
  } catch {
    // fetch only rejects on network/proxy failure — the API being down.
    throw new ApiError(0, 'Cannot reach the API. Is the backend running on :5002?')
  }

  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response))
  }
  return (await response.json()) as SearchResponse
}
