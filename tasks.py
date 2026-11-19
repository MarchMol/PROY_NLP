"""Runner de tareas multiplataforma (equivalente al Makefile, para Windows sin `make`).

Uso:  python tasks.py <tarea>      (p. ej. `python tasks.py test`)
      python tasks.py --list
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
PY = str(VENV_PY if VENV_PY.exists() else Path(sys.executable))


def _run(*args: str) -> int:
    print("$", " ".join(args), flush=True)
    return subprocess.call(list(args), cwd=ROOT)


def setup() -> int:
    """Crea .venv (Python 3.11) e instala dependencias fijadas."""
    if not VENV_PY.exists():
        launcher = ["py", "-3.11"] if os.name == "nt" else ["python3.11"]
        rc = _run(*launcher, "-m", "venv", ".venv")
        if rc:
            return rc
    py = str(VENV_PY)
    return (
        _run(py, "-m", "pip", "install", "--upgrade", "pip")
        or _run(py, "-m", "pip", "install", "-r", "requirements-lock.txt")
        or _run(py, "-m", "pip", "install", "-e", ".", "--no-deps")
    )


def test() -> int:
    """Corre la suite de tests (sin red)."""
    return _run(PY, "-m", "pytest", "-m", "not network")


def lint() -> int:
    """Lint con ruff."""
    return _run(PY, "-m", "ruff", "check", "src", "tests", "tasks.py")


def fmt() -> int:
    """Formatea con ruff."""
    return _run(PY, "-m", "ruff", "format", "src", "tests", "tasks.py")


def ingest_download() -> int:
    """Descarga los artículos semilla a un snapshot nuevo en data/raw/."""
    return _run(PY, "-m", "marioqa.ingest.download")


def ingest_chunks() -> int:
    """Limpia el último snapshot y genera chunks (ventanas 200, 300 y 500)."""
    for size in ("200", "300", "500"):
        rc = _run(PY, "-m", "marioqa.ingest.build_chunks", "--chunk-tokens", size)
        if rc:
            return rc
    return 0


def ingest() -> int:
    """Un comando: snapshot → chunks → Chroma + BM25 + SQLite (idempotente)."""
    return _run(PY, "-m", "marioqa.ingest.pipeline")


def demo() -> int:
    """Corre las 4 preguntas demo con la línea base (LLM según .env)."""
    return _run(PY, "-m", "marioqa.eval.run_demo")


def run() -> int:
    """Levanta la API + UI en http://127.0.0.1:8000 (Ctrl+C para salir)."""
    return _run(PY, "-m", "marioqa.api.app")


def bench() -> int:
    """Benchmark de 33 casos (línea base vs agente) → eval/report.md."""
    return _run(PY, "-m", "marioqa.eval.run_bench")


def security() -> int:
    """Solo la suite adversaria (8 casos de seguridad) contra el agente."""
    return _run(
        PY,
        "-m",
        "marioqa.eval.run_bench",
        "--only",
        "agente",
        "--cases",
        "eval/security.jsonl",
        "--report",
        "eval/security_report.md",
    )


TASKS: dict[str, Callable[[], int]] = {
    "setup": setup,
    "test": test,
    "lint": lint,
    "fmt": fmt,
    "ingest-download": ingest_download,
    "ingest-chunks": ingest_chunks,
    "ingest": ingest,
    "demo": demo,
    "run": run,
    "bench": bench,
    "security": security,
}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] in {"-h", "--help", "--list"}:
        print(__doc__)
        for name, fn in TASKS.items():
            print(f"  {name:<16} {fn.__doc__}")
        return 0 if argv and argv[0] in {"-h", "--help", "--list"} else 2
    task = TASKS.get(argv[0])
    if task is None:
        print(f"Tarea desconocida: {argv[0]}")
        return 2
    return task()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
