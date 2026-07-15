import { useState } from 'react'
import { Filters } from '@/components/Filters'
import { ModeToggle } from '@/components/ModeToggle'
import { ResultList } from '@/components/ResultList'
import { SearchBar } from '@/components/SearchBar'
import { useSearch } from '@/lib/useSearch'
import type { SearchMode } from '@/lib/types'

const RESULT_LIMIT = 20

export default function App() {
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<SearchMode>('semantic')
  const [klassifizierung, setKlassifizierung] = useState('')
  // The query behind the current results, so a mode switch can re-run it.
  const [submittedQuery, setSubmittedQuery] = useState('')
  const { state, run } = useSearch()

  function runSearch(q: string, searchMode: SearchMode) {
    const klass = klassifizierung.trim()
    run({
      query: q,
      mode: searchMode,
      limit: RESULT_LIMIT,
      filters: klass ? { klassifizierung: klass } : undefined,
    })
  }

  function submit() {
    const trimmed = query.trim()
    if (trimmed === '') return
    setSubmittedQuery(trimmed)
    runSearch(trimmed, mode)
  }

  // Switching the mode re-runs the last search immediately, so the user can
  // compare lexical / semantic / hybrid without pressing "Suchen" again.
  function changeMode(next: SearchMode) {
    setMode(next)
    if (submittedQuery !== '') runSearch(submittedQuery, next)
  }

  return (
    <div className="mx-auto min-h-screen max-w-3xl px-4 py-10">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Dokumentensuche</h1>
        <p className="text-sm text-muted-foreground">
          Semantische, lexikalische und hybride Suche über die indexierten Dokumente.
        </p>
      </header>

      <section className="mb-8 space-y-4">
        <SearchBar
          value={query}
          onChange={setQuery}
          onSubmit={submit}
          busy={state.status === 'loading'}
        />
        <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div className="grid gap-2">
            <span className="text-sm font-medium">Modus</span>
            <ModeToggle value={mode} onChange={changeMode} />
          </div>
          <div className="sm:w-64">
            <Filters
              klassifizierung={klassifizierung}
              onKlassifizierungChange={setKlassifizierung}
            />
          </div>
        </div>
      </section>

      <ResultList state={state} />
    </div>
  )
}
