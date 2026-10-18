"""大盘上的时段报告(时段体检 / 时段 Top SQL):在 Node 里真跑页面的取数与建模,fetch 换成假的。

钉住的是 2026-10-18 实测出的三个坑:
① 时段报告今天存档,说的却是昨天 10:46–11:06。按存档时间筛,带时间段的直达链接打开是空页 ——
   range.js 对**任何**带 window 的条目都按窗口筛。
② Top SQL 页签只认 `<时间戳>.<维度>.json`,时段榜(`.time.span.json`)永远看不到 ——
   直达链接带 run=<文件>,load() 钉住那一份;页签仍只取累计榜。
③ 时段体检与此刻巡检维度不同,「比上次多 3 条」只在同一种口径里比;只跑过时段体检时 latest.json 不存在
   (时段报告不顶掉 latest),页面退回索引里最新的一份,不能显示「还没有报告」。
没有 node 时跳过(交付环境的单测不依赖 node)。
"""
import json
import os
import pathlib
import shutil
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FRONT = _ROOT / "frontend"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="没有 node")

_SCRIPT = r"""
const base = process.argv[1];
const R = await import(base + '/lib/range.js');
const T = await import(base + '/pages/topsql.js');
const H = await import(base + '/pages/health.js');

const WIN = { begin_ts: '2026-10-17 10:46:29', end_ts: '2026-10-17 11:06:30' };
const SPAN_T = { at: '20261018T124105Z', file: '20261018T124105Z.time.span.json', mode: 'span', window: WIN };
const CUM_T = { at: '20261018T120000Z', file: '20261018T120000Z.time.json' };
const DELTA_T = { at: '20261018T121000Z', file: '20261018T121000Z.avg.delta.json', mode: 'delta' };
const SPAN_H = { at: '20261018T124017Z', file: '20261018T124017Z.span.json', mode: 'span', window: WIN, overall: 2, counts: { 2: 2 } };
const SPAN_H0 = { at: '20261018T110000Z', file: '20261018T110000Z.span.json', mode: 'span', window: WIN, overall: 1, counts: { 1: 1 } };
const NOW_H = { at: '20261018T123000Z', file: '20261018T123000Z.json', overall: 0, counts: {} };

const serve = (map) => { globalThis.fetch = async (path) => {
  const k = Object.keys(map).find((p) => path.endsWith(p));
  return k ? { ok: true, status: 200, json: async () => map[k] } : { ok: false, status: 404 };
}; };
const yday = { from: '2026-10-17T10:46', to: '2026-10-17T11:06' };

// ① 时间段筛选按窗口
const r1 = {
  span_in_by_window: R.inRange('health', SPAN_H, yday),
  span_not_by_archive_time: R.inRange('topsql', SPAN_T, { from: '2026-10-18T20:30', to: '2026-10-18T20:50' }),
  plain_still_by_archive_time: R.inRange('topsql', CUM_T, { from: '2026-10-18T19:59', to: '2026-10-18T20:01' }),
  label_same_day: R.windowLabel(WIN),
  label_two_days: R.windowLabel({ begin_ts: '2026-10-17 23:50:00', end_ts: '2026-10-18 00:10:00' }),
  label_missing: R.windowLabel(null),
};

// ② Top SQL:页签只取累计榜;run= 钉住时段榜
const rep = { conn: 'lab/og5', by: 'time', limit: 10, mode: 'span', window: WIN, span_note: '你要看的是……', rows: [{ sql_id: '1', calls: 2, total_sec: 3, avg_ms: 1500, rows: 0, is_new: true, query: 'q' }] };
const cum = { conn: 'lab/og5', by: 'time', limit: 10, rows: [{ sql_id: '9', calls: 1, total_sec: 1, avg_ms: 1000, rows: 0, query: 'c' }] };
serve({ '/topsql/k/index.json': [CUM_T, DELTA_T, SPAN_T], [SPAN_T.file]: rep, [CUM_T.file]: cum });
const per = T.latestPerBy([CUM_T, DELTA_T, SPAN_T]);
const pinned = await T.load('k', 'avg', yday, SPAN_T.file);
const plain = await T.load('k', 'time', null, '');
const stale = await T.load('k', 'time', null, 'nope.json');
const r2 = {
  per_keys: Object.keys(per), per_time: per.time && per.time.file,
  pinned_ok: pinned.ok, pinned_file: pinned.entry && pinned.entry.file, pinned_by: pinned.by,
  pinned_span: pinned.span, pinned_win: pinned.win, pinned_first: pinned.rows[0].sql_id,
  plain_file: plain.entry && plain.entry.file, plain_span: plain.span,
  stale_falls_back: stale.entry && stale.entry.file,
};

// ③ 健康检查:同口径对比;latest 404 退回索引最新一份
serve({ '/health/k/index.json': [SPAN_H0, NOW_H, SPAN_H],
        [SPAN_H.file]: { conn: 'lab/og5', mode: 'span', window: WIN, overall: 2, findings: [], dims: [{ dimension: 'Window Load', available: true, headers: ['指标', '值'], rows: [['DB time', '23510.33 秒'], ['CPU time', '3501.10 秒'], ['CPU 占 DB time', '14.9%'], ['平均活跃会话', '19.176']] },
          { dimension: 'Connections', available: false, headline: '回看不了', note: '这段时间当时没有采集,回看不了' },
          { dimension: 'Window Top SQL', available: false, headline: '不可用', note: 'gaussdb-topsql 没取到' }] } });
const hm = await H.load('k', '', null);
const r3 = {
  ok: hm.ok, file: hm.entry && hm.entry.file, span: hm.span, win: hm.win,
  prev_same_mode: hm.prev && hm.prev.file, idx_len: hm.idx.length, all_len: hm.all.length,
  entry_label_span: H.entryLabel(SPAN_H), entry_label_now: H.entryLabel(NOW_H),
  title: H.title('Window Load'), fig: H.keyFig(hm.dims[0]),
  na: hm.dims.map((x) => x.na), naWord: hm.checks.map((c) => c.naWord || ''), legend: H.naLegend(hm.dims),
  legendOnlyLookback: H.naLegend(hm.dims.slice(0, 2)), legendNone: H.naLegend([]),
};
console.log(JSON.stringify({ r1, r2, r3 }));
"""


def test_span_reports_on_the_dashboard():
    env = {**os.environ, "TZ": "Asia/Shanghai"}
    out = subprocess.run(["node", "--input-type=module", "-e", _SCRIPT, "file://%s" % _FRONT],
                         capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    r1, r2, r3 = got["r1"], got["r2"], got["r3"]

    assert r1["span_in_by_window"] is True, "时段报告要按评估窗口筛"
    assert r1["span_not_by_archive_time"] is False, "时段报告不能按存档时间筛"
    assert r1["plain_still_by_archive_time"] is True
    assert r1["label_same_day"] == "10-17 10:46 – 11:06"
    assert r1["label_two_days"] == "10-17 23:50 – 10-18 00:10"
    assert r1["label_missing"] == ""

    assert r2["per_keys"] == ["time"] and r2["per_time"].endswith(".time.json"), "页签只取累计榜"
    assert r2["pinned_ok"] is True and r2["pinned_file"].endswith(".time.span.json")
    assert r2["pinned_by"] == "time", "钉住的榜单维度取自文件名,不取页签偏好"
    assert r2["pinned_span"] == "span" and r2["pinned_win"] == "10-17 10:46 – 11:06"
    assert r2["pinned_first"] == "1"
    assert r2["plain_file"].endswith(".time.json") and r2["plain_span"] == ""
    assert r2["stale_falls_back"].endswith(".time.json"), "链接里的文件不在索引里 → 退回累计榜"

    assert r3["ok"] is True and r3["file"].endswith("124017Z.span.json"), "latest 404 时退回索引最新一份"
    assert r3["span"] is True and r3["win"] == "10-17 10:46 – 11:06"
    assert r3["prev_same_mode"] == "20261018T110000Z.span.json", "上期对比只和同一种口径比"
    assert (r3["idx_len"], r3["all_len"]) == (2, 3)
    assert r3["entry_label_span"] == "时段 10-17 10:46 – 11:06"
    assert r3["entry_label_now"] == "10-18 20:30"
    assert r3["title"] == "时段负载"
    assert r3["fig"]["v"] == "19.18" and "14.9%" in r3["fig"]["cap"]
    # 回看不了 ≠ 采集失败:时段体检里瞬时维度回看不了是设计如此,真失败(headline「不可用」)仍按采集失败画
    assert r3["na"] == ["", "回看不了", ""] and r3["naWord"] == ["", "回看不了", ""]
    assert r3["legend"] == "回看不了 / 采集失败" and r3["legendOnlyLookback"] == "回看不了"
    assert r3["legendNone"] == "采集失败"
