import type { SearchHit } from '@/lib/types'
import { formatDate } from '@/lib/format'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardFooter, CardHeader } from '@/components/ui/card'
import { HitSnippet } from './HitSnippet'
import { StoragePath } from './StoragePath'

export function ResultCard({ hit }: { hit: SearchHit }) {
  const { document: doc } = hit

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{doc.aktenzeichen}</span>
            <Badge variant="secondary">{doc.klassifizierung}</Badge>
            <Badge variant="outline" className="uppercase">
              {doc.language}
            </Badge>
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
