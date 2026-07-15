import { useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { Button } from '@/components/ui/button'

/**
 * Copies `value` to the clipboard and briefly confirms with a check mark.
 *
 * navigator.clipboard needs a secure context — it works on localhost and over
 * https, but not plain http, where the copy silently no-ops.
 */
export function CopyButton({ value, label = 'Kopieren' }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // clipboard unavailable (non-secure context / denied) — nothing to do
    }
  }

  return (
    <Button
      variant="ghost"
      size="icon"
      className="size-6 shrink-0"
      onClick={copy}
      aria-label={label}
      title={copied ? 'Kopiert' : label}
    >
      {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
    </Button>
  )
}
