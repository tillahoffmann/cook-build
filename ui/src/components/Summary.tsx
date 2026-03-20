import type { TaskSummary } from '../types'

interface Props {
  tasks: TaskSummary[]
}

function Stat({ count, label, color }: { count: number; label: string; color: string }) {
  if (count === 0) return null
  return (
    <span className="flex items-center gap-1 text-[12px] text-[var(--muted-foreground)]">
      <span className="w-[6px] h-[6px] rounded-full" style={{ background: color }} />
      {count} {label}
    </span>
  )
}

export function Summary({ tasks }: Props) {
  const running = tasks.filter((t) => t.running).length
  const pending = tasks.filter((t) => t.pending).length
  const fresh = tasks.filter((t) => !t.stale && !t.failed && !t.running && !t.pending).length
  const failed = tasks.filter((t) => t.failed).length
  const stale = tasks.filter((t) => t.stale && !t.failed && !t.running && t.reason !== 'never run' && !t.reason?.startsWith('always-run')).length
  const neverRun = tasks.filter((t) => t.reason === 'never run').length
  const alwaysRun = tasks.filter((t) => t.reason?.startsWith('always-run')).length

  return (
    <div className="flex items-center gap-3">
      <span className="text-[12px] text-[var(--muted-foreground)]">{tasks.length} tasks</span>
      <Stat count={pending} label="pending" color="var(--color-pending)" />
      <Stat count={running} label="running" color="var(--color-running)" />
      <Stat count={fresh} label="fresh" color="var(--color-fresh)" />
      <Stat count={stale} label="stale" color="var(--color-stale)" />
      <Stat count={failed} label="failed" color="var(--color-failed)" />
      <Stat count={neverRun} label="never run" color="var(--color-never-run)" />
      <Stat count={alwaysRun} label="always-run" color="var(--color-always-run)" />
    </div>
  )
}
