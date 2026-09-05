"""
Qlib 数据更新执行器 — 从 GitHub 下载最新的 qlib_bin.tar.gz 并解压到 ~/.qlib/qlib_data/cn_data。

用法:
    from data_runner import run_data_update
    result = run_data_update(progress_callback=cb)
    print(result["message"])
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
import time
from datetime import date
from pathlib import PurePosixPath
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Callable

import requests

# ---- 常量 ----
GITHUB_REPO = "chenditc/investment_data"
RELEASE_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
ASSET_NAME = "qlib_bin.tar.gz"
QLIB_DATA_DIR = Path(os.environ.get("QLIB_DATA_DIR", Path.home() / ".qlib" / "qlib_data" / "cn_data"))

# 下载超时（秒）
DOWNLOAD_TIMEOUT = (10, 30)
# 每下载 chunk 后推送间隔（秒）
PROGRESS_INTERVAL = 1.0

# ---- 代理配置 ----
_PROXY_URL = os.environ.get("QLIB_DATA_PROXY", "")
_PROXIES = {"http": _PROXY_URL, "https": _PROXY_URL} if _PROXY_URL else None


def _make_session() -> requests.Session:
    """创建带代理配置的 requests Session."""
    session = requests.Session()
    if _PROXIES:
        session.proxies.update(_PROXIES)
    return session


def _log(progress_callback: Callable | None, message: str, **extra):
    """统一日志推送."""
    if progress_callback:
        progress_callback({"message": message, **extra})


def _get_latest_release_info() -> dict:
    """获取最新 release 的下载 URL 和 tag."""
    session = _make_session()
    resp = session.get(
        RELEASE_API_URL,
        headers={"User-Agent": "StockFish/1.0"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    tag = data.get("tag_name", "unknown")
    for asset in data.get("assets", []):
        if asset.get("name") == ASSET_NAME:
            return {
                # 使用 API asset URL（需 Accept: application/octet-stream header）
                "download_url": asset["url"],
                "tag": tag,
                "size": asset.get("size", 0),
                "digest": asset.get("digest"),
            }

    raise FileNotFoundError(
        f"在 {GITHUB_REPO} 的最新 release ({tag}) 中未找到 {ASSET_NAME}"
    )


def _download_file(
    url: str,
    dest_path: Path,
    total_size: int = 0,
    progress_callback: Callable | None = None,
) -> None:
    """流式下载文件并推送进度."""
    _log(progress_callback, f"开始下载: {url}")
    _log(progress_callback, f"目标文件: {dest_path}")
    if total_size > 0:
        _log(progress_callback, f"文件大小: {total_size / 1024 / 1024:.1f} MB")

    session = _make_session()
    resp = session.get(
        url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "StockFish/1.0",
        },
        stream=True,
        timeout=DOWNLOAD_TIMEOUT,
    )
    resp.raise_for_status()

    # 实际 content-length
    content_length = int(resp.headers.get("content-length", 0))
    if content_length == 0:
        content_length = total_size

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    deadline = time.monotonic() + 180
    last_log_time = 0.0
    import time as _time

    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):  # 1MB chunks
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if time.monotonic() > deadline:
                    raise ValueError("ARCHIVE_DOWNLOAD_DEADLINE")
                if downloaded > 2 * 1024 ** 3:
                    raise ValueError("ARCHIVE_DOWNLOAD_SIZE_LIMIT")
                now = _time.time()
                if content_length > 0 and (now - last_log_time) >= PROGRESS_INTERVAL:
                    pct = downloaded / content_length * 100
                    mb_dl = downloaded / 1024 / 1024
                    mb_total = content_length / 1024 / 1024
                    _log(
                        progress_callback,
                        f"下载中... {mb_dl:.1f}/{mb_total:.1f} MB ({pct:.1f}%)",
                        progress=round(pct / 100, 4),
                    )
                    last_log_time = now

    if content_length and downloaded != content_length:
        raise ValueError("DOWNLOAD_LENGTH_MISMATCH")
    if total_size and downloaded != total_size:
        raise ValueError("RELEASE_ASSET_LENGTH_MISMATCH")
    _log(progress_callback, f"下载完成: {downloaded / 1024 / 1024:.1f} MB")


def _extract_tar_gz(archive_path: Path, extract_to: Path, progress_callback: Callable | None = None) -> None:
    """Extract only to a new/empty directory after validating every member.

    No archive paths, symlinks, hardlinks or special files can escape staging.
    Existing datasets are never cleared or overwritten.
    """
    extract_to = Path(extract_to).resolve()
    if extract_to.exists() and any(extract_to.iterdir()):
        raise ValueError("NONEMPTY_EXTRACTION_TARGET")
    extract_to.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        if not members or len(members) > 200000:
            raise ValueError("ARCHIVE_MEMBER_LIMIT")
        paths = []
        total = 0
        for member in members:
            raw = member.name
            relative = PurePosixPath(raw)
            if "\\" in raw or ":" in raw or relative.is_absolute() or ".." in relative.parts:
                raise ValueError("UNSAFE_ARCHIVE_PATH")
            if not (member.isfile() or member.isdir()):
                raise ValueError("UNSAFE_ARCHIVE_MEMBER_TYPE")
            total += member.size
            if member.size > 1024**3 or total > 4*1024**3:
                raise ValueError("ARCHIVE_EXPANSION_LIMIT")
            parts = tuple(part for part in relative.parts if part != ".")
            paths.append(parts)
        # Strip a genuine shared wrapper (including its directory entry).
        prefix = ()
        files = [parts for member, parts in zip(members, paths) if member.isfile()]
        while files and all(len(parts) > len(prefix)+1 and parts[len(prefix)] == files[0][len(prefix)] for parts in files):
            prefix = files[0][:len(prefix)+1]
        planned, seen = [], set()
        for member, parts in zip(members, paths):
            if prefix and parts[:len(prefix)] == prefix:
                parts = parts[len(prefix):]
            elif prefix and member.isdir() and prefix[:len(parts)] == parts:
                continue
            if not parts:
                continue
            target = extract_to.joinpath(*parts).resolve()
            if not target.is_relative_to(extract_to) or target == extract_to:
                raise ValueError("ARCHIVE_PATH_ESCAPE")
            key = str(target).casefold()
            if key in seen:
                raise ValueError("ARCHIVE_DUPLICATE_PATH")
            seen.add(key)
            planned.append((member, target))
        for member, target in planned:
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024*1024)
                if target.stat().st_size != member.size:
                    raise ValueError("EXTRACTION_SIZE_MISMATCH")
    _log(progress_callback, "新快照解压完成，旧活动数据保持可读")


def _verify_data_dir(progress_callback: Callable | None = None, data_dir: Path | None = None) -> list[str]:
    target = Path(data_dir or QLIB_DATA_DIR).resolve()
    checks = ["calendars/day.txt", "instruments/csi300.txt", "instruments/all.txt"]
    if any(not (target/name).is_file() for name in checks):
        raise ValueError("QLIB_REQUIRED_FILE_MISSING")
    sessions = [date.fromisoformat(v.strip()) for v in (target/checks[0]).read_text(encoding="utf-8-sig").splitlines() if v.strip()]
    if not sessions or sessions != sorted(set(sessions)):
        raise ValueError("QLIB_CALENDAR_INVALID")
    for name in checks[1:]:
        lines = (target/name).read_text(encoding="utf-8-sig").splitlines()
        if not lines:
            raise ValueError("QLIB_UNIVERSE_EMPTY")
        for line in lines:
            parts = line.split()
            if len(parts) != 3 or not re.fullmatch(r"(?:SH|SZ|BJ)[0-9]{6}", parts[0], re.I):
                raise ValueError("QLIB_UNIVERSE_INVALID")
            if date.fromisoformat(parts[1]) > date.fromisoformat(parts[2]):
                raise ValueError("QLIB_UNIVERSE_RANGE_INVALID")
    features = list((target/"features").rglob("*.day.bin"))
    if not features or not any(p.name == "close.day.bin" for p in features):
        raise ValueError("QLIB_FEATURES_MISSING")
    for feature in features:
        size = feature.stat().st_size
        if size < 8 or size % 4:
            raise ValueError("QLIB_FEATURE_SIZE_INVALID")
        with feature.open("rb") as handle:
            index = struct.unpack("<f", handle.read(4))[0]
        if not math.isfinite(index) or index < 0 or index != int(index) or int(index)+(size//4-1) > len(sessions):
            raise ValueError("QLIB_FEATURE_INDEX_INVALID")
    _log(progress_callback, "日历、证券区间与全部日线二进制边界验证通过")
    return checks + ["features/*.day.bin"]


def run_data_update(progress_callback: Callable | None = None, *, expected_sha256: str | None = None) -> dict:
    """Publish an immutable validated snapshot; never mutate active QLIB_DATA_DIR.

    SHA256 must come from GitHub's release asset digest or an explicitly pinned
    caller value. Successful staging requires explicit later activation through
    QLIB_DATA_DIR, avoiding Windows non-atomic directory replacement races.
    """
    started = time.monotonic()
    try:
        info = _get_latest_release_info()
        expected = expected_sha256 or str(info.get("digest") or "").removeprefix("sha256:")
        if not re.fullmatch(r"[a-fA-F0-9]{64}", expected):
            raise ValueError("TRUSTED_ARCHIVE_SHA256_REQUIRED")
        snapshot_parent = QLIB_DATA_DIR.resolve().parent / ".snapshots"
        snapshot_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".stage-", dir=snapshot_parent) as staging:
            stage = Path(staging).resolve()
            if not stage.is_relative_to(snapshot_parent.resolve()):
                raise ValueError("STAGING_PATH_ESCAPE")
            archive = stage / ASSET_NAME
            _download_file(info["download_url"], archive, total_size=info["size"], progress_callback=progress_callback)
            h = hashlib.sha256()
            with archive.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024*1024), b""):
                    h.update(chunk)
            if h.hexdigest() != expected.lower():
                raise ValueError("ARCHIVE_SHA256_MISMATCH")
            candidate = stage/"dataset"
            _extract_tar_gz(archive, candidate, progress_callback)
            verified = _verify_data_dir(progress_callback, candidate)
            # data_runner is also executable directly from qlib-zh.
            core_root = str(Path(__file__).resolve().parent.parent)
            if core_root not in sys.path:
                sys.path.insert(0, core_root)
            from stockfish_core.data import LocalQlibDataAdapter
            manifest = LocalQlibDataAdapter(candidate).manifest()
            destination = snapshot_parent/manifest.digest
            if destination.exists():
                if LocalQlibDataAdapter(destination).manifest().digest != manifest.digest:
                    raise ValueError("IMMUTABLE_SNAPSHOT_COLLISION")
            else:
                candidate.rename(destination)
            message = "新数据快照已验证并保存；通过 QLIB_DATA_DIR 显式切换后生效"
            _log(progress_callback, message, progress=1.0, status="completed", activation_required=True)
            return dict(success=True, message=message, tag=info["tag"], verified_files=verified,
                        elapsed_seconds=round(time.monotonic()-started, 1), data_dir=str(destination),
                        data_version=manifest.digest, archive_sha256=h.hexdigest(), activation_required=True)
    except Exception as exc:
        reason = str(exc) if isinstance(exc, (ValueError, FileNotFoundError)) else type(exc).__name__
        _log(progress_callback, "数据更新失败；原活动快照未修改", status="failed", error=reason)
        return {"success": False, "message": "数据更新失败；原活动快照未修改", "error": reason}


def _print_progress(data):
    print(data.get("message", ""), file=sys.stderr)


if __name__ == "__main__":
    result = run_data_update(progress_callback=_print_progress)
    print(json.dumps(result, ensure_ascii=False, indent=2))
