import { Sun, Moon, Github } from 'lucide-react'

export function ThemeToggle({ dark, onToggle }: { dark: boolean; onToggle: () => void }) {
  return (
    <button
      onClick={onToggle}
      className="p-1.5 rounded-md hover:bg-[var(--color-surface-raised)] transition-colors cursor-pointer text-[var(--color-text-secondary)] hover:text-[var(--color-text)]"
      title={dark ? 'Switch to light mode' : 'Switch to dark mode'}
    >
      {dark ? <Sun size={16} /> : <Moon size={16} />}
    </button>
  )
}

export function GitHubLink() {
  return (
    <a
      href="https://github.com/tillahoffmann/cook-build"
      target="_blank"
      rel="noopener noreferrer"
      className="p-1.5 rounded-md hover:bg-[var(--color-surface-raised)] transition-colors text-[var(--color-text-secondary)] hover:text-[var(--color-text)]"
      title="GitHub"
    >
      <Github size={16} />
    </a>
  )
}
