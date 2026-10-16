"""Offline migrations, coordinated backups, guarded restores and index reconciliation."""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import httpx
from qdrant_client import QdrantClient, models
from sqlalchemy import func, inspect, select
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.db import Database
from app.models import Chunk, CleanupTask, Document, DocumentRevision, IngestionJob


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def pg_command(settings, tool, container=None):
    url = make_url(settings.database_url)
    if container:
        return ["docker", "exec", "-i", container, tool, "-U", url.username or "postgres", "-d", url.database], os.environ.copy()
    binary = shutil.which(tool)
    if not binary:
        raise ValueError(f"Install PostgreSQL client tools or pass --postgres-container for {tool}")
    environment = {**os.environ, "PGPASSWORD": url.password or ""}
    return [binary, "-h", url.host or "localhost", "-p", str(url.port or 5432),
            "-U", url.username or "postgres", "-d", url.database], environment


def vector_client(settings):
    if settings.qdrant_url:
        return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)
    return QdrantClient(path=str(settings.data_dir / "qdrant"))


def backup(settings, destination, quiesced=False, postgres_container=None):
    if not quiesced:
        raise ValueError("Stop ALL API and worker replicas, then pass --quiesced to confirm a coordinated snapshot")
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("Backup destination must be a new directory")
    destination.mkdir(parents=True)
    url = make_url(settings.database_url)
    manifest = {"format": 1, "created_at": datetime.now(UTC).isoformat(), "quiesced": True,
        "database": "postgresql" if url.drivername.startswith("postgresql") else "sqlite",
        "collection_prefix": settings.qdrant_collection_prefix,
        "vector_mode": "server" if settings.qdrant_url else "local", "collections": [],
        "old_upload_root": str((settings.data_dir / "uploads").resolve()), "files": {}}
    if manifest["database"] == "postgresql":
        command, env = pg_command(settings, "pg_dump", postgres_container)
        with (destination / "database.dump").open("wb") as output:
            subprocess.run([*command, "--format=custom", "--no-owner", "--no-privileges"], env=env,
                           stdout=output, check=True)
    else:
        with sqlite3.connect(url.database) as source, sqlite3.connect(destination / "database.sqlite") as target:
            source.backup(target)
    uploads = settings.data_dir / "uploads"
    if uploads.exists():
        if any(path.is_symlink() for path in uploads.rglob("*")):
            raise ValueError("Upload backup must not contain symlinks")
        shutil.copytree(uploads, destination / "uploads")
    if settings.qdrant_url:
        headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}
        with closing(vector_client(settings)) as store, httpx.Client(base_url=settings.qdrant_url, headers=headers, timeout=300) as api:
            manifest["qdrant_version"] = api.get("/").json().get("version")
            for item in store.get_collections().collections:
                if not item.name.startswith(settings.qdrant_collection_prefix + "_"):
                    continue
                snapshot = store.create_snapshot(item.name)
                manifest["collections"].append(item.name)
                target = destination / "vectors" / f"{item.name}.snapshot"
                target.parent.mkdir(exist_ok=True)
                try:
                    with api.stream("GET", f"/collections/{item.name}/snapshots/{snapshot.name}") as response:
                        response.raise_for_status()
                        with target.open("wb") as output:
                            for block in response.iter_bytes(1024 * 1024):
                                output.write(block)
                finally:
                    store.delete_snapshot(item.name, snapshot.name)
    elif (settings.data_dir / "qdrant").exists():
        shutil.copytree(settings.data_dir / "qdrant", destination / "qdrant")
    for path in sorted(destination.rglob("*")):
        if path.is_file():
            manifest["files"][path.relative_to(destination).as_posix()] = {"sha256": digest(path), "bytes": path.stat().st_size}
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"backup": str(destination), "files": len(manifest["files"]), "collections": len(manifest["collections"])}


def verify_backup(source):
    source = Path(source).resolve()
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1 or not manifest.get("quiesced"):
        raise ValueError("Unsupported or non-coordinated backup")
    actual = {p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file() and p.name != "manifest.json"}
    if actual != set(manifest["files"]) or any(p.is_symlink() for p in source.rglob("*")):
        raise ValueError("Backup contains unverified files or symlinks")
    for relative, expected in manifest["files"].items():
        path = (source / relative).resolve()
        if not path.is_relative_to(source) or not path.is_file() or path.stat().st_size != expected["bytes"] or digest(path) != expected["sha256"]:
            raise ValueError("Backup integrity check failed")
    return manifest


def restore(settings, source, quiesced=False, postgres_container=None):
    if not quiesced:
        raise ValueError("Restore requires stopped API/workers and --quiesced")
    source = Path(source).resolve()
    manifest = verify_backup(source)
    import re
    names = {}
    for name in manifest["collections"]:
        prefix = manifest.get("collection_prefix", settings.qdrant_collection_prefix)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or not name.startswith(prefix + "_"):
            raise ValueError("Backup contains an invalid collection name")
        names[name] = settings.qdrant_collection_prefix + name[len(prefix):]
    url = make_url(settings.database_url)
    expected_database = "postgresql" if url.drivername.startswith("postgresql") else "sqlite"
    if manifest["database"] != expected_database or manifest["vector_mode"] != ("server" if settings.qdrant_url else "local"):
        raise ValueError("Restore target must use the same database and vector storage modes")
    database = Database(settings)
    if inspect(database.engine).get_table_names():
        database.engine.dispose()
        raise ValueError("Restore target database must be empty; existing databases are never overwritten")
    database.engine.dispose()
    root = settings.data_dir.resolve()
    for name in ["uploads", "qdrant"]:
        if (root / name).exists() and any((root / name).iterdir()):
            raise ValueError("Restore target uploads/vector directories must be empty")
    if settings.qdrant_url:
        with closing(vector_client(settings)) as store:
            if any(store.collection_exists(name) for name in names.values()):
                raise ValueError("A target collection already exists; restore refuses to overwrite vectors")
    root.mkdir(parents=True, exist_ok=True)
    if expected_database == "postgresql":
        command, env = pg_command(settings, "pg_restore", postgres_container)
        with (source / "database.dump").open("rb") as data:
            subprocess.run([*command, "--no-owner", "--no-privileges", "--exit-on-error"], env=env, stdin=data, check=True)
    else:
        from urllib.parse import quote
        source_uri = "file:" + quote((source / "database.sqlite").as_posix(), safe="/:") + "?mode=ro&immutable=1"
        with sqlite3.connect(source_uri, uri=True) as backup_db, sqlite3.connect(url.database) as target:
            backup_db.backup(target)
    for name in ["uploads", "qdrant"]:
        if (source / name).exists():
            shutil.copytree(source / name, root / name, dirs_exist_ok=True)
    # Support Windows/Linux relocation without trusting arbitrary paths from a backup.
    database = Database(settings.model_copy(update={"auto_migrate": True}))
    database.initialize()
    with database.session() as db:
        for model in [Document, DocumentRevision]:
            for record in db.scalars(select(model)).yield_per(200):
                old = record.stored_path.replace("\\", "/")
                filename = old.rsplit("/", 1)[-1]
                if not filename or filename in {".", ".."} or not (root / "uploads" / filename).is_file():
                    raise ValueError("Restored record references a missing upload")
                record.stored_path = str(root / "uploads" / filename)
        for task in db.scalars(select(CleanupTask)).yield_per(200):
            task.paths = [str(root / "uploads" / value.replace("\\", "/").rsplit("/", 1)[-1]) for value in task.paths]
        db.commit()
    database.engine.dispose()
    if settings.qdrant_url:
        headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}
        with httpx.Client(base_url=settings.qdrant_url, headers=headers, timeout=600) as api:
            for name in manifest["collections"]:
                with (source / "vectors" / f"{name}.snapshot").open("rb") as data:
                    response = api.post(f"/collections/{names[name]}/snapshots/upload", params={"priority": "snapshot", "wait": "true"},
                                        files={"snapshot": (name + ".snapshot", data, "application/octet-stream")})
                    response.raise_for_status()
    return {"restored": True, "collections": len(manifest["collections"]), "files_verified": len(manifest["files"])}


def reconcile(settings, apply=False):
    database, orphan_ids, scanned = Database(settings), [], 0
    with database.session() as db, closing(vector_client(settings)) as store:
        for name in [r.name for r in store.get_collections().collections if r.name.startswith(settings.qdrant_collection_prefix + "_")]:
            offset = None
            while True:
                points, offset = store.scroll(name, offset=offset, limit=200, with_payload=True, with_vectors=False)
                for point in points:
                    scanned += 1
                    payload = point.payload or {}
                    chunk = db.get(Chunk, payload.get("chunk_id", ""))
                    doc = db.get(Document, chunk.document_id) if chunk else None
                    pending = db.scalar(select(IngestionJob.id).where(
                        IngestionJob.document_id == payload.get("document_id"),
                        IngestionJob.status.in_(["queued", "processing"])))
                    valid = doc is not None and chunk.revision_id == doc.active_revision_id
                    if not valid and not pending:
                        orphan_ids.append((name, point.id))
                if offset is None:
                    break
        if apply:
            for name in {name for name, _ in orphan_ids}:
                ids = [pid for collection, pid in orphan_ids if collection == name]
                for start in range(0, len(ids), 200):
                    store.delete(name, models.PointIdsList(points=ids[start:start + 200]), wait=True)
        active_count = db.scalar(select(func.count()).select_from(Chunk).join(Document)
            .where(Chunk.revision_id == Document.active_revision_id))
    database.engine.dispose()
    return {"scanned_vectors": scanned, "orphan_vectors": len(orphan_ids), "active_sql_chunks": active_count,
            "applied": apply, "note": "Orphan cleanup only; missing/changed-model vectors require a document reindex"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["migrate", "backup", "verify-backup", "restore", "reconcile"])
    parser.add_argument("--path", type=Path)
    parser.add_argument("--quiesced", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--postgres-container")
    args, settings = parser.parse_args(), get_settings()
    if args.command == "migrate":
        database = Database(settings.model_copy(update={"auto_migrate": True}))
        database.initialize()
        database.engine.dispose()
        result = {"migration": "head"}
    elif args.command == "reconcile":
        if args.apply and not args.quiesced:
            parser.error("reconcile --apply requires stopped writers and --quiesced")
        result = reconcile(settings, args.apply)
    else:
        if not args.path:
            parser.error("--path is required")
        if args.command == "verify-backup":
            result = {"verified_files": len(verify_backup(args.path)["files"])}
        else:
            result = (backup if args.command == "backup" else restore)(
                settings, args.path, args.quiesced, args.postgres_container)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
