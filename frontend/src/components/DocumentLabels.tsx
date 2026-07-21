import type { HitDocument } from '@/lib/types'
import { Badge } from '@/components/ui/badge'

/**
 * The document's identity labels in a card header: Aktenzeichen, classification
 * and language. Aktenzeichen and classification are optional (ingest no longer
 * requires them), so both degrade gracefully when absent.
 */
export function DocumentLabels({ doc }: { doc: HitDocument }) {
  return (
    <>
      {doc.aktenzeichen ? (
        <span className="font-medium">{doc.aktenzeichen}</span>
      ) : (
        <span className="font-medium italic text-muted-foreground">ohne Aktenzeichen</span>
      )}
      {doc.klassifizierung && <Badge variant="secondary">{doc.klassifizierung}</Badge>}
      <Badge variant="outline" className="uppercase">
        {doc.language}
      </Badge>
    </>
  )
}
