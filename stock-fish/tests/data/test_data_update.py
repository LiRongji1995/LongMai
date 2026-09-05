import hashlib
import importlib.util
import io
from pathlib import Path
import struct
import tarfile
import pytest


@pytest.fixture
def runner():
    source = Path(__file__).resolve().parents[2]/"qlib-zh/data_runner.py"
    spec = importlib.util.spec_from_file_location("safe_data_runner", source)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def archive(path, entries):
    with tarfile.open(path, "w:gz") as handle:
        for name, value in entries.items():
            member = tarfile.TarInfo(name); member.size = len(value)
            handle.addfile(member, io.BytesIO(value))
    return path


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/escape", "safe/../../escape", "safe\\escape"])
def test_archive_traversal_rejected_before_writing(runner, tmp_path, name):
    path = archive(tmp_path/"bad.tar.gz", {"valid.txt":b"valid", name:b"bad"})
    with pytest.raises(ValueError): runner._extract_tar_gz(path, tmp_path/"new")
    assert not list((tmp_path/"new").iterdir())


def test_archive_symlink_and_existing_target_rejected(runner, tmp_path):
    path = tmp_path/"link.tar.gz"
    with tarfile.open(path, "w:gz") as handle:
        member = tarfile.TarInfo("link"); member.type=tarfile.SYMTYPE; member.linkname="../outside"; handle.addfile(member)
    with pytest.raises(ValueError): runner._extract_tar_gz(path, tmp_path/"new")
    (tmp_path/"active").mkdir(); (tmp_path/"active/sentinel").write_text("unchanged")
    with pytest.raises(ValueError): runner._extract_tar_gz(path, tmp_path/"active")
    assert (tmp_path/"active/sentinel").read_text() == "unchanged"


def test_update_verifies_and_publishes_immutable_snapshot_without_overwrite(runner, tmp_path, monkeypatch):
    path = archive(tmp_path/"valid.tar.gz", {"cn_data/calendars/day.txt":b"2026-09-04\n",
        "cn_data/instruments/all.txt":b"SZ000001\t2026-09-04\t2026-09-04\n",
        "cn_data/instruments/csi300.txt":b"SZ000001\t2026-09-04\t2026-09-04\n",
        "cn_data/features/sz000001/close.day.bin":struct.pack("<ff",0.,10.)})
    active = tmp_path/"active"; active.mkdir(); (active/"sentinel").write_text("old")
    monkeypatch.setattr(runner,"QLIB_DATA_DIR",active)
    monkeypatch.setattr(runner,"_get_latest_release_info",lambda:dict(tag="fixture",download_url="https://fixture",size=path.stat().st_size,digest="sha256:"+hashlib.sha256(path.read_bytes()).hexdigest()))
    monkeypatch.setattr(runner,"_download_file",lambda url,dest,**kw: dest.write_bytes(path.read_bytes()))
    result = runner.run_data_update()
    assert result["success"] and result["activation_required"]
    assert Path(result["data_dir"]).is_dir() and (active/"sentinel").read_text() == "old"
    assert runner.run_data_update()["data_version"] == result["data_version"]
    failed = runner.run_data_update(expected_sha256="0"*64)
    assert not failed["success"] and failed["error"] == "ARCHIVE_SHA256_MISMATCH"
    assert (active/"sentinel").read_text() == "old"
