"""Verified demo roots, seed backups, and generation-bound SQLite factories."""

import asyncio
import errno
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import threading
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy import URL, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# HTTP metadata is shared with the state provider; no HTTP framework dependency.
ERRORS = {
    "SESSION_REQUIRED": (401, False, "start_required", None),
    "EXPIRED": (410, False, "expired", None),
    "STALE_GENERATION": (409, False, "stale_generation", None),
    "STALE_ROLE": (409, False, "stale_role", None),
    "START_IN_PROGRESS": (409, False, "preparing", None),
    "PREPARE_FAILED": (503, True, "prepare_failed", None),
    "RESET_IN_PROGRESS": (409, False, "resetting", None),
    "RESET_FAILED": (503, True, "reset_failed", None),
    "DRAIN_TIMEOUT": (503, True, "reset_failed", None),
    "DB_LOCKED": (503, True, "retry_later", 1),
    "ROLE_DELETED": (409, False, "role_deleted", None),
    "BUSY": (409, True, "wait_or_stop", None),
    "ACTION_FORBIDDEN": (403, False, "action_forbidden", None),
    "CSRF_INVALID": (403, False, "csrf_refresh", None),
    "LIMIT_REACHED": (429, False, "limit_reached", None),
    "RATE_LIMITED": (429, True, "retry_later", None),
    "CAPACITY": (503, True, "capacity", 30),
    "STORAGE_LIMIT": (507, False, "storage_limit", None),
    "UPLOAD_TOO_LARGE": (413, False, "upload_limit", None),
    "CLEANUP_BACKLOG": (503, True, "retry_later", None),
    "VERSION_MISMATCH": (503, False, "unavailable", None),
    "CONFIGURATION_INVALID": (503, False, "unavailable", None),
    "UNSUPPORTED_CONFIGURATION": (422, False, "supported_only", None),
    "INVALID_REQUEST": (422, False, "check_input", None),
    "REQUEST_CONFLICT": (409, False, "refresh_state", None),
    "RESOURCE_NOT_FOUND": (404, False, "not_found", None),
}


class DemoError(Exception):
    def __init__(
        self, code, *, generation=None, auth_epoch=None, retry_after=None
    ):
        self.code = code if code.startswith("DEMO_") else "DEMO_" + code
        self.status_code, self.retryable, recovery, default_retry = ERRORS[
            self.code.removeprefix("DEMO_")
        ]
        self.status = self.status_code
        self.recovery = "demo." + recovery
        self.generation = generation
        self.auth_epoch = auth_epoch
        self.retry_after = default_retry if retry_after is None else retry_after
        self.detail = dict(
            code=self.code,
            retryable=self.retryable,
            recovery=self.recovery,
            generation=generation,
            auth_epoch=auth_epoch,
        )
        super().__init__(self.code)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    return json.loads(data, object_pairs_hook=pairs)


def _plain(path, *, directory=False):
    """Reject links in every component, including the final file."""
    if not path.is_absolute() or ".." in path.parts:
        raise DemoError("CONFIGURATION_INVALID")
    for part in (path, *path.parents):
        if part.is_symlink():
            raise DemoError("CONFIGURATION_INVALID")
    try:
        info = path.stat()
    except OSError as exc:
        raise DemoError("CONFIGURATION_INVALID") from exc
    valid_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not valid_type(info.st_mode) or (not directory and info.st_nlink != 1):
        raise DemoError("CONFIGURATION_INVALID")
    return info


@dataclass(frozen=True)
class SeedSpec:
    path: Path
    manifest_path: Path
    db_schema_version: str
    migration_set_sha256: str
    migration_filenames: tuple[str, ...]
    verify_content: Callable[[sqlite3.Connection, dict], None]


class DatabaseRoot:
    def __init__(
        self, path, *, initialize=False, forbidden_paths=(), max_db_bytes=None
    ):
        self.path = Path(path)
        self.initialize = initialize
        self.forbidden_paths = tuple(Path(p) for p in forbidden_paths)
        self.max_db_bytes = max_db_bytes
        self._lock = None
        self._refs = {}
        self._seed = None
        self._orphans = []
        self.seed_bytes = 0
        self.seed_manifest_sha256 = None
        self.root_id = None

    def open(self):
        try:
            if self._lock is not None:
                raise DemoError("CONFIGURATION_INVALID")
            if not self.path.exists() and self.initialize:
                _plain(self.path.parent, directory=True)
                self.path.mkdir(mode=0o700)
            info = _plain(self.path, directory=True)
            if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise DemoError("CONFIGURATION_INVALID")
            repo = Path(__file__).resolve().parents[2]
            for forbidden in (repo, *self.forbidden_paths):
                target = forbidden.resolve()
                if (
                    self.path == target
                    or self.path.is_relative_to(target)
                    or target.is_relative_to(self.path)
                ):
                    raise DemoError("CONFIGURATION_INVALID")
            marker = self.path / ".demo-root.json"
            if not marker.exists():
                if not self.initialize or any(self.path.iterdir()):
                    raise DemoError("CONFIGURATION_INVALID")
                marker.write_text(
                    json.dumps(
                        dict(
                            kind="misconcept-demo-root",
                            marker_version=1,
                            root_id=str(uuid4()),
                            app_mode="demo",
                        )
                    )
                )
                marker.chmod(0o600)
            _plain(marker)
            data = _json(marker.read_bytes())
            if (
                set(data) != {"kind", "marker_version", "root_id", "app_mode"}
                or data["kind"] != "misconcept-demo-root"
                or type(data["marker_version"]) is not int
                or data["marker_version"] != 1
                or data["app_mode"] != "demo"
            ):
                raise DemoError("CONFIGURATION_INVALID")
            self.root_id = UUID(data["root_id"])
            self._marker_bytes = marker.read_bytes()
            lock = self.path / ".demo.lock"
            if lock.exists():
                _plain(lock)
            fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException:
                os.close(fd)
                raise
            self._lock = fd
            self._identity = (info.st_dev, info.st_ino)
            lock_info = os.fstat(fd)
            self._lock_identity = (lock_info.st_dev, lock_info.st_ino)
            return self
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise DemoError("CONFIGURATION_INVALID") from exc

    def _check(self):
        if self._lock is None:
            raise DemoError("CONFIGURATION_INVALID")
        info = _plain(self.path, directory=True)
        if (info.st_dev, info.st_ino) != self._identity:
            raise DemoError("CONFIGURATION_INVALID")
        marker = self.path / ".demo-root.json"
        _plain(marker)
        lock = _plain(self.path / ".demo.lock")
        if (
            marker.read_bytes() != self._marker_bytes
            or (lock.st_dev, lock.st_ino) != self._lock_identity
        ):
            raise DemoError("CONFIGURATION_INVALID")

    def close(self):
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None

    def owns(self, ref):
        return self._refs.get(id(ref)) is ref

    @property
    def external_seed_bytes(self):
        if self._seed is None or self._seed.path.is_relative_to(self.path):
            return 0
        return _file_bytes(self._seed.path)

    def bytes_used(self):
        self._check()
        total = 0
        for path in self.path.rglob("*"):
            info = _plain(path, directory=path.is_dir())
            if not path.is_dir():
                total += info.st_size
        return total

    def validate_seed(self, spec):
        self._check()
        try:
            _file_bytes(spec.path)
            manifest = self._manifest(spec)
            with _readonly(spec.path) as db:
                self._verify(db, spec, manifest, "demo_seed", None, None)
            self.seed_bytes = _file_bytes(spec.path)
            if (
                self.max_db_bytes is not None
                and self.seed_bytes > self.max_db_bytes
            ):
                raise DemoError("CONFIGURATION_INVALID")
            self.seed_manifest_sha256 = _sha(spec.manifest_path.read_bytes())
            self._seed = spec
            self.orphan_refs()
        except DemoError:
            self._seed = None
            raise
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            sqlite3.Error,
        ) as exc:
            self._seed = None
            raise DemoError("VERSION_MISMATCH") from exc

    def _manifest(self, spec):
        _plain(spec.manifest_path)
        manifest = _json(spec.manifest_path.read_bytes())
        fixed = dict(
            manifest_version=1,
            contract_version="d0-03-v1-r3",
            content_version="d0-02-v1",
            fixture_version="demo-fixture-v1",
            db_schema_version=spec.db_schema_version,
            migration_set_sha256=spec.migration_set_sha256,
            scenario_config_schema_version=1,
            analysis_schema_version=2,
            provider="demo",
            model_id="demo-classroom-v1",
            capability_definition_version="demo-cap-v1",
            role_contract_versions=dict(
                student="s1-v1", mentor="s3-v1", analysis="s4-v2"
            ),
            scenario_ref="unit-fraction-v1",
            seed_version="demo-seed-v1",
            seed_timestamp="2026-10-18T00:00:00Z",
            lookup_version=1,
            seed_provenance=dict(
                source="synthetic",
                generator="src/demo/seed.py",
                content_version="d0-02-v1",
            ),
        )
        if set(manifest) != set(fixed) | {
            "supported_config_sha256",
            "seed_refs",
            "files",
            "responses",
        }:
            raise ValueError("manifest fields")
        for name, expected in fixed.items():
            if manifest[name] != expected or type(manifest[name]) is not type(
                expected
            ):
                raise ValueError("manifest version")
        for name in ("supported_config_sha256", "migration_set_sha256"):
            if not isinstance(manifest[name], str) or not re.fullmatch(
                "[0-9a-f]{64}", manifest[name]
            ):
                raise ValueError("manifest digest")
        refs = manifest["seed_refs"]
        if not isinstance(refs, dict) or set(refs) != {
            "admin",
            "teacher",
            "group",
            "scenario",
            "completed_session",
            "model",
            "connection",
        }:
            raise ValueError("seed references")
        for value in refs.values():
            if type(value) is int and value > 0:
                continue
            if not isinstance(value, str) or str(UUID(value)) != value:
                raise ValueError("seed reference type")
        paths = []
        for item in manifest["files"]:
            if set(item) != {"path", "sha256", "kind"} or item["kind"] not in {
                "student",
                "mentor",
                "analysis",
                "failure",
                "seed",
            }:
                raise ValueError("fixture file")
            relative = PurePosixPath(item["path"])
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or str(relative) != item["path"]
                or "\\" in item["path"]
            ):
                raise ValueError("fixture path")
            path = spec.manifest_path.parent / item["path"]
            _plain(path)
            if (
                path == spec.manifest_path
                or _sha(path.read_bytes()) != item["sha256"]
            ):
                raise ValueError("fixture digest")
            paths.append(item["path"])
        if (
            paths != sorted(set(paths))
            or not isinstance(manifest["responses"], dict)
            or any(
                not isinstance(k, str) or v not in paths
                for k, v in manifest["responses"].items()
            )
        ):
            raise ValueError("fixture lookup")
        return manifest

    def _verify(self, db, spec, manifest, kind, visitor_id, generation):
        db.row_factory = sqlite3.Row
        if [r[0] for r in db.execute("PRAGMA integrity_check")] != [
            "ok"
        ] or db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("SQLite integrity")
        history = tuple(
            r[0]
            for r in db.execute("SELECT filename FROM _migrations ORDER BY id")
        )
        if (
            history != spec.migration_filenames
            or history[-1] != spec.db_schema_version
        ):
            raise ValueError("migration history")
        rows = db.execute("SELECT * FROM demo_metadata").fetchall()
        expected = dict(
            id=1,
            kind=kind,
            contract_version=manifest["contract_version"],
            content_version=manifest["content_version"],
            fixture_version=manifest["fixture_version"],
            db_schema_version=manifest["db_schema_version"],
            manifest_sha256=_sha(spec.manifest_path.read_bytes()),
            visitor_id=visitor_id,
            generation=generation,
        )
        if len(rows) != 1 or any(rows[0][k] != v for k, v in expected.items()):
            raise ValueError("database marker")
        for row in db.execute("SELECT * FROM provider_connection"):
            if any(
                row[name] is not None
                for name in (
                    "encrypted_key",
                    "nonce",
                    "encryption_key_version",
                    "masked_hint",
                )
            ) or (row["provider"] != "demo" and row["enabled"]):
                raise DemoError("CONFIGURATION_INVALID")
        if db.execute(
            "SELECT 1 FROM model_config m JOIN provider_connection p ON p.id=m.provider_connection_id WHERE p.provider != 'demo' LIMIT 1"
        ).fetchone():
            raise DemoError("CONFIGURATION_INVALID")
        spec.verify_content(db, manifest)

    async def clone(
        self, visitor_id, generation, *, operation_id=None, cancel_event=None
    ):
        self._check()
        if self._seed is None:
            raise DemoError("CONFIGURATION_INVALID")
        try:
            parsed = UUID(visitor_id)
            if (
                parsed.version != 4
                or str(parsed) != visitor_id
                or type(generation) is not int
                or generation < 1
            ):
                raise ValueError("generation identity")
        except (ValueError, TypeError, AttributeError) as exc:
            raise DemoError("INVALID_REQUEST") from exc
        stem = f"{visitor_id}-g{generation}-{uuid4()}"
        staging = self.path / (stem + ".staging")
        final = self.path / (stem + ".db")
        stopped = (
            cancel_event if cancel_event is not None else threading.Event()
        )
        builder = asyncio.create_task(
            asyncio.to_thread(
                self._clone, staging, final, visitor_id, generation, stopped
            )
        )
        try:
            return await asyncio.shield(builder)
        except asyncio.CancelledError:
            stopped.set()
            # A thread cannot be cancelled: retain its files until it has stopped.
            try:
                ref = await _settle(builder)
            except BaseException:
                pass
            else:
                ref.factory.retire()
                await _settle(asyncio.create_task(self.cleanup(ref)))
            raise

    def _clone(self, staging, final, visitor_id, generation, stopped):
        spec = self._seed
        created = renamed = False
        try:
            self._check()
            manifest = self._manifest(spec)
            if (
                _sha(spec.manifest_path.read_bytes())
                != self.seed_manifest_sha256
            ):
                raise DemoError("VERSION_MISMATCH")
            _file_bytes(spec.path)
            with _readonly(spec.path) as source:
                self._verify(source, spec, manifest, "demo_seed", None, None)
                os.close(
                    os.open(
                        staging,
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                        0o600,
                    )
                )
                created = True
                with (
                    closing(sqlite3.connect(staging)) as destination,
                    destination,
                ):

                    def progress(status, remaining, total):
                        if stopped.is_set():
                            raise DemoError("PREPARE_FAILED")

                    source.backup(
                        destination, pages=128, progress=progress, sleep=0.01
                    )
                    destination.execute(
                        "UPDATE demo_metadata SET kind='demo_visitor', visitor_id=?, generation=? WHERE id=1",
                        (visitor_id, generation),
                    )
                with _readonly(staging) as destination:
                    self._verify(
                        destination,
                        spec,
                        manifest,
                        "demo_visitor",
                        visitor_id,
                        generation,
                    )
                if (
                    self.max_db_bytes is not None
                    and _file_bytes(staging) > self.max_db_bytes
                ):
                    raise DemoError("STORAGE_LIMIT")
                if stopped.is_set():
                    raise DemoError("PREPARE_FAILED")
                if final.exists() or final.is_symlink():
                    raise DemoError("CONFIGURATION_INVALID")
                os.rename(staging, final)
                renamed = True
                factory = GenerationFactory(final, self.max_db_bytes)
                ref = GenerationRef(
                    visitor_id,
                    generation,
                    factory,
                    self.seed_manifest_sha256,
                    final,
                )
                self._refs[id(ref)] = ref
                return ref
        except BaseException as exc:
            for path, owned in ((staging, created), (final, renamed)):
                if owned and (path.exists() or path.is_symlink()):
                    _unlink_files(path)
            if isinstance(exc, sqlite3.Error) or (
                isinstance(exc, OSError) and exc.errno == errno.ENOSPC
            ):
                raise _sqlite_error(exc) from exc
            if isinstance(
                exc, (OSError, ValueError, KeyError, IndexError, TypeError)
            ):
                raise DemoError("VERSION_MISMATCH") from exc
            raise

    def orphan_refs(self):
        """Recover only files with verified generation names and database markers."""
        self._check()
        if self._seed is None:
            raise DemoError("CONFIGURATION_INVALID")
        spec = self._seed
        manifest = self._manifest(spec)
        known = {
            self.path / ".demo-root.json",
            self.path / ".demo.lock",
            spec.path,
            spec.manifest_path,
        }
        known.update(
            spec.manifest_path.parent / item["path"]
            for item in manifest["files"]
        )
        for path in tuple(known):
            known.update(
                Path(str(path) + suffix)
                for suffix in ("-wal", "-shm", "-journal")
            )
        owned = {ref.path for ref in self._refs.values()}
        pattern = re.compile(
            r"([0-9a-f-]{36})-g([1-9][0-9]*)-([0-9a-f-]{36})\.(db|staging)"
        )
        candidates = []
        for path in self.path.rglob("*"):
            _plain(path, directory=path.is_dir())
            if path.is_dir():
                if not any(p.is_relative_to(path) for p in known):
                    raise DemoError("CONFIGURATION_INVALID")
                continue
            if path in known:
                continue
            base = path
            for suffix in ("-wal", "-shm", "-journal"):
                if base.name.endswith(suffix):
                    base = base.with_name(base.name[: -len(suffix)])
                    break
            match = pattern.fullmatch(base.name)
            if base.parent != self.path or match is None or not base.exists():
                raise DemoError("CONFIGURATION_INVALID")
            if any(
                str(UUID(value)) != value or UUID(value).version != 4
                for value in (match[1], match[3])
            ):
                raise DemoError("CONFIGURATION_INVALID")
            if base not in owned and base not in {
                item[0] for item in candidates
            }:
                candidates.append((base, match[1], int(match[2]), match[4]))
        recovered = []
        try:
            for path, visitor_id, generation, extension in candidates:
                with _readonly(path) as db:
                    row = db.execute(
                        "SELECT kind FROM demo_metadata WHERE id=1"
                    ).fetchone()
                    kind = row[0] if row else None
                    if kind == "demo_seed" and extension == "staging":
                        self._verify(db, spec, manifest, kind, None, None)
                    else:
                        self._verify(
                            db,
                            spec,
                            manifest,
                            "demo_visitor",
                            visitor_id,
                            generation,
                        )
                factory = GenerationFactory(path, self.max_db_bytes)
                factory.retire()
                recovered.append(
                    GenerationRef(
                        visitor_id,
                        generation,
                        factory,
                        self.seed_manifest_sha256,
                        path,
                    )
                )
        except (
            OSError,
            ValueError,
            KeyError,
            IndexError,
            TypeError,
            sqlite3.Error,
        ) as exc:
            raise DemoError("VERSION_MISMATCH") from exc
        for ref in recovered:
            self._refs[id(ref)] = ref
            self._orphans.append(ref)
        return tuple(ref for ref in self._orphans if self.owns(ref))

    def generation_bytes(self, ref):
        if not self.owns(ref):
            raise DemoError("INVALID_REQUEST")
        self._check()
        return _file_bytes(ref.path)

    async def cleanup(self, ref):
        self._check()
        if not self.owns(ref) or not ref.factory.retired:
            raise DemoError("INVALID_REQUEST")
        _file_bytes(ref.path)
        await ref.factory.dispose()
        if ref.path.exists():
            info = _plain(ref.path)
            if (info.st_dev, info.st_ino) != ref.factory.identity:
                raise DemoError("CONFIGURATION_INVALID")
            try:
                with closing(
                    sqlite3.connect(
                        ref.path.as_uri() + "?mode=rw", uri=True, timeout=0
                    )
                ) as db:
                    db.execute("BEGIN EXCLUSIVE")
                    db.rollback()
            except sqlite3.OperationalError as exc:
                raise _sqlite_error(exc) from exc
        _unlink_files(ref.path)
        self._refs.pop(id(ref))
        return True


async def _settle(task):
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


@contextmanager
def _readonly(path):
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
    try:
        db.execute("BEGIN")
        yield db
    finally:
        db.close()


def _file_bytes(path):
    return sum(
        _plain(p).st_size
        for p in (
            path,
            Path(str(path) + "-wal"),
            Path(str(path) + "-shm"),
            Path(str(path) + "-journal"),
        )
        if p.exists() or p.is_symlink()
    )


def _unlink_files(path):
    paths = (
        path,
        Path(str(path) + "-wal"),
        Path(str(path) + "-shm"),
        Path(str(path) + "-journal"),
    )
    for item in paths:
        if item.exists() or item.is_symlink():
            _plain(item)
    for item in paths:
        item.unlink(missing_ok=True)


def _sqlite_error(exc):
    if (
        getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL
        or getattr(exc, "errno", None) == errno.ENOSPC
    ):
        return DemoError("STORAGE_LIMIT")
    if "locked" in str(exc).lower() or "busy" in str(exc).lower():
        return DemoError("DB_LOCKED")
    return DemoError("PREPARE_FAILED")


class GenerationFactory:
    def __init__(self, path, max_db_bytes):
        self.path = path
        self.retired = False
        self.disposed = False
        self._checked_out = set()
        self._write_check = None
        info = _plain(path)
        self.identity = (info.st_dev, info.st_ino)
        self.engine = create_async_engine(
            URL.create(
                "sqlite+aiosqlite",
                database=path.as_uri(),
                query={"mode": "rw", "uri": "true"},
            )
        )
        self._sessions = async_sessionmaker(
            self.engine, expire_on_commit=False, autoflush=False
        )

        @event.listens_for(self.engine.sync_engine, "do_connect")
        def before_connect(dialect, record, args, kwargs):
            self._check()

        @event.listens_for(self.engine.sync_engine, "connect")
        def connect(dbapi, record):
            self._check()
            cursor = dbapi.cursor()
            try:
                for pragma in (
                    "foreign_keys=ON",
                    "journal_mode=WAL",
                    "synchronous=NORMAL",
                    "busy_timeout=1000",
                ):
                    cursor.execute("PRAGMA " + pragma)
                if max_db_bytes is not None:
                    cursor.execute("PRAGMA page_size")
                    page_size = cursor.fetchone()[0]
                    cursor.execute(
                        f"PRAGMA max_page_count={max_db_bytes // page_size}"
                    )
            finally:
                cursor.close()

        @event.listens_for(self.engine.sync_engine, "checkout")
        def checkout(dbapi, record, proxy):
            self._check()
            self._checked_out.add(id(record))
            self.disposed = False

        @event.listens_for(self.engine.sync_engine, "checkin")
        def checkin(dbapi, record):
            self._checked_out.discard(id(record))

        @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
        def statement(conn, cursor, sql, parameters, context, executemany):
            if self._write_check is None:
                return
            normalized = sql.strip().upper()
            if normalized.startswith("SELECT ") or re.fullmatch(
                r"PRAGMA\s+(FOREIGN_KEYS|JOURNAL_MODE|SYNCHRONOUS|BUSY_TIMEOUT|PAGE_SIZE|PAGE_COUNT|MAX_PAGE_COUNT)\s*;?",
                normalized,
            ):
                return
            if normalized in {"BEGIN", "BEGIN IMMEDIATE", "ROLLBACK"}:
                return
            self._write_check()
            if normalized.startswith("PRAGMA"):
                raise DemoError("ACTION_FORBIDDEN")

        @event.listens_for(self.engine.sync_engine, "handle_error")
        def sqlite_failure(context):
            error = _sqlite_error(context.original_exception)
            if error.code in {"DEMO_DB_LOCKED", "DEMO_STORAGE_LIMIT"}:
                raise error from context.original_exception

        @event.listens_for(self.engine.sync_engine, "commit")
        def commit(conn):
            if self._write_check is not None:
                self._write_check()

    def _check(self):
        if self.retired:
            raise DemoError("STALE_GENERATION")
        _file_bytes(self.path)
        info = _plain(self.path)
        if (info.st_dev, info.st_ino) != self.identity:
            raise DemoError("CONFIGURATION_INVALID")

    def __call__(self, **kwargs):
        self._check()
        return self._sessions(**kwargs)

    def set_write_check(self, callback):
        self._write_check = callback

    def retire(self):
        self.retired = True

    async def dispose(self):
        if self._checked_out:
            raise DemoError("DB_LOCKED")
        await self.engine.dispose()
        self.disposed = True


@dataclass(frozen=True)
class GenerationRef:
    visitor_id: str
    generation: int
    factory: GenerationFactory
    manifest_sha256: str
    path: Path

    @property
    def bytes_used(self):
        return _file_bytes(self.path)
