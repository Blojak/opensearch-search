import { FolderOpen } from 'lucide-react'
import type { SearchHit } from '@/lib/types'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardFooter, CardHeader } from '@/components/ui/card'
import { HighlightedText, PassageText } from './HighlightedText'

function formatDate(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleDateString('de-DE', { year: 'numeric', month: 'short', day: 'numeric' })
}

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
        {hit.highlights.length > 0 ? (
          // Lexical / hybrid: OpenSearch's term-level highlight fragments.
          <div className="space-y-2">
            {hit.highlights.map((fragment, i) => (
              <p key={i}>
                <HighlightedText fragment={fragment} />
              </p>
            ))}
          </div>
        ) : hit.context ? (
          // Semantic: no term highlights, so show the whole chunk in context.
          <p>
            <PassageText
              text={hit.context.text}
              start={hit.context.hit_start}
              end={hit.context.hit_end}
            />
          </p>
        ) : (
          <p>{hit.chunk_text}</p>
        )}
      </CardContent>
      {doc.s3_object_key && (
        <CardFooter className="justify-end">
          <span
            className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground"
            title="Ablageort des Dokuments"
          >
            <FolderOpen className="size-3.5 shrink-0" />
            {doc.s3_object_key}
          </span>
        </CardFooter>
      )}
    </Card>
  )
}
