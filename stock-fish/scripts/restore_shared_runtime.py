"""Restore one private, bounded daily runtime from the existing official hash lock.

This does not alter the lock, a service, a database, a source tree, or a locator.
The disk watchdog is admission plus sampled enforcement, not a filesystem quota.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bootstrap_daily_runtime as original

MIB = 1024 ** 2
GIB = 1024 ** 3
VERSION = "BOUNDED_SHARED_RUNTIME_RESTORE_V1"


class RestoreError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise RestoreError(reason)


def resident(path):
    value = Path(path).lstat()
    require(stat.S_ISREG(value.st_mode) and not getattr(value, 'st_flags', 0) & 0x40000000
            and not (value.st_size and getattr(value, 'st_blocks', 1) == 0),
            'RESIDENT_REGULAR_FILE_REQUIRED')
    return value


def identity(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def tree_bytes(root):
    """Count only metadata; never follow links into the base Python or old state."""
    root = Path(root)
    before = root.lstat()
    require(stat.S_ISDIR(before.st_mode), 'RESTORE_DIRECTORY_REQUIRED')
    logical = allocated = count = 0
    for folder, directories, files in os.walk(root, followlinks=False):
        for name in [*directories, *files]:
            try:
                value = (Path(folder) / name).lstat()
            except FileNotFoundError:
                # pip atomically renames/unlinks its temporary files while installing.
                # A vanished sample contributes no current bytes; other errors fail.
                continue
            count += 1
            require(count <= 100000, 'RESTORE_FILE_COUNT_LIMIT')
            require(not getattr(value, 'st_flags', 0) & 0x40000000, 'CLOUD_FILE_REJECTED')
            if not stat.S_ISDIR(value.st_mode):
                logical += value.st_size
                allocated += getattr(value, 'st_blocks', 0) * 512
    after = root.lstat()
    require((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
            and stat.S_ISDIR(after.st_mode), 'RESTORE_DIRECTORY_CHANGED')
    return {'logical_bytes': logical, 'allocated_bytes': allocated, 'entries': count}


class Meter:
    def __init__(self, destination, *, download_limit=160*MIB, file_limit=48*MIB,
                 disk_limit=GIB, min_free=10*GIB, cache_read_limit=512*MIB):
        self.destination = Path(destination)
        self.download_limit, self.file_limit = download_limit, file_limit
        self.disk_limit, self.min_free = disk_limit, min_free
        self.cache_read_limit = cache_read_limit
        self.data = {'contract': VERSION, 'status': 'STARTED', 'download_bytes': 0,
                     'cache_read_bytes': 0, 'cache_reused_bytes': 0, 'cache_reused_artifacts': 0,
                     'http_requests': 0, 'downloaded_artifacts': 0, 'expanded_artifact_bytes': 0,
                     'peak_logical_bytes': 0, 'peak_allocated_bytes': 0, 'steps': [],
                     'budgets': {'download_bytes': download_limit, 'per_file_bytes': file_limit,
                                 'disk_bytes': disk_limit, 'min_free_bytes': min_free,
                                 'cache_read_bytes': cache_read_limit},
                     'production_switched': False, 'database_copied': False,
                     'disk_enforcement': 'ADMISSION_AND_250MS_WATCHDOG_NOT_FILESYSTEM_QUOTA'}
        self.data['download_metric'] = 'RETURNED_HTTP_RESPONSE_BODY_BYTES_NOT_TLS_WIRE_BYTES'

    def check(self, reservation=0):
        current = tree_bytes(self.destination)
        for key in ('logical_bytes', 'allocated_bytes'):
            self.data['peak_' + key] = max(self.data['peak_' + key], current[key])
            require(current[key] + reservation <= self.disk_limit, 'RESTORE_DISK_BUDGET_EXCEEDED')
        require(shutil.disk_usage(self.destination).free - reservation >= self.min_free,
                'RESTORE_FREE_SPACE_RESERVE')
        return current

    def download_charge(self, size):
        require(self.data['download_bytes'] + size <= self.download_limit, 'DOWNLOAD_TOTAL_LIMIT')
        self.data['download_bytes'] += size

    def cache_charge(self, size):
        require(self.data['cache_read_bytes'] + size <= self.cache_read_limit, 'CACHE_READ_LIMIT')
        self.data['cache_read_bytes'] += size


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, newurl):
        raise RestoreError('ARTIFACT_REDIRECT_REJECTED')


def digest_file(path, meter, *, copy_to=None):
    before = resident(path)
    require(before.st_size <= meter.file_limit, 'CACHE_ARTIFACT_FILE_LIMIT')
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    target = None
    try:
        require(identity(os.fstat(descriptor)) == identity(before), 'CACHE_FILE_CHANGED')
        if copy_to is not None:
            target = open(copy_to, 'xb')
        with os.fdopen(descriptor, 'rb') as source:
            descriptor = None
            remaining = before.st_size
            while remaining:
                allowance = min(64*1024, remaining,
                                meter.cache_read_limit-meter.data['cache_read_bytes'])
                require(allowance > 0, 'CACHE_READ_LIMIT')
                chunk = source.read(allowance)
                require(bool(chunk), 'CACHE_FILE_CHANGED')
                remaining -= len(chunk)
                meter.cache_charge(len(chunk))
                digest.update(chunk)
                if target is not None:
                    meter.check(len(chunk))
                    target.write(chunk)
            require(identity(os.fstat(source.fileno())) == identity(before)
                    and identity(resident(path)) == identity(before), 'CACHE_FILE_CHANGED')
        if target is not None:
            target.flush()
            os.fsync(target.fileno())
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if target is not None:
            target.close()
    return digest.hexdigest(), before.st_size


def cache_candidates(roots, *, max_entries=5000):
    """Only pip artifact bodies/wheels under explicitly supplied pip cache roots."""
    count = 0
    for root in roots:
        root = Path(root).expanduser()
        if not root.exists() or root.is_symlink():
            continue
        for sub in ('http-v2', 'wheels'):
            base = root / sub
            if not base.is_dir() or base.is_symlink():
                continue
            for folder, directories, files in os.walk(base, followlinks=False):
                count += len(directories)
                if count > max_entries:
                    return
                directories[:] = sorted(name for name in directories
                    if not (Path(folder) / name).is_symlink())
                for name in sorted(files):
                    count += 1
                    if count > max_entries:
                        return
                    if (sub == 'http-v2' and name.endswith('.body')) or (
                            sub == 'wheels' and name.endswith('.whl')):
                        yield Path(folder) / name


def reuse_cache(packages, roots, stage, meter):
    missing = {item['sha256']: item for item in packages}
    for candidate in cache_candidates(roots):
        partial = None
        try:
            size = resident(candidate).st_size
            if size > meter.file_limit or meter.data['cache_read_bytes'] + 2*size > meter.cache_read_limit:
                continue
            digest, _ = digest_file(candidate, meter)
            if digest not in missing:
                continue
            package = missing[digest]
            target = stage / package['filename']
            partial = stage / ('cache-copy-' + uuid.uuid4().hex + '.partial')
            copied_digest, size = digest_file(candidate, meter, copy_to=partial)
            require(copied_digest == digest, 'CACHE_COPY_HASH_MISMATCH')
            os.link(partial, target)  # Never replace a staged file supplied by another actor.
            meter.data['cache_reused_bytes'] += size
            meter.data['cache_reused_artifacts'] += 1
            del missing[digest]
        except (OSError, RestoreError) as error:
            # A changing or nonresident cache is unavailable, never authoritative.
            if str(error) in {'RESTORE_DISK_BUDGET_EXCEEDED', 'RESTORE_FREE_SPACE_RESERVE'}:
                raise
            meter.data['cache_unavailable_files'] = meter.data.get('cache_unavailable_files', 0) + 1
        finally:
            if partial is not None and partial.exists():
                partial.unlink()  # This invocation's unique unverified staging copy only.
        if not missing:
            break
    return [item for item in packages if item['sha256'] in missing]


def download(package, stage, meter, *, opener=None):
    parsed = urllib.parse.urlsplit(package['url'])
    require(parsed.scheme == 'https' and parsed.hostname == 'files.pythonhosted.org'
            and not parsed.username and not parsed.password, 'OFFICIAL_ARTIFACT_REQUIRED')
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    require(Path(package['filename']).name == package['filename'], 'ARTIFACT_FILENAME_REQUIRED')
    target = stage / package['filename']
    digest, size = hashlib.sha256(), 0
    meter.data['http_requests'] += 1
    started = time.monotonic()
    with opener.open(package['url'], timeout=30) as response, open(target, 'xb') as output:
        length = response.headers.get('Content-Length')
        if length is not None:
            require(length.isdigit(), 'INVALID_CONTENT_LENGTH')
            require(int(length) <= meter.file_limit, 'DOWNLOAD_FILE_LIMIT')
            require(meter.data['download_bytes'] + int(length) <= meter.download_limit,
                    'DOWNLOAD_TOTAL_LIMIT')
            meter.check(int(length))
        while True:
            require(time.monotonic() - started <= 30, 'DOWNLOAD_TOTAL_TIMEOUT')
            allowance = min(64*1024, meter.file_limit-size,
                            meter.download_limit-meter.data['download_bytes'])
            if allowance == 0:
                # Exact-budget artifacts may finish successfully only if the complete
                # pinned hash matches. Never read an extra byte just to detect overflow.
                break
            chunk = response.read(allowance)
            require(time.monotonic() - started <= 30, 'DOWNLOAD_TOTAL_TIMEOUT')
            if not chunk:
                break
            size += len(chunk)
            require(size <= meter.file_limit, 'DOWNLOAD_FILE_LIMIT')
            meter.download_charge(len(chunk))
            meter.check(len(chunk))
            output.write(chunk)
            digest.update(chunk)
        output.flush()
        os.fsync(output.fileno())
    require(length is None or size == int(length), 'DOWNLOAD_LENGTH_MISMATCH')
    require(digest.hexdigest() == package['sha256'], 'ARTIFACT_HASH_MISMATCH')
    meter.data['downloaded_artifacts'] += 1


def expanded_bytes(path):
    if str(path).endswith('.whl'):
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            require(len(entries) <= 100000, 'ARCHIVE_ENTRY_LIMIT')
            return sum(item.file_size for item in entries)
    with tarfile.open(path, 'r:*') as archive:
        total = count = 0
        for item in archive:
            count += 1
            require(count <= 100000, 'ARCHIVE_ENTRY_LIMIT')
            require(item.size <= GIB, 'ARCHIVE_MEMBER_LIMIT')
            total += item.size
            require(total <= GIB, 'ARCHIVE_EXPANSION_LIMIT')
        return total


def run_step(argv, destination, meter, label, *, timeout=900):
    logs = destination / 'restore-logs'
    logs.mkdir(exist_ok=True)
    environment = original.clean_environment()
    environment.update(TMPDIR=str(destination / 'restore-tmp'), TMP=str(destination / 'restore-tmp'),
                       TEMP=str(destination / 'restore-tmp'), PYTHONDONTWRITEBYTECODE='1')
    started = time.monotonic()
    record = {'label': label, 'argv': list(map(str, argv)), 'exit_code': None}
    meter.data['steps'].append(record)
    process = None
    try:
        with open(logs / (label + '.log'), 'xb') as output:
            process = subprocess.Popen(list(map(str, argv)), cwd=destination, env=environment,
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                start_new_session=True)
            while process.poll() is None:
                meter.check()
                require(time.monotonic() - started <= timeout, 'RESTORE_STEP_TIMEOUT')
                time.sleep(.25)
            record['exit_code'] = process.returncode
            meter.check()
            require(process.returncode == 0, 'RESTORE_STEP_FAILED_' + label)
    except BaseException:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            record['exit_code'] = process.returncode
        raise
    finally:
        record['wall_seconds'] = time.monotonic() - started


def restore(destination, cache_roots=(), **budgets):
    manifest = original.validate_lock()
    host = original.native_identity(manifest)
    destination = Path(destination).expanduser().absolute()
    require(not destination.exists() and not destination.is_symlink(), 'DESTINATION_ALREADY_EXISTS')
    require(destination.parent.is_dir() and destination.parent.resolve() == destination.parent,
            'EXISTING_REAL_PARENT_REQUIRED')
    destination.mkdir(mode=0o700)
    meter = Meter(destination, **budgets)
    meter.data.update(host=host, destination=str(destination), manifest_sha256=original.sha256(original.MANIFEST))
    stage = destination / ('restore-stage-' + uuid.uuid4().hex)
    stage.mkdir(mode=0o700)
    (destination / 'restore-tmp').mkdir(mode=0o700)
    started = time.monotonic()
    try:
        meter.check()
        packages = manifest['packages']
        require(len({item['filename'] for item in packages}) == len(packages), 'DUPLICATE_ARTIFACT_FILENAME')
        missing = reuse_cache(packages, cache_roots, stage, meter)
        for package in missing:
            download(package, stage, meter)
        expanded = sum(expanded_bytes(stage / item['filename']) for item in packages)
        meter.data['expanded_artifact_bytes'] = expanded
        # Includes a second expansion for installer/build temporary files and base venv overhead.
        meter.check(2*expanded + 32*MIB)
        run_step([sys.executable, '-I', '-B', '-m', 'venv', '--without-pip', str(destination)],
                 destination, meter, 'create-venv', timeout=120)
        python = destination / 'bin/python'
        run_step([python, '-I', '-B', '-m', 'ensurepip', '--default-pip'],
                 destination, meter, 'ensurepip', timeout=120)
        pip = [python, '-I', '-B', '-m', 'pip', '--isolated', '--disable-pip-version-check',
               'install', '--no-cache-dir', '--no-index', '--no-deps', '--no-build-isolation',
               '--require-hashes', '--find-links', stage]
        for label, lock in [('build-tools', 'build-tools-py312.lock'),
                            ('runtime', 'daily-review-py312-macos-x86_64.lock')]:
            run_step([*pip, '-r', original.LOCK_ROOT / lock], destination, meter, label)
        # This original verifier checks exactly the locked distributions, three doctor
        # profiles and the TEST_FIXTURE app/CLI/database smoke; no service starts.
        run_step([python, '-I', '-B', Path(original.__file__).resolve(), 'verify',
                  '--destination', destination], destination, meter, 'verify', timeout=1200)
        meter.data['status'] = 'ACCEPTED_TEST_FIXTURE_RUNTIME'
    except BaseException as error:
        meter.data.update(status='FAILED', error_type=type(error).__name__, error=str(error))
        raise
    finally:
        # Only this invocation's generated artifact staging is disposable.
        meter.data['stage_removed_logical_bytes'] = tree_bytes(stage)['logical_bytes']
        shutil.rmtree(stage)
        meter.data['wall_seconds'] = time.monotonic() - started
        meter.data['final_disk'] = tree_bytes(destination)
        with open(destination / 'measurement.json', 'x', encoding='utf-8') as output:
            json.dump(meter.data, output, ensure_ascii=False, indent=2)
            output.write('\n')
    return meter.data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--cache-root', type=Path, action='append', default=[],
                        help='Optional existing pip cache; only http-v2 bodies and wheels are read')
    args = parser.parse_args(argv)
    result = restore(args.destination, args.cache_root)
    print(json.dumps({key: result[key] for key in ('status', 'destination', 'download_bytes',
                     'cache_reused_bytes', 'peak_logical_bytes', 'wall_seconds')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
