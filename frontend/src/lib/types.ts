/**
 * The search API contract, mirrored from the Flask backend.
 *
 * Kept in lockstep with `app/search.py` (SearchHit, SearchMode) and
 * `app/api.py` (post_search request/response). If the backend response shape
 * changes, this file is the single place to follow it.
 */

export type SearchMode = 'lexical' | 'semantic' | 'hybrid'

export const SEARCH_MODES: SearchMode[] = ['lexical', 'semantic', 'hybrid']

/** Optional metadata filters, mirrored from Postgres onto every chunk. */
export interface SearchFilters {
  klassifizierung?: string
  aktenzeichen?: string
  verfahren_id?: string
  language?: string
  created_from?: string // ISO-8601
  created_to?: string // ISO-8601
}

export interface SearchRequest {
  query: string
  mode?: SearchMode
  limit?: number
  filters?: SearchFilters
}

/** Document-level metadata carried on every hit (denormalized from Postgres). */
export interface HitDocument {
  id: string
  aktenzeichen: string
  verfahren_id: string | null
  klassifizierung: string
  language: string
  mime_type: string | null
  created_at: string
  version_number: number
}

export interface SearchHit {
  score: number
  document_id: string
  version_number: number
  chunk_index: number
  chunk_text: string
  /** Offsets into the version body — for later passage extraction / highlighting. */
  start_char: number | null
  end_char: number | null
  /** `<em>`-wrapped fragments from OpenSearch; empty for pure semantic hits. */
  highlights: string[]
  document: HitDocument
}

export interface SearchResponse {
  query: string
  mode: SearchMode
  count: number
  results: SearchHit[]
}
