"""entrypoint.sh 的契约——只 grep 结构,不跑它。

报告只读端口是大盘的数据口:entrypoint 要导出 GSDB_REPORTS_DIR、在 opencode 之后拉起
serve-reports、停机时把它和另外两个后台循环一起收掉。漏任何一条都是「大盘无数据且不报错」。
"""
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_EP = (_ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")


def test_reports_dir_is_exported_under_nas_me():
    assert re.search(r'^export GSDB_REPORTS_DIR=\$NAS_ME/reports\b', _EP, re.M), "报告目录必须在 NAS 上,随 Pod 重建不丢"


def test_reports_server_is_started_after_opencode_and_backgrounded():
    oc = _EP.index("opencode serve --hostname")
    m = re.search(r'\$PODCTL serve-reports --dir "\$GSDB_REPORTS_DIR" --port 4097 --kb-dir "\$GSDB_KB_DIR" &\s*\nREPORTS=\$!', _EP)
    assert m and m.start() > oc, "serve-reports 要在 opencode serve 之后、放后台并记 PID,且带 --kb-dir(大盘知识库页签的数据)"


def test_reports_server_is_killed_with_the_other_background_loops():
    assert re.search(r'^kill "\$REFRESHER" "\$SYNCER" "\$REPORTS"', _EP, re.M), "停机要连报告服务一起收掉"
