export interface TaskSummary {
  name: string
  type: string
  stale: boolean
  failed?: boolean
  running?: boolean
  pending?: boolean
  reason: string | null
  deps: string[]
  inputs: string[]
  outputs: string[]
  cmd?: string
  extra: Record<string, unknown>
}

export interface TaskHistory {
  last_started?: string
  last_succeeded?: string
  last_failed?: string
  duration?: number
  error?: string
}

export interface TaskDetail extends TaskSummary {
  history: TaskHistory | null
}

export interface Edge {
  from: string
  to: string
}

export interface AppConfig {
  pattern: string | null
  project_root: string
}
