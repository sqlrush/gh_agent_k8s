"""部署自检(gaussdb-login/scripts/selfcheck.py,2026-11-08 加)。

行方测试报告里一半的「失败 / 受限」是环境问题:新包没装进运行目录、白名单没导全、中间件跑不了多条语句、
受限账号没权限、监控开关没开、知识库目录不存在。自检要一次把它们都点出来,并说清该找谁。
"""
import importlib.util
import pathlib
import sys
from dataclasses import replace

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common.access import QueryError  # noqa: E402
from common.config import Connection  # noqa: E402
from common.grmp.registry import Registry  # noqa: E402

_SCRIPTS = _ROOT / "skills" / "gaussdb-login" / "scripts"
sys.modules.pop("render", None)
sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location("selfcheck_under_test", _SCRIPTS / "selfcheck.py")
sc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sc
_spec.loader.exec_module(sc)
sys.path.remove(str(_SCRIPTS))

CONN = Connection(name="10-0-0-9-appdb", type="gaussdb", host="127.0.0.1", port=8769, database="appdb",
                  user="grmp", driver="grmp", data_ip="10.0.0.9", app="api")
GOOD_TOP = {"unique_sql_id": "1", "query": "select 1", "calls": "1", "total_sec": "1", "avg_ms": "1", "rows": "1",
            "cpu_sec": "0", "io_sec": "0", "rows_written": "0", "user_name": "app"}


class _Client:
    def __init__(self, names):
        self.names = names

    def list_operations(self):
        return [{"cmd_name": n} for n in self.names]


class _Runner:
    def __init__(self, names, broken=(), old_top=False, storage_null=False, multi=True, switches=None):
        self.client = _Client(names)
        self.broken, self.old_top, self.storage_null, self.multi = set(broken), old_top, storage_null, multi
        self.switches = switches or {"enable_stmt_track": "on", "track_functions": "all",
                                     "enable_resource_track": "on", "enable_wdr_snapshot": "on"}

    def run(self, script, values=None):
        if script in self.broken:
            raise QueryError("中间件返回 HTTP 400:agent诊断脚本执行失败")
        if script == "explain.multi_stmt_probe":
            if not self.multi:
                raise QueryError("中间件返回 HTTP 400:agent诊断脚本执行失败")
            return [{"ok": "1"}]
        if script == "health.storage":
            return [{"kind": "database", "name": "appdb", "bytes": "100"},
                    {"kind": "database", "name": "other", "bytes": "" if self.storage_null else "5"}]
        if script == "topsql.top_sql":
            row = dict(GOOD_TOP)
            if self.old_top:
                for k in ("cpu_sec", "io_sec", "rows_written", "user_name"):
                    row.pop(k)
            return [row]
        if script == "health.slow_sql":
            return [{"unique_sql_id": "1", "user_name": "app"}]
        if script == "config.settings":
            return [{"name": k, "setting": v} for k, v in self.switches.items()]
        return [{"x": "1"}]


def _run(monkeypatch, tmp_path, runner, capsys, kb=True):
    kb_dir = tmp_path / "kb"
    if kb:
        kb_dir.mkdir()
    monkeypatch.setenv("GSDB_KB_DIR", str(kb_dir))
    monkeypatch.setenv("GSDB_SKILLS_DIR", str(tmp_path))
    monkeypatch.setattr(sc.config, "resolve", lambda name=None: CONN)
    monkeypatch.setattr(sc.access, "runner_for", lambda conn: runner)
    rc = sc.main([])
    return rc, capsys.readouterr().out


def test_a_healthy_environment_passes(monkeypatch, tmp_path, capsys):
    rc, out = _run(monkeypatch, tmp_path, _Runner(Registry().names()), capsys)
    assert rc == 0 and "全部正常" in out and sc.package_version() in out


def test_the_bank_environment_is_explained_item_by_item(monkeypatch, tmp_path, capsys):
    names = [n for n in Registry().names() if not n.startswith("health.xlog")]      # 白名单没导全
    runner = _Runner(names, broken={"health.slow_sql"}, old_top=True, storage_null=True, multi=False,
                     switches={"enable_stmt_track": "on", "track_functions": "none"})
    rc, out = _run(monkeypatch, tmp_path, runner, capsys, kb=False)
    assert rc == 1
    assert "缺 " in out and "health.xlog_position" in out and "中间件团队" in out
    assert "不支持:执行计划与调优改为补全表名后单条执行" in out
    assert "health.slow_sql 执行失败" in out and "中间件日志" in out
    assert "topsql.top_sql 是旧版本" in out and "user_name" in out
    assert "1 个库 / 表空间没有权限查看" in out and "运维 DBA" in out
    assert "track_functions" in out and "存储过程排行" in out
    assert "知识库目录" in out and "不存在" in out


def test_without_a_session_it_says_to_log_in_first(monkeypatch, tmp_path, capsys):
    from common.config import ConfigError

    def _no(name=None):
        raise ConfigError("本对话未登录")
    monkeypatch.setenv("GSDB_SKILLS_DIR", str(tmp_path))
    monkeypatch.setattr(sc.config, "resolve", _no)
    assert sc.main([]) == 1
    assert "先用 gaussdb-login 登录再自检" in capsys.readouterr().out


def test_gsql_mode_skips_the_middleware_whitelist(monkeypatch, tmp_path, capsys):
    direct = replace(CONN, driver="gsql")
    monkeypatch.setenv("GSDB_KB_DIR", str(tmp_path))
    monkeypatch.setenv("GSDB_SKILLS_DIR", str(tmp_path))
    monkeypatch.setattr(sc.config, "resolve", lambda name=None: direct)
    monkeypatch.setattr(sc.access, "runner_for", lambda conn: _Runner([]))
    assert sc.main([]) == 0
    assert "| 白名单 |" not in capsys.readouterr().out
