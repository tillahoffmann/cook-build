# 🧑‍🍳 Cook

A Python-native build system with content-hash-based incremental builds. Define tasks in plain Python, and Cook handles dependency ordering, parallel execution, and skipping unchanged work.

## Quick start

Install cook by running `pip install cook-build` or your favorite Python package manager. Then create a `recipe.py` in your project root:

```python
from pathlib import Path
from cook import sh

sources = sorted(Path("src").glob("*.c"))
objects = []
for src in sources:
    objects.append(sh(
        name=f"compile-{src.stem}",
        cmd=f"gcc -c {src} -o {src.with_suffix('.o')}",
        inputs=[src], outputs=[src.with_suffix(".o")],
    ))

sh(
    name="link",
    cmd=f"gcc {' '.join(str(o.outputs[0]) for o in objects)} -o build/app",
    inputs=objects, outputs=[Path("build/app")]
)
```

Then run:

```bash
cook run "*"
```

On the second run, unchanged tasks are skipped automatically:

```
[1/3] Fresh   compile-foo
[2/3] Fresh   compile-bar
[3/3] Fresh   link

Build finished: 3 fresh in 0.0s
```

## CLI

```bash
cook run [pattern]               # run tasks matching glob patterncook run -n [pattern]            # show what would run (--dry-run)
cook run -k [pattern]            # keep going on failure (--keep-going)
cook run -j4 [pattern]           # run up to 4 tasks in parallel (--jobs)
cook run -s [pattern]            # stream task output to terminal (--stream)
cook build <output-pattern>      # run tasks that produce matching outputs
cook inspect [pattern]           # show dependency graph and staleness
cook inspect --json [pattern]    # JSON lines output
cook list [pattern]              # list task namescook list -s [pattern]           # list only stale tasks (--stale)
cook list --json [pattern]       # JSON lines output
cook invalidate <pattern>        # force tasks to re-run next time
cook validate <pattern>          # mark tasks as up-to-date without running
```

Patterns use glob syntax (`fnmatch`). Use `-r` for regex. Dependencies of matched tasks are always included.

Global flags: `-v` (verbose), `-q` (quiet), `--color=auto|always|never`, `-f` (recipe file), `-c` (config file), `--version`.

## Defining tasks

Use `sh()` to create shell tasks:

```python
from cook import sh

obj = sh(
    name="compile-foo",
    cmd="gcc -c foo.c -o foo.o",
    inputs=["foo.c"],
    outputs=["foo.o"],
)

# depend on another task by putting it in inputs
sh(
    name="link",
    cmd="gcc foo.o -o app",
    inputs=[obj, "foo.o"],
    outputs=["app"],
)
```

- **`inputs`** -- file paths and/or other tasks. Files are hashed for change detection; tasks become dependencies.
- **`outputs`** -- files the task produces. Cook verifies they exist after execution.
- Tasks with no outputs always run (useful for tests, linters, etc.).

### Implicit dependencies

If a task's file input matches another task's declared output, Cook automatically adds a dependency. This is equivalent to how Make resolves file-based dependencies:

```python
# No need to pass the compile task object — Cook resolves the dependency
# automatically because link's input "foo.o" matches compile's output "foo.o".
sh(name="compile", cmd="gcc -c foo.c -o foo.o", inputs=["foo.c"], outputs=["foo.o"])
sh(name="link", cmd="gcc foo.o -o app", inputs=["foo.o"], outputs=["app"])
```

### Path resolution

All relative paths in `inputs` and `outputs` are resolved relative to the recipe file's directory. This means recipes are portable — moving the project directory doesn't force a full rebuild.

## Custom task types

Subclass `Task` as a dataclass and register a handler with the executor:

```python
>>> from dataclasses import dataclass
>>> from cook import Task, get_context
>>> from cook.executor import LocalExecutor

>>> @dataclass
... class DownloadTask(Task):
...     url: str = "https://example.com"

>>> async def handle_download(executor, task):
...     ...  # your logic here

>>> _ = LocalExecutor.register_handler(handle_download, task_type=DownloadTask)

>>> ctx = get_context()
>>> _ = ctx.register(DownloadTask(name="fetch-data", url="https://example.com/data.csv", outputs=["data.csv"]))

```

## Configuration

Optional `cook.toml` in your project root:

```toml
[cook]
recipe = "recipe.py"        # default recipe file
executor = "local"          # or "slurm"
default = "build-*"         # default pattern for bare `cook run`

[cook.local]
max_concurrent = 8          # parallel task limit (default: 1)

[cook.slurm]
max_concurrent = 64
poll_interval = 2.0

[cook.slurm.defaults]      # default sbatch flags for all slurm tasks
mem = "4G"
partition = "batch"
```

All settings have sensible defaults. CLI flags override config values.

Per-task slurm options override defaults:

```python
sh(name="train", cmd="python train.py", slurm={"mem": "32G", "gres": "gpu:1"})
```

## How staleness works

Cook computes a content hash for each task based on:

1. The task's own fields (command, name, etc.)
2. The contents of its input files
3. The hashes of its dependencies

If the hash matches the last successful run and all outputs exist, the task is skipped. Changed inputs, changed commands, or missing outputs all trigger re-execution.

Tasks with no outputs (tests, linters) always run. If an always-run task is a dependency, all downstream tasks also re-run.
