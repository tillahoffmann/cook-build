import type { TaskSummary, TaskDetail, Edge, AppConfig } from './types'

const BASE = ''

async function fetchJSON<T>(url: string): Promise<T> {
  const res = await fetch(`${BASE}${url}`)
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`)
  }
  return res.json()
}

export function fetchTasks(): Promise<TaskSummary[]> {
  return fetchJSON('/api/tasks')
}

export function fetchEdges(): Promise<Edge[]> {
  return fetchJSON('/api/edges')
}

export function fetchTaskDetail(name: string): Promise<TaskDetail> {
  return fetchJSON(`/api/tasks/${encodeURIComponent(name)}`)
}

export function fetchConfig(): Promise<AppConfig> {
  return fetchJSON('/api/config')
}
