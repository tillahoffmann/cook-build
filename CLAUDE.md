# CLAUDE.md — Cook Build System

## Project

Cook is a Python-native build system. See `DESIGN.md` for architecture and design decisions.

- **Package:** `cook-build` (PyPI), `cook` (import/CLI)
- **Python:** 3.11+ (stdlib only for runtime dependencies)
- **Tooling:** uv for everything (deps, running, scripts)

## Commands

```
uv run pytest                    # run tests
uv run pytest -q --cov=src/cook  # run tests with coverage
uv run ruff check --fix          # lint
uv run ruff format               # format
uv run pyright                   # type check
```

Pre-commit hooks run all of the above (including 100% test coverage). Commit must pass all hooks. `--no-verify` is FORBIDDEN and may NEVER be used.

## Code Conventions

### General

- stdlib only for runtime. No third-party dependencies unless there is a very strong reason.
- Type annotations on all public APIs and function signatures. Internal helpers can skip annotations where types are obvious.
- Prefer simple, direct code. Don't abstract prematurely. Three similar lines are better than a clever helper used once.
- No docstrings on things that are self-evident. Add comments only where the *why* isn't obvious.

### Async

- All I/O-bound and concurrent code uses async/await. The scheduler and executor are fully async.
- The BuildStore is synchronous (SQLite ops are fast enough).

### Dataclasses

- Tasks are plain dataclasses (not frozen).
- Use `field(default_factory=...)` for mutable defaults.

### Error Handling

- Raise specific exception types (`ValueError`, `TypeError`, or custom exceptions defined near their use).
- Error messages should be actionable — tell the user what went wrong and how to fix it.

## Testing

- **Framework:** pytest with pytest-asyncio.
- **Style:** flat functions, not unittest classes. Name tests `test_<thing>_<scenario>`.
- **Async tests:** use `async def test_...` with pytest-asyncio. Prefer async tests wherever the code under test is async.
- **Coverage:** 100% required. Pre-commit enforces this.
- **Fixtures over setup:** use `@pytest.fixture` for shared state. Use `tmp_path` for temp files.
- **Test isolation:** use the `Context` context manager for test isolation (see DESIGN.md).
- **Real I/O in scheduler/integration tests:** use actual files and the real SqliteBuildStore. Don't mock the filesystem.

## Project Layout

```
src/cook/           # source
tests/              # tests (flat, test_*.py)
tickets/            # work tickets (not tracked in git)
DESIGN.md           # architecture and design decisions
```
