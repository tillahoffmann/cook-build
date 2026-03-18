import { useCallback, useRef, useState, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  defaultWidth?: number
  minWidth?: number
  maxWidth?: number
}

export function ResizablePanel({
  children,
  defaultWidth = 320,
  minWidth = 200,
  maxWidth = 600,
}: Props) {
  const [width, setWidth] = useState(defaultWidth)
  const panelRef = useRef<HTMLDivElement>(null)
  const startX = useRef(0)
  const startWidth = useRef(0)

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      startX.current = e.clientX
      startWidth.current = width
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'

      const onMouseMove = (e: MouseEvent) => {
        const delta = startX.current - e.clientX
        const newWidth = Math.min(maxWidth, Math.max(minWidth, startWidth.current + delta))
        // Mutate DOM directly to avoid re-rendering ReactFlow on every frame
        if (panelRef.current) {
          panelRef.current.style.width = `${newWidth}px`
        }
      }

      const onMouseUp = (e: MouseEvent) => {
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
        document.removeEventListener('mousemove', onMouseMove)
        document.removeEventListener('mouseup', onMouseUp)
        // Commit final width to state
        const delta = startX.current - e.clientX
        const finalWidth = Math.min(maxWidth, Math.max(minWidth, startWidth.current + delta))
        setWidth(finalWidth)
      }

      document.addEventListener('mousemove', onMouseMove)
      document.addEventListener('mouseup', onMouseUp)
    },
    [width, minWidth, maxWidth],
  )

  return (
    <div className="flex h-full">
      <div
        onMouseDown={onMouseDown}
        className="w-[3px] cursor-col-resize hover:bg-[var(--color-highlight)] transition-colors flex-shrink-0"
        style={{ background: 'transparent' }}
      />
      <div
        ref={panelRef}
        className="overflow-hidden flex-shrink-0"
        style={{
          width,
          background: 'var(--card)',
          boxShadow: '-1px 0 0 var(--border)',
        }}
      >
        {children}
      </div>
    </div>
  )
}
