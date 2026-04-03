# 🧑‍🍳 Cook

A Python-native build system with content-hash-based incremental builds. Define tasks in plain Python, and Cook handles dependency ordering, parallel execution, and skipping unchanged work.

> [!WARNING]
> Cook v1.0.0 is a complete rewrite of the build system with different syntax. Pin `cook-build<1.0.0` to retain the old `cook.create_task` syntax.

## Quick start

Install cook by running `pip install cook-build` or your favorite Python package manager. Then create a `recipe.py` in your project root:

```python
>>> from pathlib import Path
>>> from cook import sh, group

>>> sources = list(Path("example").glob("*.c"))
>>> objects = [src.with_suffix(".o") for src in sources]

>>> with group("compile"):
...     for src, obj in zip(sources, objects):
...         sh(
...             name=f"compile-{src.stem}",
...             cmd=f"gcc -c {src} -o {obj}",
...             inputs=[src], outputs=[obj],
...         )
ShellTask(name='compile-util', inputs=[PosixPath('example/util.c')], outputs=[PosixPath('example/util.o')], extra={}, cmd='gcc -c example/util.c -o example/util.o', env=None, cwd=None)
ShellTask(name='compile-main', inputs=[PosixPath('example/main.c')], outputs=[PosixPath('example/main.o')], extra={}, cmd='gcc -c example/main.c -o example/main.o', env=None, cwd=None)

>>> sh(
...     name="link",
...     cmd=f"gcc {' '.join(str(o) for o in objects)} -o build/app",
...     inputs=objects, outputs=["build/app"],
... )
ShellTask(name='link', inputs=[PosixPath('example/util.o'), PosixPath('example/main.o')], outputs=['build/app'], extra={}, cmd='gcc example/util.o example/main.o -o build/app', env=None, cwd=None)

```

Then run:

```bash
cook run "*"
```

On the second run, unchanged tasks are skipped automatically:

```
[1/3] Fresh   compile-util
[2/3] Fresh   compile-main
[3/3] Fresh   link

Build finished: 3 fresh in 0.0s
```

## Tasks

Use `sh()` to create shell tasks. **inputs** are file paths and/or other tasks — files are hashed for change detection, tasks become dependencies. **outputs** are files the task produces — Cook verifies they exist after execution. Tasks with no outputs always run (useful for tests, linters).

If a file input matches another task's declared output, Cook automatically adds the dependency — no need to pass the task object explicitly:

```python
>>> _ = sh(name="cc-foo", cmd="gcc -c foo.c -o foo.o", inputs=["foo.c"], outputs=["foo.o"])
>>> _ = sh(name="link-foo", cmd="gcc foo.o -o app", inputs=["foo.o"], outputs=["app"])

```

Use `group()` to organize related tasks. A group is itself a task, so you can depend on it or run it by name:

```python
>>> with group("data") as data:
...     _ = sh(name="gen-a", cmd="generate a", outputs=["a.csv"])
...     _ = sh(name="gen-b", cmd="generate b", outputs=["b.csv"])

>>> _ = sh(name="train", cmd="python train.py", inputs=[data])

```

```bash
cook run data       # runs gen-a and gen-b
cook run train      # runs data group first, then train
```

All relative paths resolve relative to the recipe file's directory, making recipes portable across machines.

## CLI

```bash
cook run [pattern]               # run tasks matching glob pattern
cook run -n [pattern]            # show what would run (--dry-run)
cook run -k [pattern]            # keep going on failure (--keep-going)
cook run -j4 [pattern]           # run up to 4 tasks in parallel (--jobs)
cook run -s [pattern]            # stream task output to terminal (--stream)
cook run -x slurm [pattern]      # override executor backend (--executor)

cook build <output-pattern>      # run tasks that produce matching outputs

cook inspect [pattern]           # show dependency graph and staleness
cook inspect --json [pattern]    # JSON lines output

cook list [pattern]              # list task names
cook list -s [pattern]           # list only stale tasks (--stale)
cook list --json [pattern]       # JSON lines output

cook invalidate <pattern>        # force tasks to re-run next time

cook validate <pattern>          # mark tasks as up-to-date without running

cook ui [pattern]                # interactive DAG visualization
```

Patterns use glob syntax (`fnmatch`). Use `-r` for regex. Dependencies of matched tasks are always included.

Global flags: `-v` (verbose), `-q` (quiet), `--color=auto|always|never`, `-f` (recipe file), `-c` (config file), `--version`.

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
poll_timeout = 86400.0
poll_retries = 10

[cook.slurm.defaults]      # default sbatch flags for all slurm tasks
mem = "4G"
partition = "batch"
```

All settings have sensible defaults. CLI flags override config values.

Per-task slurm options override defaults:

```python
>>> _ = sh(name="gpu-train", cmd="python train.py", slurm={"mem": "32G", "gres": "gpu:1"})

```

## How staleness works

Cook computes a content hash for each task based on its fields, the contents of its input files, and the hashes of its dependencies. If the hash matches the last successful run and all outputs exist, the task is skipped. Changed inputs, changed commands, or missing outputs trigger re-execution.
