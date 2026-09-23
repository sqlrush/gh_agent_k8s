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
    # 维度名、表头、行形状都照 gaussdb-health/scripts/collectors.py —— 第一版样例是按设计稿编的,
    # 页面对着它写,真数据上维度中文名、表格全都对不上(2026-09-23 真集群截图发现)。
    M = model
    dims = [
        M.DimResult(dimension=M.DIM_OVERVIEW, headline="命中率 99.1%、连接 61/1000、未在恢复、最老事务 无",
                    headers=["cache_hit%", "connections", "max_conn", "in_recovery", "最老事务"], rows=[["99.1", "61", "1000", "false", ""]]),
        M.DimResult(dimension=M.DIM_SLOWSQL, headline="Top1 avg 8412ms ×214(共3条超阈值)",
                    headers=["sql_id", "calls", "avg_ms", "total_s", "cpu_s", "query"],
                    rows=[["1a9f", "214", "8412.00", "1800.17", "12.40", "SELECT r.region, s.store, sum(f.amount) FROM fact_sales f JOIN customers c ON c.id=f.cust_id GROUP BY 1,2"],
                          ["77e0", "1880", "2105.00", "3957.40", "2.10", "SELECT category, sum(amount) FROM fact_sales WHERE sale_date >= $1 GROUP BY category"]]),
        M.DimResult(dimension=M.DIM_XACT, headline="1 个空闲中事务已持续 6 分钟",
                    headers=["pid", "user", "state", "时长(s)", "query"],
                    rows=[["140233", "batch", "idle in transaction", "372", "UPDATE accounts SET balance = balance - $1 WHERE id = $2"]]),
        M.DimResult(dimension=M.DIM_CONN, headline="共 61:active 14、idle 47", headers=["state", "会话数"], rows=[["active", "14"], ["idle", "47"]]),
        M.DimResult(dimension=M.DIM_LOGS, headline="checkpoint req 占比 0%、归档 off", headers=["指标", "值"],
                    rows=[["checkpoint timed/req", "17695/41"], ["archive_mode", "off"]]),
        M.DimResult(dimension=M.DIM_REPL, headline="1 个备机 · 延迟 0.2 s",
                    headers=["standby", "client", "state", "sync", "replay_lag"], rows=[["dn_6002", "10.0.0.12", "streaming", "sync", "0.2 s"]]),
        M.DimResult(dimension=M.DIM_SCHEMA, headline="窗口内未使用的索引 2、无主键大表 1(观测窗口 自 2026-09-06 起 17.0 天)",
                    headers=["项", "对象", "值"],
                    rows=[["观测窗口", "pg_stat_database.stats_reset", "自 2026-09-06 11:56:28 起 17.0 天"],
                          ["普通索引", "public.idx_orders_tmp", "795.0M 扫描 0 次"], ["无主键大表", "public.fact_sales", "6.1 GB"]]),
        M.DimResult(dimension=M.DIM_CONCURRENCY, headline="死锁 0、回滚率 0.0%、2PC 0", headers=["指标", "值"],
                    rows=[["deadlocks", "0"], ["commit/rollback", "1558509/597 (0.0%回滚)"]]),
    ]
    findings = [
        Finding(M.DIM_SLOWSQL, "SLOW_AVG_MS", Severity.WARN, "avg_ms", "8,412", "> 1,000", "sql_id 1a9f · 214 次", sql_id="1a9f"),
        Finding(M.DIM_SCHEMA, "UNUSED_INDEX", Severity.NOTICE, "idx_scan", "0", "> 10M 且 0 次", "public.idx_orders_tmp 795.0M"),
        Finding(M.DIM_XACT, "IDLE_IN_XACT", Severity.NOTICE, "idle_in_xact_min", "6", "> 5", "pid 140233 · app=batch-loader"),
        # 子技能(waitevent)的发现带自己的维度名,不属于上面 8 个维度 —— 页面要单独计数
        Finding("DB Time", "LWLOCK_EVENT", Severity.WARN, "LWLOCK_EVENT 耗时占 DB_TIME", "42.8%", ">=10.0%",
                "snapshot.snap_global_wait_events wait_class=LWLOCK_EVENT"),
    ]
    for d in dims:
        d.findings = [f for f in findings if f.dimension == d.dimension]
    subs = [types.SimpleNamespace(skill="gaussdb-lockwait", ok=True, error=""),
            types.SimpleNamespace(skill="gaussdb-waitevent", ok=True, error=""),
            types.SimpleNamespace(skill="gaussdb-vacuum", ok=False, error="超时 60 s")]
    for i, (ts, overall, fs) in enumerate(zip(TS, [Severity.NOTICE, Severity.NOTICE, Severity.WARN], [findings[2:3], findings[1:3], findings])):
        ev = model.HealthEvidence(conn=CONN, dims=dims, findings=fs, overall=overall)
        reports.archive("health", report.health_dict(ev, sub_results=subs), instance=CONN, now=ts)

    down = "请求中间件失败:Remote end closed connection without response"
    failed = [model.DimResult(dimension=x.dimension, available=False, note=down, headline="不可用:" + down) for x in dims]
    bad_subs = [types.SimpleNamespace(skill=s.skill, ok=False, error="error: " + down) for s in subs]
    ev = model.HealthEvidence(conn="og-std", dims=failed, findings=[], overall=Severity.OK)
    reports.archive("health", report.health_dict(ev, sub_results=bad_subs), instance="og-std", now=TS[2])


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
        # 维度名、表头、行形状照 gaussdb-wdr/scripts/collectors.py:Load Profile 与 Database Stat 各一行、按列存值。
        # 第一版样例把它们编成「按行名存」,页面对着写,真数据上 8 个 KPI 全是「未采集」。
        M = model
        cpu_pct = 72.0 if sev else 86.0
        ev.dims = [
            M.DimResult(dimension=M.DIM_LOADPROFILE, headline="DB time %ss(其中 CPU %.0f%%)" % (dbtime, cpu_pct),
                        headers=["DB time(s)", "CPU time(s)", "CPU占DBtime%", "commits", "物理读(块)", "逻辑读(块)"],
                        rows=[[f"{dbtime:.2f}", f"{dbtime * cpu_pct / 100:.2f}", f"{cpu_pct:.2f}", "14688", str(reads * 540), "3200000"]]),
            M.DimResult(dimension=M.DIM_DBSTAT, headline="commit 14688 / rollback 44(回滚率 0.3%%)、命中率 %s%%" % hit,
                        headers=["commits", "rollbacks", "回滚率%", "死锁", "临时溢出", "cache_hit%"],
                        rows=[["14688", "44", "0.30", "0", "19763.20MiB" if sev else "0.00MiB", str(hit)]]),
            M.DimResult(dimension=M.DIM_TOPSQL, headline="各维度元凶 — DB time:1a9f(41%)",
                        headers=["sql_id", "calls", "elapsed_s", "cpu_s", "spill_MB", "物理读(块)", "占DB time%", "query"],
                        rows=[["1a9f", "3", "2410.00", "610.20", "19300.00", "1200000", "41.53",
                               "SELECT r.region, s.store, sum(f.amount) FROM fact_sales f JOIN customers c ON c.id=f.cust_id GROUP BY 1,2"],
                              ["4c77", "2", "1180.00", "330.00", "0.00", "610000", "20.33", "SELECT sale_date, sum(amount) FROM fact_sales GROUP BY sale_date"],
                              ["e2b9", "14800", "95.00", "40.00", "0.00", "1200", "1.64", "UPDATE accounts SET balance = balance - $1 WHERE id = $2"]]),
            M.DimResult(dimension=M.DIM_WAITS, headline="Top 等待类 IO_EVENT 占 81%",
                        headers=["等待类", "waits", "wait_s", "占比%"],
                        rows=[["IO_EVENT", "120433", "437.00", "81.00"], ["LWLOCK_EVENT", "9001", "70.00", "13.00"], ["LOCK_EVENT", "12", "32.00", "6.00"]]),
            M.DimResult(dimension=M.DIM_CHECKPOINT, headline="checkpoint timed 2 / req 0(req 占 0%)",
                        headers=["timed_ckpt", "req_ckpt", "req占比%"], rows=[["2", "0", "0.00"]]),
            M.DimResult(dimension=M.DIM_CACHE, headline="fact_sales 物理读最多", headers=["对象", "物理读(块)", "逻辑读(块)"],
                        rows=[["fact_sales", "1900000", "3200000"], ["customers", "140000", "900000"]]),
            M.DimResult(dimension=M.DIM_FILEIO, headline="物理读 Top 文件:db14725/spc1663/f39021", headers=["文件", "物理读", "物理写"],
                        rows=[["db14725/spc1663/f39021", "2040000", "31000"], ["db14725/spc1663/f39022", "410000", "900"]]),
        ]
        ev.findings = [Finding(M.DIM_WAITS, "WDR_WAIT_CLASS_SKEW", Severity.WARN, "等待类倾斜 IO_EVENT", "81%", ">60%", "snap_global_wait_events 等待类聚合"),
                       Finding(M.DIM_TOPSQL, "WDR_SQL_DBTIME_SHARE", Severity.CRITICAL, "单条 SQL 占 DB time", "41.53%", ">30%",
                               "snap_summary_statement:sqlid 1a9f elapsed 2410s ×3")][:sev]
        ev.overall = Severity.CRITICAL if sev else Severity.OK
        ev.native = model.NativeInfo(generated=True, bytes=1_800_000, saved_path="/nas/me/reports/wdr/og5/%s.native.html" % TS[2])
        return ev
    reports.archive("wdr", ev_for(1914, 1915, 2972, 0.97, 96.4, 406, 0).to_dict(), instance=CONN, now=TS[1])
    reports.archive("wdr", ev_for(1915, 1916, 5803, 10.7, 93.6, 3771, 2).to_dict(), instance=CONN, now=TS[2])
    # 原生报告文件本身也写一份(占位),预览站上「下载 HTML →」才下得到
    native = reports.reports_dir() / "wdr" / CONN / ("%s.native.html" % TS[2])
    native.write_text("<html><body><h1>WDR 样例</h1>" + "<p>占位</p>" * 300 + "</body></html>", encoding="utf-8")


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
    # 监控类 SQL:调优技能按策略跳过时存的那份(sqltune.skip_payload 的形状),Top SQL 页据此不再挂「调优 →」
    reports.archive("sqltune", {"sql_id": "0b1c…e7", "conn": CONN, "skipped": "system", "system_objects": ["gs_session_memory_detail"]},
                    name="0b1c…e7", instance=CONN, now=TS[2])


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
