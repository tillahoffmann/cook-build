import { Handle, Position } from '@xyflow/react'
import type { TaskSummary } from '../types'

export type TaskNodeData = TaskSummary & { dimmed: boolean; selected: boolean }

function statusColor(task: TaskSummary): string {
  if (!task.stale) return 'var(--color-fresh)'
  if (task.reason?.startsWith('always-run')) return 'var(--color-always-run)'
  if (task.reason === 'never run') return 'var(--color-never-run)'
  return 'var(--color-stale)'
}

export function TaskNode({ data }: { data: TaskNodeData }) {
  const color = statusColor(data)
  return (
    <div
      className="flex items-center gap-2 rounded-[10px] px-3.5 py-2 text-[13px] min-w-[120px] transition-all"
      style={{
        background: data.selected ? 'var(--color-surface)' : 'var(--color-surface-raised)',
        opacity: data.dimmed ? 0.2 : 1,
        boxShadow: data.selected
          ? '0 0 0 2px var(--color-highlight), 0 4px 16px rgba(0,0,0,0.25)'
          : '0 1px 3px rgba(0,0,0,0.2)',
      }}
    >
      <Handle type="target" position={Position.Top} className="!bg-transparent !border-0 !w-0 !h-0" />
      <span
        className="w-[7px] h-[7px] rounded-full flex-shrink-0"
        style={{ background: color }}
      />
      <span className="font-medium" style={{ color: 'var(--color-text)' }}>
        {data.name}
      </span>
      <Handle type="source" position={Position.Bottom} className="!bg-transparent !border-0 !w-0 !h-0" />
    </div>
  )
}
