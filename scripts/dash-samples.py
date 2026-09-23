#!/usr/bin/env python3
"""生成大盘的样例报告目录 —— **用 Python 侧真实的报告类与 reports.archive() 生成**,不手写 JSON。

  scripts/dash-samples.py <输出目录>

输出目录的布局就是用户 Pod 里 /nas/me/reports 的布局(4097 端出来的那份):
  health/og5/{<ts>.json,latest.json,index.json}  health/targets.json
  topsql/og5/…  wdr/og5/…  sqltune/og5/1a9f.json  kb/health.json  kb/queries.jsonl  whoami.json

两个用途:
  · scripts/dash-preview.sh 拿它起本地预览站,四个页面对着它开发与验收;
  · agent/tests/test_frontend_contract_units.py 拿同一批构造函数核对 frontend/lib/contract.json。
样例形状一变,两边一起变 —— 这就是不手写 JSON 的原因。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import types

_ROOT = pathlib.Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(_ROOT))

from common import reports  # noqa: E402
from common.finding import Finding, Severity  # noqa: E402

CONN = "og5"
TS = ["20260920T090000Z", "20260921T090700Z", "20260922T140300Z"]


def _load(scripts: str, *purge: str):
    for m in purge:
        sys.modules.pop(m, None)
    sys.path.insert(0, str(_ROOT / "skills" / scripts / "scripts"))


def health_samples():
    _load("gaussdb-health", "model", "thresholds", "util", "collectors", "report", "health", "aggregate", "render")
    import model, report  # noqa: E402
    dims = [
        model.DimResult(dimension="overview", headline="缓存命中 99.1% · 连接 61/1000 · 非恢复态",
                        headers=["cache_hit%", "connections", "max_conn"], rows=[["99.1", "61", "1000"]]),
        model.DimResult(dimension="slowsql", headline="3 条语句平均耗时 > 1 s,最慢 8.4 s",
                        headers=["sql_id", "avg_ms", "calls"], rows=[["1a9f", "8412", "214"], ["77e0", "2105", "1880"]]),
        model.DimResult(dimension="xact", headline="1 个空闲中事务已持续 6 分钟", headers=["state", "n"], rows=[["idle in transaction", "1"]]),
        model.DimResult(dimension="conn", headline="active 14 · idle 47", headers=["state", "会话数"], rows=[["active", "14"], ["idle", "47"]]),
        model.DimResult(dimension="logs", headline="近 1h 无 FATAL / PANIC", headers=["指标", "值"], rows=[["ERROR", "3"], ["FATAL", "0"]]),
        model.DimResult(dimension="repl", headline="1 个备机 · 延迟 0.2 s", headers=["备机", "lag"], rows=[["1", "0.2 s"]]),
        model.DimResult(dimension="schema", headline="2 个无效索引 · 1 张表无主键且 > 5 GB",
                        headers=["项", "对象", "值"], rows=[["无效索引", "idx_orders_tmp", ""], ["无主键大表", "fact_sales", "6.1 GB"]]),
        model.DimResult(dimension="concurrency", headline="TPS 27 · 回滚率 0.3%", headers=["指标", "值"], rows=[["xact_commit", "27.1 /s"]]),
        model.DimResult(dimension="locks", headline="无阻塞链", headers=["等待锁", "阻塞链"], rows=[["0", "0"]]),
        model.DimResult(dimension="waits", headline="IO 事件 94% · LWLock 6%", headers=["事件", "占比"], rows=[["DataFileRead", "61%"], ["BufferPin", "4%"]]),
        model.DimResult(dimension="bloat", available=False, note="子技能 gaussdb-vacuum 超时 60 s", headline="不可用:子技能 gaussdb-vacuum 超时 60 s"),
    ]
    findings = [
        Finding("slowsql", "SLOW_AVG_MS", Severity.WARN, "avg_ms", "8,412", "> 1,000", "sql_id 1a9f · 214 次", sql_id="1a9f"),
        Finding("schema", "INVALID_INDEX", Severity.WARN, "invalid_index", "2", "> 0", "idx_orders_tmp, idx_ev_2"),
        Finding("schema", "NOPK_BIGTABLE", Severity.WARN, "no_pk_gb", "6.1 GB", "> 5 GB", "public.fact_sales"),
        Finding("xact", "IDLE_IN_XACT", Severity.NOTICE, "idle_in_xact_min", "6", "> 5", "pid 140233 · app=batch-loader"),
    ]
    for d in dims:
        d.findings = [f for f in findings if f.dimension == d.dimension]
    subs = [types.SimpleNamespace(skill="gaussdb-lockwait", ok=True, error=""),
            types.SimpleNamespace(skill="gaussdb-waitevent", ok=True, error=""),
            types.SimpleNamespace(skill="gaussdb-vacuum", ok=False, error="超时 60 s")]
    for i, (ts, overall, fs) in enumerate(zip(TS, [Severity.OK, Severity.NOTICE, Severity.WARN], [findings[3:], findings[2:], findings])):
        ev = model.HealthEvidence(conn=CONN, dims=dims if i == 2 else dims[:8], findings=fs, overall=overall)
        reports.archive("health", report.health_dict(ev, sub_results=subs), instance=CONN, now=ts)


def topsql_samples():
    _load("gaussdb-topsql", "topsql", "render")
    import topsql  # noqa: E402
    rows = [
        topsql.StmtRow("0b1c…e7", "SELECT sessid, sum(usedsize) FROM gs_session_memory_detail GROUP BY sessid", 1728000, 26470.0, 15.3, 1728000),
        topsql.StmtRow("9d40…13", "SELECT category, sum(amount) FROM fact_sales WHERE sale_date >= $1 GROUP BY category", 41200, 7420.0, 180.1, 41200),
        topsql.StmtRow("1a9f", "SELECT r.region, s.store, sum(f.amount) FROM fact_sales f JOIN customers c ON c.id=f.cust_id JOIN stores s ON s.id=f.store_id GROUP BY 1,2", 214, 1800.0, 8412.0, 2140),
        topsql.StmtRow("4c77…a0", "SELECT sale_date, sum(amount) FROM fact_sales GROUP BY sale_date", 8800, 1410.0, 160.2, 8800),
        topsql.StmtRow("e2b9…55", "UPDATE accounts SET balance = balance - $1 WHERE id = $2", 9800000, 980.0, 0.1, 9800000),
    ]
    for by, ts in (("time", TS[1]), ("avg", TS[2])):
        payload = {"conn": CONN, "by": by, "limit": 10, "rows": [r.__dict__ for r in rows]}
        reports.archive("topsql", payload, name="%s.%s" % (ts, by), instance=CONN, now=ts)


def wdr_samples():
    _load("gaussdb-wdr", "model", "thresholds", "util", "collectors", "report", "native", "snaps",
          "interp", "recheck", "finalreport", "ansi", "wdr", "render")
    import model  # noqa: E402
    def ev_for(begin, end, dbtime, aas, hit, reads, sev):
        ev = model.Evidence(conn=CONN)
        ev.window = model.Window(begin_id=begin, end_id=end, begin_ts="2026-09-22 10:30:00", end_ts="2026-09-22 10:39:00",
                                 duration_min=9, scope="node", node="dn_6001", wdr_enabled=True)
        ev.dims = [
            model.DimResult(dimension="loadprofile", headline="DB Time %s s · AAS %s" % (dbtime, aas), headers=["指标", "值", "每秒"],
                            rows=[["DB Time(s)", str(dbtime), ""], ["AAS", str(aas), ""], ["TPS", "27.2", "27.2"], ["WAL 写(KB/s)", "56.6", ""],
                                  ["临时文件(GB)", "19.3", ""], ["物理读(块/s)", str(reads), str(reads)]]),
            model.DimResult(dimension="dbstat", headline="缓存命中 %s%%" % hit, headers=["指标", "值"], rows=[["缓存命中%", str(hit)], ["回滚率%", "0.3"]]),
            model.DimResult(dimension="waits", headline="其他等待 69%", headers=["等待类", "waits", "wait_s", "占比%"],
                            rows=[["IO", "120433", "437", "7.5"], ["CPU", "", "1131", "19.5"], ["其他等待", "9001", "4009", "69.1"], ["网络", "", "44", "0.8"]]),
            model.DimResult(dimension="topsql", headline="Top 5 by DB Time", headers=["语句", "DB Time(s)", "次数", "物理读"],
                            rows=[["按区域/门店聚合 fact_sales ⋈ customers", "2410", "3", "1200000"], ["整表 sum fact_sales", "1180", "2", "610000"],
                                  ["按品类聚合 fact_sales", "760", "4", "220000"], ["UPDATE accounts 余额", "95", "14800", "1200"]]),
            model.DimResult(dimension="checkpoint", headline="定时 2 · 被动 0", headers=["指标", "值"],
                            rows=[["定时触发", "2"], ["被动触发", "0"], ["刷脏(MB)", "13.5"], ["写耗时(s)", "1.1"]]),
            model.DimResult(dimension="cache", headline="fact_sales 物理读最多", headers=["对象", "物理读(块)", "逻辑读(块)"],
                            rows=[["fact_sales", "1900000", "3200000"], ["customers", "140000", "900000"], ["idx_fs_date", "38000", "120000"]]),
            model.DimResult(dimension="fileio", headline="临时文件 19.3 GB", headers=["文件", "物理读", "物理写"],
                            rows=[["数据文件", "2040000", "31000"], ["临时文件", "0", "19300 MB"], ["WAL", "0", "30.6 MB"]]),
        ]
        ev.findings = [Finding("cache", "WDR_PHYS_READ_SPIKE", Severity.CRITICAL, "物理读", "3771 块/s", "×3 上窗", "三条串行大聚合把全表扫描压进本窗口"),
                       Finding("fileio", "WDR_TEMP_SPILL", Severity.WARN, "临时文件", "19.3 GB", "> 1 GB", "work_mem 不够,排序/哈希下盘")][:sev]
        ev.overall = Severity.CRITICAL if sev else Severity.OK
        ev.native = model.NativeInfo(generated=True, bytes=1_800_000, saved_path="/nas/me/reports/wdr/og5/%s.native.html" % TS[2])
        return ev
    reports.archive("wdr", ev_for(1914, 1915, 2972, 0.97, 96.4, 406, 0).to_dict(), instance=CONN, now=TS[1])
    reports.archive("wdr", ev_for(1915, 1916, 5803, 10.7, 93.6, 3771, 2).to_dict(), instance=CONN, now=TS[2])


def sqltune_samples():
    payload = {"conn": CONN, "sql_id": "1a9f", "source": "statement_history", "schema": "public",
               "original_sql": "SELECT r.region, s.store, sum(f.amount) FROM fact_sales f JOIN customers c ON c.id=f.cust_id GROUP BY 1,2",
               "substitution": {"sql": "", "placeholders": 0, "substitutions": []},
               "evidence": {"version": "openGauss 5.0", "analyzed": True, "plan": "Seq Scan on fact_sales (cost=0..612000)",
                            # 形状 = skills/gaussdb-sqltune/scripts/evidence.py 的 Finding.__dict__
                            "findings": [{"kind": "seq_scan_big_table", "severity": "warn",
                                          "detail": "fact_sales 全表扫描 6.1 GB",
                                          "advice": "建 (store_id, sale_date) 复合索引;先按 store 聚合再 JOIN regions"}],
                            "tables": [], "indexes": [], "columns": [], "gucs": []}}
    reports.archive("sqltune", payload, name="1a9f", instance=CONN, now=TS[2])


def kb_samples(out: pathlib.Path):
    sys.path.insert(0, str(_ROOT / "skills" / "gaussdb-kb" / "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("kb_cli_for_samples", _ROOT / "skills" / "gaussdb-kb" / "scripts" / "kb.py")
    kb = importlib.util.module_from_spec(spec); sys.modules["kb_cli_for_samples"] = kb; spec.loader.exec_module(kb)
    from common.kb import query as kbquery
    kbdir = out / "_kbdir"; (kbdir / "index").mkdir(parents=True, exist_ok=True)
    (kbdir / "index" / "misses.log").write_text("2026-09-20\tNOPK_BIGTABLE\tq\n2026-09-21\tNOPK_BIGTABLE\tq\n2026-09-21\tIDLE_IN_XACT\tq\n", encoding="utf-8")
    st = kbquery.KbStatus(attached=True, version="3", counts={"docs.case": 286, "docs.rule": 412, "docs.raw": 341},
                          vector="DataVec(覆盖 93%)", graph="图文件", mode="向量库+图文件")
    d = kb.health_dict(kbdir, st, ["cases/bad.md: 缺 frontmatter"], True)
    reports.archive("kb", d, name="health")
    for row in [{"at": "2026-09-22T14:05:00Z", "q": "慢 SQL 平均耗时超阈值 · 全表扫描", "key": "SLOW_AVG_MS", "hits_cases": 3, "hits_rules": 2, "how": "semantic", "elapsed_ms": 42},
                {"at": "2026-09-22T11:40:00Z", "q": "无主键大表怎么处理", "key": "NOPK_BIGTABLE", "hits_cases": 0, "hits_rules": 1, "how": "keyword", "elapsed_ms": 30},
                {"at": "2026-09-21T16:22:00Z", "q": "idle in transaction 6 分钟", "key": "IDLE_IN_XACT", "hits_cases": 0, "hits_rules": 0, "how": "keyword", "elapsed_ms": 25}]:
        reports.append_jsonl("kb", "queries", row)
    import shutil; shutil.rmtree(kbdir, ignore_errors=True)


def main(argv=None) -> int:
    a = argv if argv is not None else sys.argv[1:]
    if len(a) != 1:
        print(__doc__, file=sys.stderr); return 2
    out = pathlib.Path(a[0]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ["GSDB_REPORTS_DIR"] = str(out)
    health_samples(); topsql_samples(); wdr_samples(); sqltune_samples(); kb_samples(out)
    (out / "whoami.json").write_text(json.dumps({"user_id": "u1234"}), encoding="utf-8")
    print("样例报告已生成:%s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
