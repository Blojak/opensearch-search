import type { SearchHit } from '@/lib/types'
import { formatDate } from '@/lib/format'
import { Card, CardContent, CardFooter, CardHeader } from '@/components/ui/card'
import { DocumentLabels } from './DocumentLabels'
import { HitSnippet } from './HitSnippet'
import { StoragePath } from './StoragePath'

export function ResultCard({ hit }: { hit: SearchHit }) {
  const { document: doc } = hit

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <DocumentLabels doc={doc} />
          </div>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span>{formatDate(doc.created_at)}</span>
            <span title="Relevanz-Score">score {hit.score.toFixed(3)}</span>
          </div>
        </div>
      </CardHeader>
      <CardContent className="text-sm leading-relaxed text-muted-foreground">
        <HitSnippet hit={hit} />
      </CardContent>
      {doc.s3_object_key && (
        <CardFooter className="justify-end gap-1">
          <StoragePath path={doc.s3_object_key} />
        </CardFooter>
      )}
    </Card>
  )
}
