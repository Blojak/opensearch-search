import { DOC_TYPES, type FilterState } from '@/lib/filters'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

const selectClass =
  'flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs ' +
  'outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50'

export function Filters({
  value,
  onChange,
}: {
  value: FilterState
  onChange: (next: FilterState) => void
}) {
  function set(patch: Partial<FilterState>) {
    onChange({ ...value, ...patch })
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="grid gap-2">
        <Label htmlFor="filter-klassifizierung">Klassifikation</Label>
        <Input
          id="filter-klassifizierung"
          value={value.klassifizierung}
          onChange={(e) => set({ klassifizierung: e.target.value })}
          placeholder="z. B. Gutachten (optional)"
        />
      </div>

      <div className="grid gap-2">
        <Label htmlFor="filter-doctype">Dokumententyp</Label>
        <select
          id="filter-doctype"
          className={selectClass}
          value={value.docType}
          onChange={(e) => set({ docType: e.target.value })}
        >
          <option value="">Alle Typen</option>
          {DOC_TYPES.map((t) => (
            <option key={t.key} value={t.key}>
              {t.label}
            </option>
          ))}
        </select>
      </div>

      <div className="grid gap-2">
        <Label htmlFor="filter-from">Erstellt von</Label>
        <Input
          id="filter-from"
          type="date"
          value={value.createdFrom}
          max={value.createdTo || undefined}
          onChange={(e) => set({ createdFrom: e.target.value })}
        />
      </div>

      <div className="grid gap-2">
        <Label htmlFor="filter-to">Erstellt bis</Label>
        <Input
          id="filter-to"
          type="date"
          value={value.createdTo}
          min={value.createdFrom || undefined}
          onChange={(e) => set({ createdTo: e.target.value })}
        />
      </div>
    </div>
  )
}
