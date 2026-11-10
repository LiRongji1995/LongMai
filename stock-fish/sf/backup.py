"""Portable data backups: one archive of the data directory for another machine (`sf backup` / `sf restore`).

Archive `longmai-data-<UTC stamp>.tar.gz` holds every file under the data directory except derived caches,
plus `BACKUP_MANIFEST.json` (format version, creation time, code commit, bar range, and every file's
size and sha256). A sidecar `<archive>.sha256` holds the archive's own sha256 for checking a download.

Restore never overwrites: a file that already exists with the same content is skipped, one with
different content is reported as a conflict and left as it is. Every restored file is checked against
the manifest; any mismatch fails the restore (exit 1). Members must be plain files with relative paths.
"""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from . import manifest, store

FORMAT = "SF_BACKUP_V1"
MANIFEST = "BACKUP_MANIFEST.json"
# Derived caches, rebuilt on demand; temporary files from interrupted writes.
EXCLUDE_GLOBS = ("research/seat_skill-*.parquet", "**/*.tmp", "**/.DS_Store")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _files(root: Path) -> list[Path]:
    excluded = {p for pattern in EXCLUDE_GLOBS for p in root.glob(pattern)}
    return sorted(p for p in root.rglob("*") if p.is_file() and p not in excluded)


def create(out_dir: Path, root: Path | None = None, log=print) -> dict:
    """Write the archive and its .sha256 sidecar into out_dir. Never overwrites an existing archive."""
    root = root or store.home()
    out_dir.mkdir(parents=True, exist_ok=True)
    if out_dir.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"backup directory {out_dir} is inside the data directory {root}")
    now = datetime.now(timezone.utc)
    archive = out_dir / f"longmai-data-{now.strftime('%Y%m%dT%H%M%SZ')}.tar.gz"
    if archive.exists():
        raise FileExistsError(archive)
    files = _files(root)
    entries = [dict(path=p.relative_to(root).as_posix(), size=p.stat().st_size, sha256=sha256(p)) for p in files]
    bars = sorted(p.stem for p in (root / "bars").glob("*.parquet"))
    info = dict(format=FORMAT, created_at=now.isoformat(), code=manifest.code_version(),
                bars=dict(first=bars[0] if bars else None, last=bars[-1] if bars else None, sessions=len(bars)),
                files=len(entries), bytes=sum(e["size"] for e in entries), entries=entries)
    tmp = archive.with_name(archive.name + ".part")
    with tarfile.open(tmp, "w:gz") as tar:
        payload = json.dumps(info, ensure_ascii=False, indent=1).encode("utf-8")
        member = tarfile.TarInfo(MANIFEST)
        member.size, member.mtime = len(payload), int(now.timestamp())
        tar.addfile(member, io.BytesIO(payload))
        for path, entry in zip(files, entries):
            tar.add(path, arcname=entry["path"], recursive=False)
    tmp.replace(archive)
    digest = sha256(archive)
    (out_dir / (archive.name + ".sha256")).write_text(f"{digest}  {archive.name}\n")
    log(f"  {len(entries)} files, {info['bytes'] / 1e6:.1f} MB of data -> {archive.stat().st_size / 1e6:.1f} MB")
    return dict(archive=archive, sha256=digest, files=len(entries), bars=info["bars"])


def _safe(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and name not in ("", ".")


def restore(archive: Path, root: Path | None = None, expected_sha256: str | None = None, log=print) -> dict:
    """Unpack into the data directory without overwriting; verify every file. Returns counts and problems."""
    root = root or store.home()
    sidecar = archive.with_name(archive.name + ".sha256")
    if expected_sha256 is None and sidecar.exists():
        expected_sha256 = sidecar.read_text().split()[0]
    if expected_sha256 is not None and sha256(archive) != expected_sha256.lower():
        raise ValueError(f"{archive.name}: sha256 does not match {expected_sha256} (damaged or incomplete download)")
    restored, skipped, conflicts, bad = 0, 0, [], []
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        if not members or members[0].name != MANIFEST:
            raise ValueError(f"{archive.name} is not a {FORMAT} archive (no {MANIFEST})")
        info = json.loads(tar.extractfile(members[0]).read())
        if info.get("format") != FORMAT:
            raise ValueError(f"unsupported backup format {info.get('format')}")
        expected = {e["path"]: e for e in info["entries"]}
        for member in members[1:]:
            if not (member.isfile() and _safe(member.name) and member.name in expected):
                raise ValueError(f"unexpected archive member {member.name!r}; nothing more restored")
            target = root / member.name
            data = tar.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != expected[member.name]["sha256"]:
                bad.append(member.name)
                continue
            if target.exists():
                if sha256(target) == expected[member.name]["sha256"]:
                    skipped += 1
                else:
                    conflicts.append(member.name)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".restore")
            tmp.write_bytes(data)
            tmp.replace(target)
            restored += 1
        missing = sorted(set(expected) - {m.name for m in members[1:]})
    log(f"  restored {restored}, already present {skipped}, conflicts {len(conflicts)}, damaged {len(bad)}, "
        f"missing {len(missing)}")
    return dict(restored=restored, skipped=skipped, conflicts=conflicts, damaged=bad, missing=missing,
                bars=info["bars"], created_at=info["created_at"], code=info["code"])
