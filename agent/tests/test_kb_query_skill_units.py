"""gaussdb-kb(查询 skill)—— runtime 镜像里只有它;导入命令物理不在。"""
import importlib.util
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
_Q = _ROOT / "skills" / "gaussdb-kb" / "scripts"
_I = _ROOT / "skills" / "gaussdb-kb-import" / "scripts"


def _load(scripts: pathlib.Path, name: str):
    """两个 skill 的入口都叫 kb.py:按目录取不同的模块名,别让 sys.modules 里的那个顶掉。"""
    modname = f"{scripts.parent.name}_{name}".replace("-", "_")
    if modname in sys.modules:
        return sys.modules[modname]
    saved = sys.path[:]
    sys.path.insert(0, str(scripts))
    try:
        spec = importlib.util.spec_from_file_location(modname, scripts / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[modname] = mod
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = saved
    return mod


def _commands(mod) -> set:
    parser = mod.build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    return set(sub.choices)


def test_query_skill_has_the_four_read_commands():
    assert {"query", "health", "search", "cite-check"} <= _commands(_load(_Q, "kb"))


def test_import_skill_has_no_read_only_duplicates():
    cmds = _commands(_load(_I, "kb"))
    assert {"ingest", "index", "validate", "propose", "review", "apply", "setup", "feedback", "eval", "contract"} <= cmds
    assert not cmds & {"query", "health", "search", "cite-check"}


def test_query_skill_scripts_contain_no_import_code():
    names = {p.name for p in _Q.glob("*.py")}
    assert names == {"kb.py", "kb_cite.py"}
    for p in _Q.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        assert "kb_cases" not in text and "def cmd_apply" not in text and "def cmd_ingest" not in text, p.name


def _kb(tmp_path: pathlib.Path) -> pathlib.Path:
    kb = tmp_path / "kb"
    (kb / "rules").mkdir(parents=True)
    (kb / "rules" / "sq.yaml").write_text(
        "- id: GS-SQ-001\n  severity: warn\n  check: advisory\n  rule: 禁止 select star\n", encoding="utf-8")
    return kb


def test_query_search_runs_against_a_kb(tmp_path, capsys):
    mod = _load(_Q, "kb")
    assert mod.main(["search", "select star", "--kb", str(_kb(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert "rules/sq.yaml" in out and "select star" in out      # search 是按行 grep,命中的是 rule: 那一行


def test_query_health_runs_in_file_mode(tmp_path, capsys):
    mod = _load(_Q, "kb")
    rc = mod.main(["health", "--kb", str(_kb(tmp_path))])
    out = capsys.readouterr().out
    assert rc == 0 and "模式:文件" in out and "收件目录" in out


def test_query_missing_kb_dir_is_a_clean_error(tmp_path, capsys):
    mod = _load(_Q, "kb")
    rc = mod.main(["search", "x", "--kb", str(tmp_path / "nope")])
    assert rc == 1 and "KB 目录不存在" in capsys.readouterr().err
