import type { SearchHit } from '@/lib/types'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { HighlightedText } from './HighlightedText'

function formatDate(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleDateString('de-DE', { year: 'numeric', month: 'short', day: 'numeric' })
}

export function ResultCard({ hit }: { hit: SearchHit }) {
  const { document: doc } = hit
  // Pure semantic hits carry no highlights; show the chunk text instead.
  const snippets = hit.highlights.length > 0 ? hit.highlights : [hit.chunk_text]

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
      <CardContent className="space-y-2 text-sm leading-relaxed text-muted-foreground">
        {snippets.map((snippet, i) => (
          <p key={i}>
            <HighlightedText fragment={snippet} />
          </p>
        ))}
      </CardContent>
    </Card>
  )
}
