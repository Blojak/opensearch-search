import { FolderOpen } from 'lucide-react'
import { CopyButton } from './CopyButton'

/** The document's storage location plus a copy button. */
export function StoragePath({ path }: { path: string }) {
  return (
    <>
      <span
        className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground"
        title="Ablageort des Dokuments"
      >
        <FolderOpen className="size-3.5 shrink-0" />
        {path}
      </span>
      <CopyButton value={path} label="Ablageort kopieren" />
    </>
  )
}
