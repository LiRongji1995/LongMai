"""Windows-safe atomic JSON replacement and shared process/thread write locks.

Legacy files remain diagnostic caches. Recommendation truth lives in SQLite.
Corrupt history and write failures are raised; they are never replaced by [].
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import tempfile
import threading


def atomic_write_json(path: Path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        # Windows has no portable directory fsync; the canonical ledger uses
        # SQLite FULL synchronous mode. Atomic replace preserves the old file
        # if replacement itself fails, unlike delete-then-rename.
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class _PathLock:
    def __init__(self, path: Path):
        self.path = path
        self.thread_lock = threading.RLock()
        self.local = threading.local()

    def acquire(self):
        self.thread_lock.acquire()
        depth = getattr(self.local, "depth", 0)
        try:
            if not depth:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                stream = self.path.open("a+b")
                self.local.stream = stream
                if os.fstat(stream.fileno()).st_size == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            self.local.depth = depth + 1
        except BaseException:
            if not depth and getattr(self.local, "stream", None):
                self.local.stream.close()
            self.thread_lock.release()
            raise
        return True

    def release(self):
        try:
            self.local.depth -= 1
            if not self.local.depth:
                stream = self.local.stream
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                finally:
                    stream.close()
        finally:
            self.thread_lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()


_registry_guard = threading.Lock()
_registry = {}


def shared_directory_lock(directory: Path):
    path = Path(directory).resolve() / ".legacy-write.lock"
    with _registry_guard:
        return _registry.setdefault(str(path).casefold(), _PathLock(path))


def safe_component(value: str) -> str:
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}", value)
            or value.endswith(".") or value.split(".")[0].upper() in
            {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}):
        raise ValueError("invalid storage path component")
    return value
