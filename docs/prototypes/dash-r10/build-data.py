#!/usr/bin/env python3
"""R3 原型的数据:读 dash-samples.py 生成的样例报告目录,原样打成 data.js(window.DASH)。

字段与真实报告完全一致(页面按真字段名取值,不按设计稿编)。唯一的补充:巡检历史与
WDR 窗口历史只有 2–3 条,画不出时间轴的样子,所以在 index 前面补若干条 `demo: true`
的示意条目 —— 页面上明确标成「示意」,点不开、不参与任何计算。

用法: python3 build-data.py <样例报告目录> > data.js
      样例报告目录由 scripts/dash-preview.sh 或 scripts/dash-samples.py 生成。
"""
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])


def load(p):
    return json.loads((root / p).read_text(encoding="utf-8"))


def runs(skill, key):
    """index + 每一份完整报告(按文件名)。"""
    idx = load(f"{skill}/{key}/index.json")
    full = {}
    for e in idx:
        f = root / skill / key / e["file"]
        if f.is_file():
            full[e["file"]] = json.loads(f.read_text(encoding="utf-8"))
    return {"index": idx, "runs": full}


def demo_health(first_at):
    # 补在真实条目之前:09-09 起每天一次,多数正常/关注,偶有告警 —— 只为看时间轴的样子
    pattern = [0, 0, 1, 0, 0, 1, 2, 1, 0, 0, 1]
    out = []
    for i, ov in enumerate(pattern):
        c = {"1": 1} if ov == 1 else ({"2": 1, "1": 1} if ov == 2 else {})
        out.append({"at": f"202609{9 + i:02d}T090000Z", "file": "", "overall": ov, "counts": c,
                    "dims": {"total": 8, "na": 0}, "demo": True})
    # 一次采集失败的示意:全部维度没采到
    out[4] = {"at": "20260913T090000Z", "file": "", "overall": 0, "counts": {}, "dims": {"total": 8, "na": 8}, "demo": True}
    return out


def demo_wdr():
    pattern = [0, 1, 0, 0, 2, 1]
    return [{"at": f"202609{15 + i:02d}T100000Z", "file": "", "overall": ov,
             "counts": ({str(ov): 1} if ov else {}), "dims": {"total": 7, "na": 0}, "demo": True}
            for i, ov in enumerate(pattern)]


health = {t["key"]: runs("health", t["key"]) for t in load("health/targets.json")}
health["og5"]["index"] = demo_health(None) + health["og5"]["index"]
wdr = {t["key"]: runs("wdr", t["key"]) for t in load("wdr/targets.json")}
wdr["og5"]["index"] = demo_wdr() + wdr["og5"]["index"]
topsql = {t["key"]: runs("topsql", t["key"]) for t in load("topsql/targets.json")}
sqltune = {}
for t in load("sqltune/targets.json"):
    k = t["key"]
    sqltune[k] = {}
    for f in (root / "sqltune" / k).glob("*.json"):
        if f.name not in ("index.json", "latest.json"):
            d = json.loads(f.read_text(encoding="utf-8"))
            sqltune[k][d["sql_id"]] = d

queries = [json.loads(l) for l in (root / "kb/queries.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
DASH = {
    "whoami": load("whoami.json"),
    "targets": load("health/targets.json"),
    "health": health, "wdr": wdr, "topsql": topsql, "sqltune": sqltune,
    "kb": {"health": load("kb/health.json"), "catalog": load("_kb/catalog.json"), "queries": queries},
}
sys.stdout.write("// 由 build-data.py 生成,勿手改\nwindow.DASH = " + json.dumps(DASH, ensure_ascii=False) + ";\n")
