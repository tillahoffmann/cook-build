# Cook — Design Document

## Overview

Cook is a Python-native build system with content-hash-based incremental builds and pluggable execution backends (local, Slurm, etc.). It prioritizes simplicity, pure Python task definitions, and async-native scheduling.

**PyPI name:** `cook-build`
**CLI name:** `cook`
**Python:** 3.11+ (stdlib only, no third-party dependencies)

---

## Core Concepts

### Task

A task is a **plain dataclass** describing *what* to do, not *how* to do it. Tasks hold no execution logic; that is the executor's job.

- Every task has a unique **name** used for CLI targeting, state storage, and error messages. Names must not contain glob characters (`*`, `?`, `[`, `]`) since those are used for CLI pattern matching.
- **`inputs`** is a mixed list of file paths (`str | Path`) and other `Task` objects. The system partitions these automatically into file inputs (for hashing) and task dependencies (for DAG construction).
- **`outputs`** is a list of file paths the task is expected to produce.
- **`digest()`** returns a SHA-256 hash of the task's own identity: all field values, filtering out Task objects from `inputs` (those are handled separately by the scheduler via the effective digest). It does *not* hash file contents; that is the scheduler's responsibility. Subclasses may override.

**ShellTask** extends Task with `cmd: str`, `env: dict | None`, and `cwd: str | None`. The `env` field follows the stdlib convention: `None` inherits the parent environment, a dict is a complete replacement.

Users define **custom task types** by subclassing Task as a dataclass and registering a handler with the executor.

### Dependencies

Dependencies can be expressed explicitly by placing Task objects in the `inputs` list, or implicitly via file matching — if a task's file input matches another task's declared output, the dependency is resolved automatically during `validate()`.

```python
# Explicit: pass the task object in inputs
obj = ctx.sh(name="compile-foo", cmd="gcc -c foo.c -o foo.o",
             inputs=["foo.c"], outputs=["foo.o"])
binary = ctx.sh(name="link-foo", cmd="gcc foo.o -o foo",
                inputs=[obj, "foo.o"], outputs=["foo"])

# Implicit: file-based resolution (Make-style)
# Just list file paths — Cook resolves foo.o to the compile task automatically.
obj = ctx.sh(name="compile-foo", cmd="gcc -c foo.c -o foo.o",
             inputs=["foo.c"], outputs=["foo.o"])
binary = ctx.sh(name="link-foo", cmd="gcc foo.o -o foo",
                inputs=["foo.o"], outputs=["foo"])
```

Implicit dependencies are stored internally (in `_deps`) and merged with explicit ones via the `task_deps` property. The original `inputs` list is never modified.

### Staleness & Effective Digest

The scheduler computes an **effective digest** for each task using SHA-256:

```
effective_digest = hash(
    task.digest(),                                       # task's own identity
    [hash_file(f) for f in sorted(task.file_inputs)],   # input file contents (sorted after path resolution)
    [effective_digest(dep) for dep in sorted(task.task_deps)],  # dependency digests (sorted by name)
)
```

File inputs and task deps are sorted (by resolved path and task name respectively) before hashing, so reordering `inputs` does not change the effective digest.

**Timing:** the effective digest is computed **after all dependencies have completed**. This is critical because file inputs may include outputs of upstream tasks (e.g., `.o` files produced by a compile step). By hashing after deps finish, we capture the actual content of intermediate files.

Both file content hashes and dep effective digests are needed:
- **File content hashes** catch changes to intermediate files (a dep produced different output)
- **Dep effective digests** catch changes to deps that have no file outputs (ordering-only deps, always-run tasks)

A task is **skipped** (up-to-date) when ALL of the following are true:
1. Its effective digest matches the stored value in the BuildStore
2. All declared output files exist on disk
3. None of its transitive dependencies are always-run tasks

A task **must execute** when ANY of the following are true:
- Effective digest does not match (or no stored record exists)
- Any declared output file is missing
- The task has no outputs (always-run: tests, linting, etc.)
- Any transitive dependency is an always-run task (because we cannot know whether its side effects changed)

**No-output tasks** have an effective digest of `None`. They always execute and no digest is stored.

**`None` propagation:** if any dependency has an effective digest of `None`, the dependent's effective digest is also `None`. This is how always-run behavior propagates up the graph — no separate graph walk is needed. A `None` digest never matches a stored value, so the task must execute.

**Caveat:** an always-run task deep in the graph forces all downstream tasks to re-run every time. Avoid placing no-output tasks (tests, linters) upstream of large subgraphs unless this is intended.

**After execution:** the scheduler verifies all declared output files exist (fails the task if any are missing), then stores the effective digest.

This model handles: changed commands, changed file contents, changed dependencies, deleted outputs, and partial failures.

### File Path Resolution

All relative paths in `inputs` and `outputs` are resolved relative to the **current working directory** at the time `cook` is invoked. This includes paths used for hashing, existence checks, and the `cwd` field on ShellTask (where `None` means the cook process's working directory).

---

## Components

There are five components: **Context**, **Task**, **Scheduler**, **Executor**, and **BuildStore**. The CLI wires them together.

### Context

The context handles task registration at recipe-load time. It is *not* involved in execution.

- Accessed via **`get_context()`**, which returns the active context or a default singleton.
- Also works as a **context manager** (backed by `contextvars`) for test isolation: `with Context() as ctx:` activates a fresh context that is automatically deactivated on exit.
- **`register(task)`** registers a single task, raises on duplicate names, and returns the task. This allows chaining: `task = ctx.register(MyTask(...))`. To register multiple tasks, call `register()` multiple times.
- **`sh()`** is a convenience that creates a ShellTask and registers it in one call (returns the task).
- **`validate()`** is called by the CLI before execution. It runs a pipeline of **graph transforms** (`GraphTransform` protocol in `transform.py`). The default pipeline:
  1. **`check_deps_registered`** — all transitive dependencies are registered
  2. **`check_outputs`** — no duplicate output paths across tasks (after path resolution); no task's file inputs overlap with its own outputs
  3. **`resolve_file_deps`** — if a task's file input matches another task's declared output, an implicit task dependency is added (stored in the internal `_deps` set, not in `inputs`). This enables Make-style file-based dependency resolution.
  4. **`check_cycles`** — no cycles in the dependency graph (DFS-based)

  Additional invariants enforced elsewhere:
  - No duplicate task names (enforced at registration time by `register()`)
  - No glob characters in task names (enforced at construction time by `Task.__post_init__`)

**Recipe usage:**

```python
from cook import get_context

ctx = get_context()

obj = ctx.sh(name="compile-foo", cmd="gcc -c foo.c -o foo.o",
             inputs=["foo.c"], outputs=["foo.o"])
ctx.sh(name="link-foo", cmd="gcc foo.o -o foo",
       inputs=[obj], outputs=["foo"])
```

**Test usage:**

```python
@pytest.fixture
def ctx():
    with Context() as ctx:
        yield ctx

def test_something(ctx):
    ctx.sh(name="test-task", cmd="echo hello")
    assert "test-task" in ctx.tasks
```

### Executor

The executor runs tasks. It owns a **handler registry** mapping task types to async handler functions, and an **asyncio.Semaphore** for concurrency control (each executor sets its own limit).

Handler resolution walks the task type's **MRO**, so a handler registered for `ShellTask` also matches its subclasses. If no handler is found, `TypeError` is raised.

Users extend the system by registering handlers for their custom task types — no need to subclass the executor itself.

**Built-in executors:**

- **LocalExecutor** — runs shell commands via `asyncio.create_subprocess_shell`.
- **SlurmExecutor** — submits jobs via `sbatch --wrap`, polls completion via `scontrol show job`, reads stderr from output files. Supports `--chdir`, `--export` for env, and `scancel` on cancellation/timeout.

### Scheduler

The scheduler owns the DAG walk. It is fully async and coordinates:

1. **Dependency resolution** — ensures all deps are up-to-date before running a task, using `asyncio.gather` for concurrency.
2. **Deduplication** — each task executes at most once per build. When multiple targets share a dependency, concurrent requests await a shared result.
3. **Staleness checking** — computes effective digests, compares against the BuildStore, checks output existence.
4. **Executor dispatch** — hands stale tasks to the executor.
5. **Output validation** — verifies declared outputs exist after execution.
6. **Failure handling** — in keep-going mode (`-k`), independent branches continue when one fails. Failed tasks are tracked; their dependents are skipped.

### BuildStore

Persists build state across runs. Abstract base class with `get(task_id)`, `save(record)`, and `close()`.

**TaskRecord** stores: task_id, effective digest, last_started, last_succeeded, last_failed, duration, and error message. Timing fields are stored for user inspection (e.g., `cook inspect` could show when a task last ran).

Default implementation uses **SQLite** (`.cook.db` in WAL mode). Operations are synchronous. Concurrent `cook` invocations against the same project are safe because builds are idempotent — redundant work may occur but results are correct.

---

## CLI

Built with `argparse`.

```
cook exec [pattern]              Run tasks matching glob pattern (fnmatch)
cook exec --dry-run [pattern]    Show what would run without executing
cook exec -k [pattern]           Keep going on failure
cook exec --executor=slurm ...   Override executor
cook inspect [pattern]           Show dependency graph and staleness info
cook invalidate <pattern>        Delete stored digests, forcing re-execution on next run
```

**`cook invalidate`** does not cascade: invalidating a task forces it to re-run, but its dependents only re-run if the invalidated task's effective digest actually changes after re-execution.

If no pattern is given, `cook exec` uses the default target from `cook.toml` (the `default` key). If no default is configured and no pattern is given, cook errors.

The pattern selects **target tasks**; their transitive dependencies are always included and executed as needed, regardless of whether they match the pattern.

### Recipe Loading

1. Read `cook.toml` from the current working directory (if absent, use defaults)
2. Import the recipe module via `importlib` (default: `recipe.py`)
   - The recipe's directory is added to `sys.path` for local imports
3. Call `context.validate()`
4. Match target pattern against task names
5. Construct Executor + BuildStore + Scheduler, run

### Target Matching

Task names are matched using `fnmatch` (stdlib glob). Future: `--regex` flag for regex patterns.

---

## Configuration

`cook.toml` at project root (optional — all settings have defaults):

```toml
[cook]
recipe = "recipe.py"
executor = "local"
default = "build-*"             # default target pattern for bare `cook exec`

[cook.local]
max_concurrent = 8
```

CLI flags override `cook.toml` values.

---

## DAG Introspection

Since tasks are pure data with explicit dependency references, the DAG is extracted by walking `task_deps` recursively — no tracing or interception needed.

**`cook inspect`** loads the recipe, walks the graph, computes effective digests, and displays a tree with staleness info.

**`cook exec --dry-run`** does the same, formatted as "would execute" output.

---

## Output

Default output shows task start/complete events with duration:

```
[compile-foo] started
[compile-foo] completed (0.3s)
[compile-bar] FAILED (0.2s)
    gcc: error: bar.c: No such file or directory
```

Future: `-v` / `-vv` for increasing verbosity.

---

## Example

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
        inputs=[str(src)], outputs=[str(src.with_suffix(".o"))],
    ))

ctx.sh(name="link",
       cmd=f"gcc {' '.join(str(o.outputs[0]) for o in objects)} -o build/app",
       inputs=objects, outputs=["build/app"])

ctx.sh(name="test", cmd="pytest",
       inputs=[ctx.tasks["link"]] + [str(f) for f in sorted(Path("tests").glob("*.py"))])
```

```
$ cook exec "compile-*"      # compile all
$ cook exec "link"            # link (auto-runs compile deps)
$ cook exec "*"               # everything
$ cook exec --dry-run "*"     # show what would run
$ cook inspect "link"         # show dependency tree
```

---

## Project Structure

```
src/cook/
    __init__.py          Public API: get_context, Task, ShellTask
    task.py              Task, ShellTask dataclasses
    context.py           Context, get_context()
    transform.py         Graph transform pipeline (validation + file dep resolution)
    scheduler.py         Async DAG scheduler
    executor/
        __init__.py      Executor ABC
        local.py         LocalExecutor
    store/
        __init__.py      BuildStore ABC, TaskRecord
        sqlite.py        SqliteBuildStore
    config.py            cook.toml loading
    cli.py               argparse CLI entry point
```

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Tasks are pure data, no execution logic | Clean separation; same task definition works across all executors |
| Mixed inputs list (files + tasks) | Ergonomic — matches how users think about "what does this task need?" |
| File inputs hashed after deps complete | Captures actual content of intermediate files. Combined with dep digests, covers both file-producing and ordering-only dependencies. |
| Output existence checked separately | Digest doesn't include own outputs. Missing outputs → rebuild. Externally modified outputs not detected unless downstream tasks hash them as inputs. |
| No-output tasks never store a digest | They always run — no ambiguity about staleness |
| Always-run propagates to dependents | If any transitive dep is always-run, we must re-run because we can't know if its effects changed |
| Handler registry on executor, not visitor | Users extend without subclassing the executor; MRO resolution handles inheritance |
| Context via contextvars | Thread-safe singleton with clean test isolation via context manager |
| register() takes one task, returns it | Allows chaining: `task = ctx.register(MyTask(...))`. No ambiguous return types. |
| Always-run effective digest is `None` | Propagates naturally: `None` in any dep → `None` for the dependent → must execute. No separate graph walk needed. |
| cook.toml lookup is CWD only | Simple and explicit. No magic parent-directory walks. |
| Paths resolve from CWD | Least surprising; matches how shell commands and tools work |
| Synchronous BuildStore | SQLite ops are sub-millisecond; async wrapping adds complexity for no measurable benefit |
| stdlib only | Fewer moving parts, easier to install, forces simplicity |

---

## Open Questions / Future Work

- **`cook build <file>`** — address tasks by output path. Deferred.
- **Multi-repo** — cross-repo deps. Deferred; monorepo for now.
- **Watch mode** — not needed currently.
- **Artifact caching** — not planned; skip-or-rebuild is sufficient.
- **`--regex` targeting** — add when needed.
- **Verbose output** (`-v` / `-vv`) — add when needed.
- **Per-task executor override** — deferred; priority semantics with CLI flag unclear.
- **`.cook.db` schema versioning** — no migration strategy yet. Add when schema evolves.
