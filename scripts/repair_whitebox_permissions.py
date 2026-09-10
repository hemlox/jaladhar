from __future__ import annotations

import importlib.util
import os
import stat
from pathlib import Path

import typer

app = typer.Typer(add_completion=False)


def whitebox_package_dir() -> Path:
    spec = importlib.util.find_spec("whitebox")
    if spec is None or spec.submodule_search_locations is None:
        raise RuntimeError("whitebox is not installed; run `uv pip install -e .` first")
    return Path(next(iter(spec.submodule_search_locations)))


def executable_targets(package_dir: Path) -> list[Path]:
    targets = [
        package_dir / "whitebox_tools",
        package_dir / "WBT" / "whitebox_tools",
    ]
    plugins = package_dir / "plugins"
    if plugins.is_dir():
        targets.extend(
            path for path in plugins.iterdir() if path.is_file() and path.suffix != ".json"
        )

    missing = [str(path) for path in targets if not path.is_file()]
    if missing:
        raise RuntimeError("Whitebox installation is incomplete; missing:\n" + "\n".join(missing))
    return targets


def repair_permissions(package_dir: Path) -> list[Path]:
    changed: list[Path] = []
    execute_bits = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    for target in executable_targets(package_dir):
        mode = target.stat().st_mode
        if mode & execute_bits != execute_bits:
            target.chmod(mode | execute_bits)
            changed.append(target)
    return changed


@app.command()
def main(
    check: bool = typer.Option(False, "--check", help="Report instead of changing permissions."),
) -> None:
    package_dir = whitebox_package_dir()
    targets = executable_targets(package_dir)
    if check:
        blocked = [path for path in targets if not os.access(path, os.X_OK)]
        if blocked:
            typer.echo(
                "Whitebox binaries are not executable:\n"
                + "\n".join(str(path) for path in blocked),
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(f"Whitebox executable permissions are valid ({len(targets)} files).")
        return

    changed = repair_permissions(package_dir)
    for target in changed:
        typer.echo(f"made executable: {target}")
    if not changed:
        typer.echo("Whitebox executable permissions already valid.")

    import whitebox

    typer.echo(whitebox.WhiteboxTools().version())


if __name__ == "__main__":
    app()
