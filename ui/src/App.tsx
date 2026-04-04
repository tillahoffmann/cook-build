import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Node,
  type Edge as FlowEdge,
  type NodeTypes,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import dagre from '@dagrejs/dagre'
import { fetchTasks, fetchEdges, fetchConfig, pollTasks, pollEdges } from './api'
import type { TaskSummary, Edge, AppConfig } from './types'
import { TaskNode } from './components/TaskNode'
import { TaskDetail } from './components/TaskDetail'
import { Search } from './components/Search'
import { Summary } from './components/Summary'
import { ChefHat } from 'lucide-react'
import { ThemeToggle, GitHubLink } from './components/ThemeToggle'
import { useTheme } from './hooks/useTheme'
import { ResizablePanel } from './components/ResizablePanel'

const nodeTypes: NodeTypes = { task: TaskNode as any }


function matchesPattern(name: string, pattern: string, isRegex: boolean): boolean {
  if (!pattern) return true
  if (isRegex) {
    try {
      return new RegExp(pattern).test(name)
    } catch {
      return false
    }
  }
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&')
  const re = new RegExp('^' + escaped.replace(/\*/g, '.*').replace(/\?/g, '.') + '$')
  return re.test(name)
}

function layoutGraph(
  tasks: TaskSummary[],
  edges: Edge[],
  matchedNames: Set<string>,
  hasPattern: boolean,
  selectedTask: string | null,
  highlightedTask: string | null,
): { nodes: Node[]; edges: FlowEdge[] } {
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'TB', nodesep: 40, ranksep: 60 })

  for (const task of tasks) {
    g.setNode(task.name, { width: 150, height: 50 })
  }
  for (const edge of edges) {
    g.setEdge(edge.from, edge.to)
  }

  dagre.layout(g)

  const nodes: Node[] = tasks.map((task) => {
    const pos = g.node(task.name)
    return {
      id: task.name,
      type: 'task',
      position: { x: pos.x - 75, y: pos.y - 25 },
      data: {
        ...task,
        dimmed: hasPattern && !matchedNames.has(task.name),
        selected: task.name === selectedTask,
        highlighted: task.name === highlightedTask,
      },
    }
  })

  const flowEdges: FlowEdge[] = edges.map((e) => ({
    id: `${e.from}->${e.to}`,
    source: e.from,
    target: e.to,
    style: { stroke: 'var(--color-edge)' },
    markerEnd: { type: 'arrowclosed' as const, color: 'var(--color-edge)' },
  }))

  return { nodes, edges: flowEdges }
}

function Flow() {
  const { dark, toggle: toggleTheme } = useTheme()
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [edgeData, setEdgeData] = useState<Edge[]>([])
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [selectedTask, setSelectedTask] = useState<string | null>(null)
  const [highlightedTask, setHighlightedTask] = useState<string | null>(null)
  const [pattern, setPattern] = useState('')
  const [isRegex, setIsRegex] = useState(false)
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<FlowEdge>([])

  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    // Initial load
    Promise.all([fetchTasks(), fetchEdges(), fetchConfig()])
      .then(([t, e, c]) => {
        setTasks(t)
        setEdgeData(e)
        setConfig(c)
        if (c.pattern) setPattern(c.pattern)
        document.title = `cook — ${c.project_root}`
      })
      .catch((e) => setLoadError(e.message))

    // Poll for updates — waits for response before scheduling next poll
    let cancelled = false
    function poll() {
      if (cancelled) return
      Promise.all([pollTasks(), pollEdges()])
        .then(([t, e]) => {
          if (t) setTasks(t)
          if (e) setEdgeData(e)
        })
        .catch(() => {})
        .finally(() => {
          if (!cancelled) setTimeout(poll, 3000)
        })
    }
    const timeout = setTimeout(poll, 3000)
    return () => { cancelled = true; clearTimeout(timeout) }
  }, [])

  const matchedNames = useMemo(() => {
    if (!pattern) return new Set<string>()
    return new Set(tasks.filter((t) => matchesPattern(t.name, pattern, isRegex)).map((t) => t.name))
  }, [tasks, pattern, isRegex])

  useEffect(() => {
    if (tasks.length === 0) return
    const { nodes: n, edges: e } = layoutGraph(tasks, edgeData, matchedNames, !!pattern, selectedTask, highlightedTask)
    setNodes(n)
    setEdges(e)
  }, [tasks, edgeData, matchedNames, selectedTask, highlightedTask, setNodes, setEdges])

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    setSelectedTask(node.id)
  }, [])

  const { setCenter } = useReactFlow()

  const handleNavigate = useCallback((name: string) => {
    setSelectedTask(name)
  }, [])

  const handleFocus = useCallback((name: string) => {
    const node = nodes.find((n) => n.id === name)
    if (node) {
      setCenter(node.position.x + 75, node.position.y + 25, { zoom: 1.5, duration: 400 })
    }
  }, [nodes, setCenter])

  const handlePatternChange = useCallback((p: string, regex: boolean) => {
    setPattern(p)
    setIsRegex(regex)
  }, [])

  if (loadError) {
    return (
      <div className="h-screen w-screen flex items-center justify-center" style={{ background: 'var(--color-bg)' }}>
        <div className="text-center space-y-2">
          <div className="text-[var(--color-stale)] text-sm">Failed to load</div>
          <div className="text-[var(--muted-foreground)] text-xs">{loadError}</div>
        </div>
      </div>
    )
  }

  return (
    <div className="h-screen w-screen flex flex-col" style={{ background: 'var(--color-bg)' }}>
      <div
        className="flex items-center gap-6 px-5 py-2.5"
        style={{ background: 'var(--color-surface)', boxShadow: '0 1px 0 var(--border)' }}
      >
        <span className="flex items-center gap-2 font-semibold text-sm">
          <ChefHat size={18} />
          cook
        </span>
        <div className="max-w-lg w-80">
          <Search initialPattern={config?.pattern ?? ''} onPatternChange={handlePatternChange} />
        </div>
        <div className="flex flex-col items-start text-xs gap-0.5">
          <Summary tasks={tasks} />
          <span className="text-[var(--color-text-secondary)] font-mono text-[11px]">
            {config?.project_root ?? ''}
          </span>
        </div>
        <div className="flex-1" />
        <div className="flex items-center gap-1.5">
          <ThemeToggle dark={dark} onToggle={toggleTheme} />
          <GitHubLink />
        </div>
      </div>

      <div className="flex-1 flex">
        <div className="flex-1">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={onNodeClick}
            nodeTypes={nodeTypes}
            fitView
            nodesConnectable={false}
            colorMode={dark ? 'dark' : 'light'}

          >
            <Background gap={20} />
            <Controls showInteractive={false} />
            <MiniMap />
          </ReactFlow>
        </div>

        {selectedTask && (
          <ResizablePanel>
            <TaskDetail
              taskName={selectedTask}
              edges={edgeData}
              onNavigate={handleNavigate}
              onFocus={handleFocus}
              onHighlight={setHighlightedTask}
              onClose={() => setSelectedTask(null)}
            />
          </ResizablePanel>
        )}
      </div>
    </div>
  )
}

export default function App() {
  return (
    <ReactFlowProvider>
      <Flow />
    </ReactFlowProvider>
  )
}
