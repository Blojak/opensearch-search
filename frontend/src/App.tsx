import { useState } from 'react'
import { Layers } from 'lucide-react'
import { Filters } from '@/components/Filters'
import { EMPTY_FILTERS, toSearchFilters, type FilterState } from '@/lib/filters'
import { ModeToggle } from '@/components/ModeToggle'
import { ResultList } from '@/components/ResultList'
import { SearchBar } from '@/components/SearchBar'
import { ThemeToggle } from '@/components/ThemeToggle'
import { Button } from '@/components/ui/button'
import { useSearch } from '@/lib/useSearch'
import type { SearchMode } from '@/lib/types'

const RESULT_LIMIT = 20

export default function App() {
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<SearchMode>('semantic')
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS)
  const [grouped, setGrouped] = useState(false)
  // The query behind the current results, so a mode switch can re-run it.
  const [submittedQuery, setSubmittedQuery] = useState('')
  const { state, run } = useSearch()

  function runSearch(q: string, searchMode: SearchMode) {
    run({
      query: q,
      mode: searchMode,
      limit: RESULT_LIMIT,
      filters: toSearchFilters(filters),
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
      <header className="mb-8 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dokumentensuche</h1>
          <p className="text-sm text-muted-foreground">
            Semantische, lexikalische und hybride Suche über die indexierten Dokumente.
          </p>
        </div>
        <ThemeToggle />
      </header>

      <section className="mb-8 space-y-4">
        <SearchBar
          value={query}
          onChange={setQuery}
          onSubmit={submit}
          busy={state.status === 'loading'}
        />
        <div className="grid gap-2">
          <span className="text-sm font-medium">Modus</span>
          <ModeToggle value={mode} onChange={changeMode} />
        </div>
        <Filters value={filters} onChange={setFilters} />
      </section>

      <div className="mb-3 flex items-center justify-end">
        <Button
          variant={grouped ? 'default' : 'outline'}
          size="sm"
          onClick={() => setGrouped((v) => !v)}
          aria-pressed={grouped}
          title="Treffer je Dokument zusammenfassen"
        >
          <Layers className="size-4" />
          Nach Dokument gruppieren
        </Button>
      </div>

      <ResultList state={state} grouped={grouped} />
    </div>
  )
}
