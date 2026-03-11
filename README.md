# 🧑‍🍳 Cook

A Python-native build system with content-hash-based incremental builds. Define tasks in plain Python, and Cook handles dependency ordering, parallel execution, and skipping unchanged work.

## Quick start

Install cook by running `pip install cook-build` or your favorite Python package manager. Then create a `recipe.py` in your project root:

```python
from pathlib import Path
from cook import get_context

ctx = get_context()

sources = sorted(Path("src").glob("*.c"))
objects = []
for src in sources:
    objects.append(ctx.sh(
        name=f"compile-{src.stem}",
        cmd=f"gcc -c {src} -o {src.with_suffix('.o')}",
        inputs=[src], outputs=[src.with_suffix(".o")],
    ))

ctx.sh(
    name="link",
    cmd=f"gcc {' '.join(str(o.outputs[0]) for o in objects)} -o build/app",
    inputs=objects, outputs=[Path("build/app")]
)
```

Then run:

```bash
cook exec "*"
```

On the second run, unchanged tasks are skipped automatically.

## CLI

```bash
cook exec [pattern]              # run tasks matching glob pattern
cook exec -n [pattern]           # show what would run (--dry-run)
cook exec -k [pattern]           # keep going on failure (--keep-going)
cook exec -j4 [pattern]          # run up to 4 tasks in parallel (--jobs)
cook inspect [pattern]           # show dependency graph and staleness
cook invalidate <pattern>        # force tasks to re-run next time
```

Patterns use glob syntax (`fnmatch`). Dependencies of matched tasks are always included.

## Defining tasks

Use `ctx.sh()` to create shell tasks:

```python
ctx = get_context()

obj = ctx.sh(
    name="compile-foo",
    cmd="gcc -c foo.c -o foo.o",
    inputs=["foo.c"],
    outputs=["foo.o"],
)

# depend on another task by putting it in inputs
ctx.sh(
    name="link",
    cmd="gcc foo.o -o app",
    inputs=[obj, "foo.o"],
    outputs=["app"],
)
```

- **`inputs`** -- file paths and/or other tasks. Files are hashed for change detection; tasks become dependencies.
- **`outputs`** -- files the task produces. Cook verifies they exist after execution.
- Tasks with no outputs always run (useful for tests, linters, etc.).

## Custom task types

Subclass `Task` as a dataclass and register a handler with the executor:

```python
>>> from dataclasses import dataclass
>>> from cook import Task
>>> from cook.executor import LocalExecutor

>>> @dataclass
... class DownloadTask(Task):
...     url: str = ""

>>> async def handle_download(executor, task):
...     ...  # your logic here

>>> _ = LocalExecutor.register_handler(handle_download, task_type=DownloadTask)

```

## Configuration

Optional `cook.toml` in your project root:

```toml
[cook]
recipe = "recipe.py"        # default recipe file
default = "build-*"         # default pattern for bare `cook exec`

[cook.local]
max_concurrent = 8          # parallel task limit (default: 1)
```

All settings have sensible defaults. CLI flags override config values.

## How staleness works

Cook computes a content hash for each task based on:

1. The task's own fields (command, name, etc.)
2. The contents of its input files
3. The hashes of its dependencies

If the hash matches the last successful run and all outputs exist, the task is skipped. Changed inputs, changed commands, or missing outputs all trigger re-execution.
