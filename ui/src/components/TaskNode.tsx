import { Handle, Position } from '@xyflow/react'
import type { TaskSummary } from '../types'

export type TaskNodeData = TaskSummary & { dimmed: boolean; selected: boolean; highlighted: boolean }

function statusColor(task: TaskSummary): string {
  if (task.running) return 'var(--color-running)'
  if (task.pending) return 'var(--color-pending)'
  if (task.failed) return 'var(--color-failed)'
  if (!task.stale) return 'var(--color-fresh)'
  if (task.reason?.startsWith('always-run')) return 'var(--color-always-run)'
  if (task.reason === 'never run') return 'var(--color-never-run)'
  return 'var(--color-stale)'
}

export function TaskNode({ data }: { data: TaskNodeData }) {
  const color = statusColor(data)
  return (
    <div
      className={`flex items-center gap-2 rounded-[10px] px-3.5 py-2 text-[13px] min-w-[120px] transition-all${data.running ? ' animate-[breathe_3s_ease-in-out_infinite]' : ''}`}
      style={{
        ...(!data.running && { background: data.selected ? 'var(--color-surface)' : 'var(--color-surface-raised)' }),
        opacity: data.dimmed ? 0.2 : 1,
        boxShadow: data.selected
          ? '0 0 0 2px var(--color-highlight), 0 4px 16px rgba(0,0,0,0.25)'
          : data.highlighted
            ? '0 0 0 1px var(--color-highlight), 0 2px 8px rgba(0,0,0,0.15)'
            : '0 1px 3px rgba(0,0,0,0.2)',
      }}
    >
      <Handle type="target" position={Position.Top} className="!bg-transparent !border-0 !w-0 !h-0" />
      <span
        className="w-[9px] h-[9px] flex-shrink-0 rounded-full"
        style={
          data.running
            ? { border: `1.5px solid ${color}`, borderTopColor: 'transparent', animation: 'spin 0.8s linear infinite' }
            : { background: color }
        }
      />
      <span className="font-medium" style={{ color: 'var(--color-text)' }}>
        {data.name}
      </span>
      <Handle type="source" position={Position.Bottom} className="!bg-transparent !border-0 !w-0 !h-0" />
    </div>
  )
}
