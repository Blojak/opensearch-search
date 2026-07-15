import { Search } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

export function SearchBar({
  value,
  onChange,
  onSubmit,
  busy,
}: {
  value: string
  onChange: (query: string) => void
  onSubmit: () => void
  busy: boolean
}) {
  return (
    <form
      className="flex gap-2"
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit()
      }}
    >
      <div className="relative flex-1">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder="Wonach suchen Sie?"
          aria-label="Suchbegriff"
          autoFocus
          className="pl-9"
        />
      </div>
      <Button type="submit" disabled={busy || value.trim() === ''}>
        {busy ? 'Sucht…' : 'Suchen'}
      </Button>
    </form>
  )
}
