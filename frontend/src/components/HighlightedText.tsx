/**
 * Renders an OpenSearch highlight fragment, which contains `<em>…</em>` around
 * the matched terms.
 *
 * We deliberately do not use `dangerouslySetInnerHTML`: the fragment is split on
 * the `<em>` tags and rebuilt as React nodes, so no markup from the indexed
 * document can ever be interpreted as HTML. The only tag we honour is the `<em>`
 * that OpenSearch itself inserted.
 */

const SPLIT = /<em>(.*?)<\/em>/gs

export function HighlightedText({ fragment }: { fragment: string }) {
  const nodes: React.ReactNode[] = []
  let lastIndex = 0
  let key = 0

  for (const match of fragment.matchAll(SPLIT)) {
    const [full, inner] = match
    const start = match.index
    if (start > lastIndex) {
      nodes.push(<span key={key++}>{fragment.slice(lastIndex, start)}</span>)
    }
    nodes.push(
      <mark key={key++} className="rounded bg-primary/15 px-0.5 text-foreground">
        {inner}
      </mark>,
    )
    lastIndex = start + full.length
  }

  if (lastIndex < fragment.length) {
    nodes.push(<span key={key++}>{fragment.slice(lastIndex)}</span>)
  }

  return <>{nodes}</>
}

/**
 * Renders `text` with the `[start, end)` slice marked — used for semantic hits,
 * where there are no term highlights but we want to show the whole chunk
 * standing out inside its surrounding body context. Offsets come from the
 * backend (`context.hit_start` / `hit_end`); the surrounding text stays dimmed.
 */
export function PassageText({
  text,
  start,
  end,
}: {
  text: string
  start: number
  end: number
}) {
  return (
    <>
      {text.slice(0, start)}
      <mark className="rounded bg-primary/15 px-0.5 text-foreground">
        {text.slice(start, end)}
      </mark>
      {text.slice(end)}
    </>
  )
}
