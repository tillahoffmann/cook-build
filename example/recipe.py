"""Compile a small C project with Cook."""

from pathlib import Path

from cook import get_context

ctx = get_context()

sources = sorted(Path(".").glob("*.c"))
headers = sorted(Path(".").glob("*.h"))

# Compile each .c → .o
for src in sources:
    obj = Path("build") / src.with_suffix(".o").name
    ctx.sh(
        name=f"compile-{src.stem}",
        cmd=f"mkdir -p build && cc -c {src} -o {obj}",
        inputs=[src] + headers,
        outputs=[obj],
    )

# Link all .o → build/app
# Cook resolves .o file inputs to their compile tasks automatically.
obj_paths = [Path("build") / s.with_suffix(".o").name for s in sources]
ctx.sh(
    name="link",
    cmd=f"cc {' '.join(str(o) for o in obj_paths)} -o build/app",
    inputs=obj_paths,
    outputs=[Path("build/app")],
)
