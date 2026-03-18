import { useEffect, useState } from 'react'
import { Search as SearchIcon, Regex } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Input } from '@/components/ui/input'

interface Props {
  initialPattern: string
  onPatternChange: (pattern: string, isRegex: boolean) => void
}

export function Search({ initialPattern, onPatternChange }: Props) {
  const [pattern, setPattern] = useState(initialPattern)
  const [isRegex, setIsRegex] = useState(false)

  useEffect(() => {
    if (initialPattern && !pattern) {
      setPattern(initialPattern)
    }
  }, [initialPattern])

  const handleChange = (value: string) => {
    setPattern(value)
    onPatternChange(value, isRegex)
  }

  const toggleRegex = () => {
    const next = !isRegex
    setIsRegex(next)
    onPatternChange(pattern, next)
  }

  return (
    <div className="relative flex items-center">
      <SearchIcon
        size={14}
        className="absolute left-3 text-[var(--color-text-secondary)] pointer-events-none"
      />
      <Input
        value={pattern}
        onChange={(e) => handleChange(e.target.value)}
        placeholder="Filter tasks..."
        className="pl-9 pr-10"
      />
      <button
        onClick={toggleRegex}
        className={cn(
          'absolute right-2 p-1 rounded-md cursor-pointer transition-colors',
          isRegex
            ? 'text-[var(--color-bg)] bg-[var(--color-highlight)]'
            : 'text-[var(--color-text-secondary)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-raised)]',
        )}
        title="Toggle regex matching"
      >
        <Regex size={14} />
      </button>
    </div>
  )
}
