"""gaussdb-kb 的 health --json 与 query 检索日志 —— 大盘知识库页的两个数据来源。"""
import json
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "gaussdb-kb" / "scripts"))
sys.path.insert(0, str(_ROOT))

import kb as kbcli  # noqa: E402
from common.kb import query as kbquery  # noqa: E402


class _Sess:
    def __init__(self, status):
        self._s = status
        self.pg = type("PG", (), {"warnings": ["cases/bad.md: 缺 frontmatter"]})()

    def status(self):
        return self._s

    def close(self):
        pass


def _fake_open(status):
    return lambda kb: _Sess(status)


def test_health_json_shape_when_attached(monkeypatch, tmp_path, capsys):
    kbdir = tmp_path / "kb"
    (kbdir / "index").mkdir(parents=True)
    (kbdir / "index" / "misses.log").write_text(
        "2026-09-20\tNOPK_BIGTABLE\tq\n2026-09-21\tNOPK_BIGTABLE\tq\n2026-09-21\tREPL_LAG\tq\n", encoding="utf-8")
    st = kbquery.KbStatus(attached=True, version="3", counts={"docs.case": 286, "docs.rule": 412},
                          vector="DataVec(覆盖 93%)", graph="图文件", mode="向量库+图文件")
    monkeypatch.setattr(kbquery.KbSession, "open", staticmethod(_fake_open(st)))
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path / "reports"))
    rc = kbcli.main(["health", "--kb", str(kbdir), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["status"]["counts"]["docs.case"] == 286 and out["status"]["mode"] == "向量库+图文件"
    assert out["misses"] == [{"code": "NOPK_BIGTABLE", "n": 2}, {"code": "REPL_LAG", "n": 1}]
    assert out["file_warnings"] == ["cases/bad.md: 缺 frontmatter"]
    assert out["readonly"] is False and out["inbox"].endswith("inbox/uploads")
    assert out["pending"] == [] and out["index_state"] == {}
    archived = json.loads((tmp_path / "reports" / "kb" / "health.json").read_text(encoding="utf-8"))
    assert archived["status"]["counts"]["docs.rule"] == 412


def test_health_json_when_not_attached_keeps_reason_and_exit_2(monkeypatch, tmp_path, capsys):
    kbdir = tmp_path / "kb"
    kbdir.mkdir()
    st = kbquery.KbStatus(attached=False, reason="未接入")
    monkeypatch.setattr(kbquery.KbSession, "open", staticmethod(_fake_open(st)))
    monkeypatch.delenv("GSDB_REPORTS_DIR", raising=False)
    rc = kbcli.main(["health", "--kb", str(kbdir), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["status"]["attached"] is False and out["status"]["reason"] == "未接入"


def test_health_text_output_unchanged_and_still_archives(monkeypatch, tmp_path, capsys):
    kbdir = tmp_path / "kb"
    kbdir.mkdir()
    st = kbquery.KbStatus(attached=True, counts={"docs.case": 1, "docs.rule": 2, "docs.raw": 0}, mode="文件")
    monkeypatch.setattr(kbquery.KbSession, "open", staticmethod(_fake_open(st)))
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path / "reports"))
    kbcli.main(["health", "--kb", str(kbdir)])
    text = capsys.readouterr().out
    assert text.startswith("> 知识库") and "缺口清单  : 无记录" in text
    assert (tmp_path / "reports" / "kb" / "health.json").is_file()
