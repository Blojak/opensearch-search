import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

/**
 * Metadata filters. For v1 only the classification, which the user asked for.
 *
 * It is a free-text field on purpose, not a dropdown: `klassifizierung` is a
 * free string today, assigned later by an ML classifier over the police
 * taxonomy — there is no fixed set of values to offer yet. The layout leaves
 * room for further filters (Aktenzeichen, Verfahren, date range, language) to
 * dock in later.
 */
export function Filters({
  klassifizierung,
  onKlassifizierungChange,
}: {
  klassifizierung: string
  onKlassifizierungChange: (value: string) => void
}) {
  return (
    <div className="grid gap-2">
      <Label htmlFor="filter-klassifizierung">Klassifikation</Label>
      <Input
        id="filter-klassifizierung"
        value={klassifizierung}
        onChange={(event) => onKlassifizierungChange(event.target.value)}
        placeholder="z. B. VS-NfD (optional)"
      />
    </div>
  )
}
