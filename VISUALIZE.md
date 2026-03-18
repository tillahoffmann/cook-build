# cook ui — Interactive DAG Viewer

## Overview

`cook ui` starts a local web server that serves an interactive DAG visualization of the build graph. The React app communicates with the cook backend via a JSON API.

## CLI

```
cook ui [pattern] [--port PORT] [--no-browser]
```

- `pattern` — optional, sets the initial search/highlight in the UI. All tasks are shown; matched tasks are highlighted/dimmed.
- `--port` — server port (default: 4200, falls back to random if taken)
- `--no-browser` — don't auto-open the browser, just print the URL
- Press ENTER or Ctrl-C to stop the server

## API Endpoints

### `GET /api/tasks`

Returns all tasks with metadata.

```json
[
  {
    "name": "compile-foo",
    "type": "ShellTask",
    "stale": true,
    "reason": "digest changed",
    "deps": ["preprocess-foo"],
    "inputs": ["src/foo.c"],
    "outputs": ["build/foo.o"],
    "cmd": "gcc -c src/foo.c -o build/foo.o",
    "extra": {}
  }
]
```

### `GET /api/edges`

Returns dependency edges for graph rendering.

```json
[
  {"from": "preprocess-foo", "to": "compile-foo"},
  {"from": "compile-foo", "to": "link"}
]
```

Direction: `from` must complete before `to` can run.

### `GET /api/tasks/:name`

Returns full detail for a single task, including execution history.

```json
{
  "name": "compile-foo",
  "type": "ShellTask",
  "stale": false,
  "reason": null,
  "deps": ["preprocess-foo"],
  "inputs": ["src/foo.c"],
  "outputs": ["build/foo.o"],
  "cmd": "gcc -c src/foo.c -o build/foo.o",
  "extra": {},
  "history": {
    "last_started": "2026-03-17T10:30:00+00:00",
    "last_succeeded": "2026-03-17T10:30:02+00:00",
    "duration": 2.031
  }
}
```

`history` is `null` if the task has never been run. History fields are only present when they have values.

### `GET /api/config`

Returns initial UI state from CLI args.

```json
{
  "pattern": "compile-*",
  "project_root": "~/projects/my-app"
}
```

### `GET /`

Serves the bundled React app.

## React App

### Tech Stack

- React + TypeScript
- React Flow (with dagre layout, native `colorMode` for dark/light)
- Tailwind CSS v4
- Lucide icons
- Vite for bundling

### Design

Apple Human Interface Guidelines-inspired:

- Neutral gray surfaces (Apple system gray scale), color used only for status indicators
- Small colored dots for task status, not colored borders or text
- Typography hierarchy through weight and size, not color
- Depth through shadows, not borders
- Consistent 10px corner radius
- SF Pro / system font with antialiasing

### Components

**Layout**: header bar + graph view (main area) + resizable detail panel (right sidebar)

**TaskNode**: neutral card with small status dot (green/red/orange/gray) + task name. Selected node gets a blue ring highlight.

**Search**: search input with glob/regex toggle. Matched tasks are highlighted; unmatched are dimmed. Initialized from CLI pattern via `/api/config`.

**TaskDetail**: compact key-value layout showing name, type, status, reason, command (with copy button), deps (orange, clickable), dependents (purple, clickable), inputs (cyan), outputs (green), history, extra. Focus button centers graph on the task.

**Summary**: task counts with colored dots. Project path displayed below.

**ThemeToggle**: dark/light mode toggle (persists to localStorage, defaults to system preference). Drives React Flow's native `colorMode`.

### File Layout

```
ui/
    package.json
    tsconfig.json
    vite.config.ts
    src/
        main.tsx
        App.tsx
        api.ts
        types.ts
        theme.css
        lib/
            utils.ts
        hooks/
            useTheme.ts
        components/
            TaskNode.tsx
            TaskDetail.tsx
            Search.tsx
            Summary.tsx
            ThemeToggle.tsx
            ResizablePanel.tsx
            ui/
                input.tsx

src/cook/
    cli/
        cmd_ui.py
    static/                 # bundled React app (not version controlled)
```

### Development

```bash
# Terminal 1: start cook API server
cook ui --no-browser --port 4200

# Terminal 2: start vite dev server with API proxy
cd ui && npm run dev
# vite proxies /api/* to localhost:4200
```

### Build & Release

- `cd ui && npm run build` outputs to `src/cook/static/`
- `src/cook/static/` is in `.gitignore` (not version controlled)
- CI builds the React app before publishing to PyPI
- `pyproject.toml` includes `static/**/*` as package data
