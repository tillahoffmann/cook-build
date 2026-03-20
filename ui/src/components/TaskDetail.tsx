import { useEffect, useState } from 'react'
import {
  X, Copy, Check, Locate, ArrowUpFromLine, ArrowDownFromLine,
  FileInput, FileOutput, CheckCircle, XCircle, Clock, Settings2,
} from 'lucide-react'
import { fetchTaskDetail } from '../api'
import type { TaskDetail as TaskDetailType, Edge } from '../types'

interface Props {
  taskName: string
  edges: Edge[]
  onNavigate: (name: string) => void
  onFocus: (name: string) => void
  onHighlight: (name: string | null) => void
  onClose: () => void
}

export function TaskDetail({ taskName, edges, onNavigate, onFocus, onHighlight, onClose }: Props) {
  const [detail, setDetail] = useState<TaskDetailType | null>(null)
  const [copied, setCopied] = useState(false)

  const copyCommand = (cmd: string) => {
    navigator.clipboard.writeText(cmd)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setDetail(null)
    setError(null)
    fetchTaskDetail(taskName)
      .then(setDetail)
      .catch((e) => setError(e.message))
  }, [taskName])

  if (error) {
    return <div className="p-4 text-[var(--color-stale)]">Error: {error}</div>
  }

  if (!detail) {
    return <div className="p-4 text-[var(--muted-foreground)]">Loading...</div>
  }

  const dependents = edges.filter((e) => e.from === taskName).map((e) => e.to)
  const isFailed = detail.history?.last_failed && (!detail.history?.last_succeeded || detail.history.last_failed > detail.history.last_succeeded)
  const isInterrupted = isFailed && detail.history?.error === 'interrupted'
  const statusColor = isFailed ? 'var(--color-failed)' : detail.stale ? 'var(--color-stale)' : 'var(--color-fresh)'
  const statusText = isInterrupted ? 'Interrupted' : isFailed ? 'Failed' : detail.stale ? 'Stale' : 'Up to date'

  return (
    <div className="h-full overflow-y-auto p-4 space-y-3 text-[13px]">
      {/* Header */}
      <div className="flex justify-between items-start gap-2">
        <div className="min-w-0">
          <div className="font-semibold text-[15px] break-all leading-tight">{detail.name}</div>
          <div className="text-[12px] text-[var(--muted-foreground)] mt-1 space-y-0.5">
            <div>{detail.type}</div>
            <div className="flex items-center gap-1">
              <span className="w-[6px] h-[6px] rounded-full" style={{ background: statusColor }} />
              {statusText}
              {detail.reason && <span>— {detail.reason}</span>}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-0.5 flex-shrink-0">
          <button
            onClick={() => onFocus(taskName)}
            className="text-[var(--muted-foreground)] hover:text-[var(--foreground)] cursor-pointer p-1 rounded-[8px] hover:bg-[var(--muted)] transition-colors"
            title="Focus in graph"
          >
            <Locate size={14} />
          </button>
          <button
            onClick={onClose}
            className="text-[var(--muted-foreground)] hover:text-[var(--foreground)] cursor-pointer p-1 rounded-[8px] hover:bg-[var(--muted)] transition-colors"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* Command */}
      {detail.cmd && (
        <div className="relative">
          <pre className="text-[12px] bg-[var(--background)] px-3 py-2 pr-9 rounded-[10px] overflow-x-auto whitespace-pre-wrap break-all leading-relaxed font-mono text-[var(--foreground)]">
            <span className="text-[var(--muted-foreground)]">$ </span>{detail.cmd}
          </pre>
          <button
            onClick={() => copyCommand(detail.cmd!)}
            className="absolute top-2 right-2 p-1 rounded-[6px] cursor-pointer text-[var(--muted-foreground)] hover:text-[var(--foreground)] hover:bg-[var(--muted)] transition-colors"
            title="Copy command"
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
          </button>
        </div>
      )}

      {/* Key-value pairs */}
      <table className="text-[12px]">
        <tbody>
          {detail.deps.length > 0 && (
            <tr className="align-top">
              <td className="text-[var(--muted-foreground)] pr-3 py-[3px] whitespace-nowrap font-normal">
                <span className="inline-flex items-center gap-1"><ArrowUpFromLine size={11} /> dependencies</span>
              </td>
              <td className="py-[3px]">
                {detail.deps.map((dep, i) => (
                  <span key={dep}>
                    {i > 0 && ', '}
                    <button
                      onClick={() => onNavigate(dep)}
                      onMouseEnter={() => onHighlight(dep)}
                      onMouseLeave={() => onHighlight(null)}
                      className="text-[var(--color-deps)] hover:underline cursor-pointer"
                    >{dep}</button>
                  </span>
                ))}
              </td>
            </tr>
          )}
          {dependents.length > 0 && (
            <tr className="align-top">
              <td className="text-[var(--muted-foreground)] pr-3 py-[3px] whitespace-nowrap font-normal">
                <span className="inline-flex items-center gap-1"><ArrowDownFromLine size={11} /> dependents</span>
              </td>
              <td className="py-[3px]">
                {dependents.map((dep, i) => (
                  <span key={dep}>
                    {i > 0 && ', '}
                    <button
                      onClick={() => onNavigate(dep)}
                      onMouseEnter={() => onHighlight(dep)}
                      onMouseLeave={() => onHighlight(null)}
                      className="text-[var(--color-dependents)] hover:underline cursor-pointer"
                    >{dep}</button>
                  </span>
                ))}
              </td>
            </tr>
          )}
          {detail.inputs.length > 0 && (
            <tr className="align-top">
              <td className="text-[var(--muted-foreground)] pr-3 py-[3px] whitespace-nowrap font-normal">
                <span className="inline-flex items-center gap-1"><FileInput size={11} /> inputs</span>
              </td>
              <td className="py-[3px] font-mono text-[var(--color-cook-input)]">{detail.inputs.join(', ')}</td>
            </tr>
          )}
          {detail.outputs.length > 0 && (
            <tr className="align-top">
              <td className="text-[var(--muted-foreground)] pr-3 py-[3px] whitespace-nowrap font-normal">
                <span className="inline-flex items-center gap-1"><FileOutput size={11} /> outputs</span>
              </td>
              <td className="py-[3px] font-mono text-[var(--color-output)]">{detail.outputs.join(', ')}</td>
            </tr>
          )}
          {detail.history?.last_succeeded && (
            <tr className="align-top">
              <td className="text-[var(--muted-foreground)] pr-3 py-[3px] whitespace-nowrap font-normal">
                <span className="inline-flex items-center gap-1"><CheckCircle size={11} /> succeeded</span>
              </td>
              <td className="py-[3px]">
                {new Date(detail.history.last_succeeded).toLocaleString()}
                {detail.history.duration != null && (
                  <span className="text-[var(--muted-foreground)]"> ({detail.history.duration.toFixed(1)}s)</span>
                )}
              </td>
            </tr>
          )}
          {detail.history?.last_failed && (
            <tr className="align-top">
              <td className="text-[var(--muted-foreground)] pr-3 py-[3px] whitespace-nowrap font-normal">
                <span className="inline-flex items-center gap-1">
                  <XCircle size={11} /> {detail.history.error === 'interrupted' ? 'interrupted' : 'failed'}
                </span>
              </td>
              <td className="py-[3px]">{new Date(detail.history.last_failed).toLocaleString()}</td>
            </tr>
          )}
          {!detail.history && (
            <tr>
              <td className="text-[var(--muted-foreground)] pr-3 py-[3px] font-normal">
                <span className="inline-flex items-center gap-1"><Clock size={11} /> history</span>
              </td>
              <td className="py-[3px] text-[var(--muted-foreground)] italic">never run</td>
            </tr>
          )}
          {Object.keys(detail.extra).length > 0 && (
            <tr className="align-top">
              <td className="text-[var(--muted-foreground)] pr-3 py-[3px] whitespace-nowrap font-normal">
                <span className="inline-flex items-center gap-1"><Settings2 size={11} /> extra</span>
              </td>
              <td className="py-[3px] font-mono">{JSON.stringify(detail.extra)}</td>
            </tr>
          )}
        </tbody>
      </table>

      {/* Error (skip for "interrupted" — already shown in the history label) */}
      {detail.history?.error && detail.history.error !== 'interrupted' && (
        <pre className="text-[12px] bg-[var(--background)] px-3 py-2 rounded-[10px] text-[var(--color-failed)] whitespace-pre-wrap leading-relaxed font-mono">
          {detail.history.error}
        </pre>
      )}
    </div>
  )
}
