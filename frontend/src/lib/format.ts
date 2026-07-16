/** Format an ISO-8601 date as a short German date, or return it unchanged. */
export function formatDate(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleDateString('de-DE', { year: 'numeric', month: 'short', day: 'numeric' })
}
