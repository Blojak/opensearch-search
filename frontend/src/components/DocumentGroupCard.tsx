import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { SearchHit } from '@/lib/types'
import { formatDate } from '@/lib/format'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader } from '@/components/ui/card'
import { HitSnippet } from './HitSnippet'
import { StoragePath } from './StoragePath'

/**
 * One document with all of its chunk hits from the current result set, collapsed
 * by default: the best chunk is shown, the rest can be expanded. The count is
 * the number of matching chunks *in these results*, not the document total —
 * "semantic matches" have no hard total, so this stays honest across modes.
 */
export function DocumentGroupCard({ hits }: { hits: SearchHit[] }) {
  const [expanded, setExpanded] = useState(false)
  const [best, ...rest] = hits
  const doc = best.document

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
            <Badge title="Fundstellen in diesen Ergebnissen">
              {hits.length} {hits.length === 1 ? 'Fundstelle' : 'Fundstellen'}
            </Badge>
          </div>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span>{formatDate(doc.created_at)}</span>
            <span title="Bester Relevanz-Score">score {best.score.toFixed(3)}</span>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-sm leading-relaxed text-muted-foreground">
        <HitSnippet hit={best} />

        {rest.length > 0 && (
          <div>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1 px-2 text-xs"
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
            >
              {expanded ? (
                <ChevronDown className="size-3.5" />
              ) : (
                <ChevronRight className="size-3.5" />
              )}
              {rest.length} weitere {rest.length === 1 ? 'Fundstelle' : 'Fundstellen'}
            </Button>

            {expanded && (
              <div className="mt-2 space-y-3 border-l-2 border-border pl-3">
                {rest.map((hit) => (
                  <HitSnippet key={hit.chunk_index} hit={hit} />
                ))}
              </div>
            )}
          </div>
        )}
      </CardContent>
      {doc.s3_object_key && (
        <CardFooter className="justify-end gap-1">
          <StoragePath path={doc.s3_object_key} />
        </CardFooter>
      )}
    </Card>
  )
}
