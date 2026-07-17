import type { SearchFilters } from './types'

/**
 * The filter form's own state (UI-shaped strings), turned into API
 * `SearchFilters` by `toSearchFilters` on submit.
 */
export interface FilterState {
  klassifizierung: string
  docType: string // key into DOC_TYPES, '' = any
  createdFrom: string // yyyy-mm-dd, '' = unset
  createdTo: string
}

export const EMPTY_FILTERS: FilterState = {
  klassifizierung: '',
  docType: '',
  createdFrom: '',
  createdTo: '',
}

/**
 * User-facing document types mapped to their MIME types. One type can map to
 * several (old + OOXML), matched with an OR on the backend.
 */
export const DOC_TYPES: { key: string; label: string; mimes: string[] }[] = [
  { key: 'pdf', label: 'PDF', mimes: ['application/pdf'] },
  {
    key: 'word',
    label: 'Word',
    mimes: [
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      'application/msword',
    ],
  },
  {
    key: 'excel',
    label: 'Excel',
    mimes: [
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      'application/vnd.ms-excel',
    ],
  },
  {
    key: 'powerpoint',
    label: 'PowerPoint',
    mimes: [
      'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      'application/vnd.ms-powerpoint',
    ],
  },
  { key: 'email', label: 'E-Mail', mimes: ['message/rfc822'] },
  { key: 'text', label: 'Text', mimes: ['text/plain'] },
]

/** Build API filters from the form state (omitting empty fields). */
export function toSearchFilters(state: FilterState): SearchFilters | undefined {
  const filters: SearchFilters = {}

  if (state.klassifizierung.trim()) filters.klassifizierung = state.klassifizierung.trim()

  const mimes = DOC_TYPES.find((t) => t.key === state.docType)?.mimes
  if (mimes) filters.mime_type = mimes

  if (state.createdFrom) filters.created_from = new Date(state.createdFrom).toISOString()
  if (state.createdTo) {
    // Include the whole selected day, not just its midnight.
    const to = new Date(state.createdTo)
    to.setHours(23, 59, 59, 999)
    filters.created_to = to.toISOString()
  }

  return Object.keys(filters).length > 0 ? filters : undefined
}
