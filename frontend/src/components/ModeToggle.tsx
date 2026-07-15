import type { SearchMode } from '@/lib/types'
import { SEARCH_MODES } from '@/lib/types'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'

const LABELS: Record<SearchMode, string> = {
  semantic: 'Semantisch',
  lexical: 'Lexikalisch',
  hybrid: 'Hybrid',
}

export function ModeToggle({
  value,
  onChange,
}: {
  value: SearchMode
  onChange: (mode: SearchMode) => void
}) {
  return (
    <ToggleGroup
      type="single"
      value={value}
      // Radix emits '' when the active item is toggled off; keep the current mode.
      onValueChange={(next) => next && onChange(next as SearchMode)}
      variant="outline"
      className="w-full sm:w-auto"
    >
      {SEARCH_MODES.map((mode) => (
        <ToggleGroupItem key={mode} value={mode} className="px-4">
          {LABELS[mode]}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  )
}
