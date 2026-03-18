import type { TaskSummary, TaskDetail, Edge, AppConfig } from './types'

const BASE = ''

// Track Last-Modified per endpoint for conditional requests
const lastModified: Record<string, string> = {}

async function fetchJSON<T>(url: string): Promise<T> {
  const res = await fetch(`${BASE}${url}`)
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`)
  }
  const lm = res.headers.get('Last-Modified')
  if (lm) lastModified[url] = lm
  return res.json()
}

async function fetchIfModified<T>(url: string): Promise<T | null> {
  const headers: Record<string, string> = {}
  if (lastModified[url]) {
    headers['If-Modified-Since'] = lastModified[url]
  }
  const res = await fetch(`${BASE}${url}`, { headers })
  if (res.status === 304) return null
  if (!res.ok) throw new Error(`API error: ${res.status}`)
  const lm = res.headers.get('Last-Modified')
  if (lm) lastModified[url] = lm
  return res.json()
}

export function fetchTasks(): Promise<TaskSummary[]> {
  return fetchJSON('/api/tasks')
}

export function fetchEdges(): Promise<Edge[]> {
  return fetchJSON('/api/edges')
}

export function pollTasks(): Promise<TaskSummary[] | null> {
  return fetchIfModified('/api/tasks')
}

export function pollEdges(): Promise<Edge[] | null> {
  return fetchIfModified('/api/edges')
}

export function fetchTaskDetail(name: string): Promise<TaskDetail> {
  return fetchJSON(`/api/tasks/${encodeURIComponent(name)}`)
}

export function fetchConfig(): Promise<AppConfig> {
  return fetchJSON('/api/config')
}
