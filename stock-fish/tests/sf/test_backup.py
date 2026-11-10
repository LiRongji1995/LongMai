"""Offline tests for `sf backup` / `sf restore` (synthetic data directory, no network)."""
from __future__ import annotations

import io
import json
import tarfile

import pytest

from sf import backup


def _data(root):
    (root / "bars").mkdir(parents=True)
    (root / "bars" / "2026-11-09.parquet").write_bytes(b"bar-09")
    (root / "bars" / "2026-11-10.parquet").write_bytes(b"bar-10")
    (root / "meta").mkdir()
    (root / "meta" / "hype_notes.csv").write_text("date,code,theme,source\n2026-11-10,sz.001379,商业航天,\n")
    (root / "research").mkdir()
    (root / "research" / "seat_skill-2026-11-10-n2.parquet").write_bytes(b"cache")  # derived, excluded
    (root / "research" / "szse_weekly_supervision.csv").write_text("pub_date\n")
    (root / "bars" / "2026-11-11.tmp").write_bytes(b"partial")  # interrupted write, excluded


def test_backup_round_trip(tmp_path):
    src, dst, out = tmp_path / "data", tmp_path / "new", tmp_path / "backups"
    _data(src)
    made = backup.create(out, root=src, log=lambda *_: None)
    assert made["files"] == 4 and made["bars"] == dict(first="2026-11-09", last="2026-11-10", sessions=2)
    assert (out / (made["archive"].name + ".sha256")).read_text().split()[0] == made["sha256"]
    result = backup.restore(made["archive"], root=dst, log=lambda *_: None)
    assert result["restored"] == 4 and not (result["conflicts"] or result["damaged"] or result["missing"])
    assert (dst / "meta" / "hype_notes.csv").read_text() == (src / "meta" / "hype_notes.csv").read_text()
    assert not (dst / "research" / "seat_skill-2026-11-10-n2.parquet").exists()
    again = backup.restore(made["archive"], root=dst, log=lambda *_: None)  # idempotent
    assert again["restored"] == 0 and again["skipped"] == 4


def test_restore_never_overwrites_a_different_file(tmp_path):
    src, dst, out = tmp_path / "data", tmp_path / "new", tmp_path / "backups"
    _data(src)
    made = backup.create(out, root=src, log=lambda *_: None)
    (dst / "meta").mkdir(parents=True)
    (dst / "meta" / "hype_notes.csv").write_text("newer local notes\n")
    result = backup.restore(made["archive"], root=dst, log=lambda *_: None)
    assert result["conflicts"] == ["meta/hype_notes.csv"]
    assert (dst / "meta" / "hype_notes.csv").read_text() == "newer local notes\n"


def test_damaged_download_is_refused(tmp_path):
    src, out = tmp_path / "data", tmp_path / "backups"
    _data(src)
    made = backup.create(out, root=src, log=lambda *_: None)
    raw = bytearray(made["archive"].read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    made["archive"].write_bytes(bytes(raw))
    with pytest.raises(ValueError, match="sha256"):
        backup.restore(made["archive"], root=tmp_path / "new", log=lambda *_: None)
    assert not (tmp_path / "new").exists()


def test_backup_refuses_output_inside_data_dir(tmp_path):
    _data(tmp_path / "data")
    with pytest.raises(ValueError, match="inside"):
        backup.create(tmp_path / "data" / "backups", root=tmp_path / "data", log=lambda *_: None)


def test_restore_rejects_path_traversal(tmp_path):
    archive = tmp_path / "evil.tar.gz"
    info = dict(format=backup.FORMAT, created_at="x", code={}, bars={}, files=1, bytes=1,
                entries=[dict(path="../escape.txt", size=1, sha256="0" * 64)])
    with tarfile.open(archive, "w:gz") as tar:
        for name, data in ((backup.MANIFEST, json.dumps(info).encode()), ("../escape.txt", b"x")):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    with pytest.raises(ValueError, match="unexpected archive member"):
        backup.restore(archive, root=tmp_path / "new", log=lambda *_: None)
    assert not (tmp_path / "escape.txt").exists()
