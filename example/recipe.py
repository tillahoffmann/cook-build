"""Compile a small C project with Cook."""

from pathlib import Path

from cook import get_context

ctx = get_context()

sources = sorted(Path(".").glob("*.c"))
headers = sorted(Path(".").glob("*.h"))

# Compile each .c → .o
objects = []
for src in sources:
    obj = Path("build") / src.with_suffix(".o").name
    objects.append(
        ctx.sh(
            name=f"compile-{src.stem}",
            cmd=f"mkdir -p build && cc -c {src} -o {obj}",
            inputs=[src] + headers,
            outputs=[obj],
        )
    )

# Link all .o → build/app
app = Path("build/app")
link = ctx.sh(
    name="link",
    cmd=f"cc {' '.join(str(o.outputs[0]) for o in objects)} -o {app}",
    inputs=objects,
    outputs=[app],
)

# Run the binary (no outputs → always runs)
ctx.sh(
    name="run",
    cmd="./build/app",
    inputs=[link],
)
