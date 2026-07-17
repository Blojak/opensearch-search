import type { SearchHit } from '@/lib/types'
import { HighlightedText, PassageText } from './HighlightedText'

/**
 * The readable snippet of a hit, with the right highlighting per search mode:
 * lexical/hybrid show OpenSearch's term fragments; a semantic hit (no term
 * highlights) shows the whole chunk marked inside its body context; otherwise
 * the plain chunk text. Shared by the flat result card and the grouped one.
 */
export function HitSnippet({ hit }: { hit: SearchHit }) {
  if (hit.highlights.length > 0) {
    return (
      <div className="space-y-2">
        {hit.highlights.map((fragment, i) => (
          <p key={i}>
            <HighlightedText fragment={fragment} />
          </p>
        ))}
      </div>
    )
  }
  if (hit.context) {
    return (
      <p>
        <PassageText
          text={hit.context.text}
          start={hit.context.hit_start}
          end={hit.context.hit_end}
        />
      </p>
    )
  }
  return <p>{hit.chunk_text}</p>
}
