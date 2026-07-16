import { AlertCircle, FileSearch } from 'lucide-react'
import type { SearchHit, SearchResponse } from '@/lib/types'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Skeleton } from '@/components/ui/skeleton'
import { DocumentGroupCard } from './DocumentGroupCard'
import { ResultCard } from './ResultCard'

export type SearchState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string; isAuth: boolean }
  | { status: 'success'; data: SearchResponse }

/** Group chunk hits by document, keeping the (score-ranked) order of first
 * appearance. Each group's first hit is therefore the document's best chunk. */
function groupByDocument(hits: SearchHit[]): SearchHit[][] {
  const groups = new Map<string, SearchHit[]>()
  for (const hit of hits) {
    const existing = groups.get(hit.document_id)
    if (existing) existing.push(hit)
    else groups.set(hit.document_id, [hit])
  }
  return [...groups.values()]
}

function LoadingSkeleton() {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="Ergebnisse werden geladen">
      {[0, 1, 2].map((i) => (
        <div key={i} className="space-y-2 rounded-xl border p-6">
          <Skeleton className="h-5 w-48" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      ))}
    </div>
  )
}

function Empty({ icon, title, hint }: { icon: React.ReactNode; title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed py-16 text-center text-muted-foreground">
      {icon}
      <p className="font-medium">{title}</p>
      {hint && <p className="max-w-sm text-sm">{hint}</p>}
    </div>
  )
}

export function ResultList({ state, grouped }: { state: SearchState; grouped: boolean }) {
  if (state.status === 'idle') {
    return (
      <Empty
        icon={<FileSearch className="size-8" />}
        title="Noch keine Suche"
        hint="Geben Sie oben einen Suchbegriff ein und wählen Sie einen Modus."
      />
    )
  }

  if (state.status === 'loading') {
    return <LoadingSkeleton />
  }

  if (state.status === 'error') {
    return (
      <Alert variant="destructive">
        <AlertCircle />
        <AlertTitle>{state.isAuth ? 'Nicht angemeldet' : 'Suche fehlgeschlagen'}</AlertTitle>
        <AlertDescription>{state.message}</AlertDescription>
      </Alert>
    )
  }

  const { data } = state
  if (data.count === 0) {
    return (
      <Empty
        icon={<FileSearch className="size-8" />}
        title="Keine Treffer"
        hint="Versuchen Sie einen anderen Begriff, einen anderen Modus oder entfernen Sie den Filter."
      />
    )
  }

  if (grouped) {
    const documents = groupByDocument(data.results)
    return (
      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">
          {documents.length} {documents.length === 1 ? 'Dokument' : 'Dokumente'} ·{' '}
          {data.count} {data.count === 1 ? 'Treffer' : 'Treffer'} · Modus {data.mode}
        </p>
        {documents.map((hits) => (
          <DocumentGroupCard key={hits[0].document_id} hits={hits} />
        ))}
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        {data.count} {data.count === 1 ? 'Treffer' : 'Treffer'} · Modus {data.mode}
      </p>
      {data.results.map((hit) => (
        <ResultCard key={`${hit.document_id}-${hit.version_number}-${hit.chunk_index}`} hit={hit} />
      ))}
    </div>
  )
}
