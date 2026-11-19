"""白名单增量的说明文件(docs/delivery/delta-notes.yaml)不许有重复的脚本名(2026-11-19)。

YAML 里同一个键写两次不报错,后一个静默覆盖前一个。这份文件新的说明写在前面、旧的留在后面,
于是 v1.4.14 的增量 scripts-delta-20261116.sql 里 vacuum.dead_tuples 的变更说明被一条更早的旧说明顶替了
(SQL 正文是对的,说明写错了)。工具读这份文件时遇到重复键直接报错;仓库里这份文件也要能被它读进来。
"""
import importlib.util
import pathlib
import sys

import pytest

_AGENT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("delivery_delta_under_test", _AGENT / "tools" / "delivery_delta.py")
dd = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = dd
_spec.loader.exec_module(dd)


def test_duplicate_script_names_in_the_notes_are_refused():
    with pytest.raises(ValueError, match="vacuum.dead_tuples"):
        dd.load_notes("changed:\n  vacuum.dead_tuples: 新说明\n  lockwait.pairs: x\n  vacuum.dead_tuples: 旧说明\n")


def test_the_notes_in_the_repo_have_no_duplicates_and_cover_this_round():
    notes = dd.load_notes(dd.NOTES.read_text(encoding="utf-8"))
    for name in ("lockwait.pairs", "health.stale_stats", "health.unused_index", "perf.table_stat", "explain.kernel_funcs"):
        assert "concat" in notes["changed"][name], name
