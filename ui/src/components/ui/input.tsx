import * as React from "react"

import { cn } from "@/lib/utils"

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      className={cn(
        "w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-secondary)] transition-colors outline-none focus:ring-1 focus:ring-[var(--color-highlight)] focus:border-[var(--color-highlight)]",
        className
      )}
      {...props}
    />
  )
}

export { Input }
