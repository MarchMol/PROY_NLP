"""`reindex_corpus`: acción CRÍTICA de administrador. Nunca se expone al LLM ni a la API.

    python -m marioqa.tools.reindex --version 20261119 --confirm

Requiere rol admin, `confirm=True` y, en la CLI, escribir la versión a mano (HITL). Construye
todo en un directorio de trabajo aislado (`data/reindex_work/<ts>/`), verifica los hashes de
todos los chunks contra el índice y solo entonces reemplaza los índices vivos (con respaldo).
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from marioqa.agent.audit import AuditLog
from marioqa.config import Settings, get_settings
from marioqa.index.access import Role
from marioqa.index.embeddings import Embedder
from marioqa.index.vector_store import VectorStore
from marioqa.ingest.build_chunks import chunks_path, read_chunks
from marioqa.ingest.chunking import TokenCounter
from marioqa.ingest.pipeline import run_ingest
from marioqa.ingest.snapshot import list_snapshots
from marioqa.logging_setup import get_logger, setup_logging

log = get_logger(__name__)


class ConfirmationRequired(PermissionError):
    """La acción exige confirmación humana explícita."""


class ReindexError(RuntimeError):
    pass


@dataclass
class ReindexReport:
    version: str
    workdir: Path
    chunks_verified: int = 0
    backup: Path | None = None
    swapped: list[str] = field(default_factory=list)


def _targets(s: Settings) -> dict[str, Path]:
    return {
        "chroma_dir": s.chroma_dir,
        "index_dir": s.index_dir,
        "processed_dir": s.processed_dir,
        "sqlite_path": s.sqlite_path,
    }


def release_chroma() -> None:
    """Cierra los clientes de Chroma del proceso: en Windows tienen abierto `chroma.sqlite3` y
    no dejan mover el directorio. Los `VectorStore` abiertos antes quedan inválidos."""
    from chromadb.api.shared_system_client import SharedSystemClient

    SharedSystemClient.clear_system_cache()
    gc.collect()


def verify_hashes(staged: Settings, versions: list[str]) -> int:
    """Cada chunk: sha256(texto) == hash guardado, y el índice tiene los mismos."""
    store = VectorStore(staged.chroma_dir, staged.collection_name())
    n = 0
    for v in versions:
        chunks = read_chunks(chunks_path(staged.processed_dir, v, staged.chunk_tokens))
        for c in chunks:
            if hashlib.sha256(c.text.encode("utf-8")).hexdigest() != c.hash:
                raise ReindexError(f"Hash inválido en {v}/{c.chunk_id}")
        if sorted(store.hashes(v).values()) != sorted(c.hash for c in chunks):
            raise ReindexError(f"El índice vectorial no coincide con los chunks de {v}")
        n += len(chunks)
    return n


def reindex_corpus(
    settings: Settings,
    version: str,
    confirm: bool,
    role: Role,
    embedder: Embedder,
    count: TokenCounter,
    *,
    audit: AuditLog | None = None,
) -> ReindexReport:
    if role != "admin":
        raise PermissionError("reindex_corpus solo puede ejecutarlo un administrador")
    if not confirm:
        raise ConfirmationRequired("reindex_corpus exige confirm=True (confirmación humana)")
    if version not in list_snapshots(settings.raw_dir):
        raise ReindexError(f"No existe el snapshot {version!r} en {settings.raw_dir}")

    work = settings.data_dir / "reindex_work" / time.strftime("%Y%m%d-%H%M%S")
    staged = settings.model_copy(
        update={
            "chroma_dir": work / "chroma",
            "index_dir": work / "index",
            "processed_dir": work / "processed",
            "sqlite_path": work / "game_data.sqlite",
        }
    )
    report = ReindexReport(version, work)
    try:
        ingest = run_ingest(staged, embedder, count)
        if ingest.active != version:
            raise ReindexError(
                f"La versión {version} no quedaría activa (la más reciente es {ingest.active})"
            )
        report.chunks_verified = verify_hashes(staged, list(ingest.versions))
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise

    release_chroma()
    backup = work / "backup"
    backup.mkdir(parents=True)
    for key, live in _targets(settings).items():
        new = getattr(staged, key)
        if live.exists():
            shutil.move(str(live), str(backup / live.name))
        live.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(new), str(live))
        report.swapped.append(key)
    report.backup = backup
    if audit is not None:
        audit.write(
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "action": "reindex_corpus",
                "role": role,
                "version": version,
                "chunks_verified": report.chunks_verified,
                "backup": str(backup),
            }
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Snapshot de data/raw a activar")
    parser.add_argument("--confirm", action="store_true", help="Confirmo la acción")
    args = parser.parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)

    if not args.confirm:
        print("Falta --confirm: reindex_corpus es una acción crítica.", file=sys.stderr)
        return 2
    typed = input(
        f"Reconstruirá TODOS los índices. Escribe la versión ({args.version}) para confirmar: "
    ).strip()
    if typed != args.version:
        print("Confirmación incorrecta; no se hizo nada.", file=sys.stderr)
        return 2

    from marioqa.agent.factory import audit_path
    from marioqa.index.embeddings import E5Embedder
    from marioqa.ingest.chunking import hf_token_counter

    report = reindex_corpus(
        settings,
        args.version,
        True,
        "admin",
        E5Embedder(settings.embed_model, batch_size=settings.embed_batch_size),
        hf_token_counter(settings.embed_model),
        audit=AuditLog(audit_path(settings)),
    )
    log.info(
        "Reindexado %s: %d chunks verificados; respaldo en %s",
        report.version,
        report.chunks_verified,
        report.backup,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
