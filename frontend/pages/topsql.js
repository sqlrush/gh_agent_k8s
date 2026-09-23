// Top SQL 大盘。数据:/reports/topsql/<key>/index.json 里按文件名后缀 .<by>.json 找每个维度最近一份,
// 再取那份;逐条卡按 sql_id 取 /reports/sqltune/<key>/<sql_id>.json(有则显示调优建议)。
// 报告只有 Top N 六个字段,没有全量合计 —— 页面上写「上榜合计」,不写「占总耗时」。

import { fetchIndex, fetchReport, fetchSqltune } from '../lib/data.js';
import { esc, fmtInt, fmtSec, fmtMs, stamp, brief, lv, lvn } from '../lib/fmt.js';
import { stack, legend, bar, PAL } from '../lib/charts.js';
import { bindDigLinks } from '../lib/dig.js';

const BY = [['time', '总耗时', 'total_sec'], ['avg', '平均耗时', 'avg_ms'], ['calls', '调用次数', 'calls'], ['reads', '逻辑读', 'total_sec'], ['rows', '返回行', 'rows']];
const BY_STORE = 'dash.topsql.by';

function readBy() { try { return localStorage.getItem(BY_STORE) || ''; } catch (_) { return ''; } }
function writeBy(b) { try { localStorage.setItem(BY_STORE, b); } catch (_) { /* 忽略 */ } }

/** index 条目 → 每个 by 最近一份的文件名。文件名形如 20260922T140300Z.avg.json。 */
export function latestPerBy(index) {
  const out = {};
  for (const e of index) {
    const m = /^\d{8}T\d{6}Z\.([a-z]+)\.json$/.exec(e.file || '');
    if (!m) continue;
    if (!out[m[1]] || (e.at || '') > (out[m[1]].at || '')) out[m[1]] = e;
  }
  return out;
}

function tunePrompt(r) { return `请调优 sql_id 为 ${r.sql_id} 的语句(调用 ${r.calls} 次,平均 ${fmtMs(r.avg_ms)}):${brief(r.query, 200)}`; }

// 取数脚本在库端把 SQL 截到 80 字(客户中间件白名单里注册的脚本,改它要客户重新注册):满 80 字就标出来
const QUERY_CAP = 80;
const fullQuery = (q) => (String(q ?? "").length >= QUERY_CAP ? `${q} …(取数时截到 ${QUERY_CAP} 字)` : String(q ?? ""));
// 调优技能按策略跳过的系统 SQL:存档里 skipped = "system"
const isSkipped = (t) => !!(t && t.skipped);

function tuneSummary(t) {
  if (isSkipped(t)) return `<div style="font-size:13px;color:var(--dim);margin-top:6px"><b>系统 SQL · 按策略不调优</b>:只引用了系统对象(${esc((t.system_objects || []).join("、"))}),这类慢多半是采集频率或系统压力,不是 SQL 本身的问题</div>`;
  // sqltune 的 findings 是 evidence.Finding.__dict__:kind / severity("warn"|"info") / detail / advice
  const fs = (t.evidence && t.evidence.findings) || [];
  const sev = (s) => (s === 'warn' ? 'warn' : s === 'info' ? 'notice' : lv(s));
  const line = (f) => `<div class="verdict ${sev(f.severity)}"><i class="lv"></i>
    <div class="t"><b>${esc(f.kind || f.code || '')}</b> ${esc(f.detail || '')}${f.advice ? `<small>建议:${esc(f.advice)}</small>` : ''}</div><span></span></div>`;
  const plan = t.evidence && t.evidence.plan ? `<pre class="sql" style="border-left:3px solid var(--ok)">${esc(String(t.evidence.plan).slice(0, 1200))}</pre>` : '';
  return `<div style="font-size:13px;margin-top:6px"><b class="ok">✓ 已调优</b> · ${fs.length} 条发现</div>${fs.slice(0, 3).map(line).join('')}${plan}`;
}

function card(r, i, tune) {
  return `<div class="card"><h2>S${i + 1} <span class="mono" style="font-weight:400;color:var(--dim)">${esc(r.sql_id)}</span>
      ${tune ? "" : `<a class="dig" href="#" data-dig="${esc(tunePrompt(r))}" data-dig-title="SQL 调优">调优 →</a>`}</h2>
    <pre class="sql">${esc(fullQuery(r.query))}</pre>
    <div class="grid g4" style="margin:0 0 8px">
      <div class="kpi"><div class="l">调用</div><div class="v tnum" style="font-size:17px">${fmtInt(r.calls)}</div></div>
      <div class="kpi"><div class="l">总耗时</div><div class="v tnum" style="font-size:17px">${fmtSec(r.total_sec)}</div></div>
      <div class="kpi ${r.avg_ms > 1000 ? 'warn' : ''}"><div class="l">平均</div><div class="v tnum" style="font-size:17px">${fmtMs(r.avg_ms)}</div></div>
      <div class="kpi"><div class="l">返回行</div><div class="v tnum" style="font-size:17px">${fmtInt(r.rows)}</div></div>
    </div>
    ${tune ? tuneSummary(tune) : `<div style="font-size:13px;color:var(--dim)">尚未调优 · <a class="dig" href="#" data-dig="${esc(tunePrompt(r))}" data-dig-title="SQL 调优">在会话里调优这条 →</a></div>`}</div>`;
}

export async function render(root, ctx) {
  const crumb = `<div class="crumb">大盘 › <b>Top SQL</b></div>`;
  const empty = (msg) => `${crumb}<div class="card"><div class="empty"><b>${esc(msg)}</b>
    <a class="dig" href="#" data-dig="请列出当前库的 Top SQL(按总耗时)。" data-dig-title="Top SQL">在会话里取一次 Top SQL →</a></div></div>`;
  if (!ctx.key) { root.innerHTML = empty('还没有 Top SQL 报告'); bindDigLinks(root); return; }
  const index = await fetchIndex('topsql', ctx.key);
  if (!index.ok) { root.innerHTML = empty(index.status === 404 ? '这个实例还没有 Top SQL 报告' : '报告读取失败:' + index.error); bindDigLinks(root); return; }
  const per = latestPerBy(index.data);
  const avail = BY.filter(([b]) => per[b]);
  if (!avail.length) { root.innerHTML = empty('这个实例还没有 Top SQL 报告'); bindDigLinks(root); return; }
  const saved = readBy();
  const by = avail.some(([b]) => b === saved) ? saved : avail[0][0];
  const rep = await fetchReport('topsql', ctx.key, per[by].file);
  if (!rep.ok) { root.innerHTML = empty('报告读取失败:' + rep.error); bindDigLinks(root); return; }
  const rows = rep.data.rows || [];
  const tunes = await Promise.all(rows.map((r) => fetchSqltune(ctx.key, r.sql_id).then((t) => (t.ok ? t.data : null))));

  const sumSec = rows.reduce((a, r) => a + (+r.total_sec || 0), 0) || 1;
  const sumCalls = rows.reduce((a, r) => a + (+r.calls || 0), 0);
  const share = (r) => (+r.total_sec || 0) / sumSec * 100;
  const top1 = rows[0] ? share(rows[0]) : 0;

  root.innerHTML = `${crumb}
  <div class="head">
    <div><h1>Top SQL ${rows[0] ? `<span class="badge ${top1 >= 40 ? 'warn' : 'ok'}">Top 1 占上榜 ${top1.toFixed(0)}%</span>` : ''}</h1>
      <div class="meta">目标 <b>${esc((ctx.target && ctx.target.label) || rep.data.conn || '')}</b> · 取数 <b>${esc(stamp(per[by].at))}</b> · 累计统计视图,不是时间窗口 · 含系统与监控 SQL(调优按策略跳过这类)</div></div>
    <div class="right">Top ${rows.length}<br><a class="dig" href="#" data-dig="请基于最近一次 Top SQL 结果分析哪些语句最值得优化,给出优先级。" data-dig-title="Top SQL">在会话里深挖 →</a></div>
  </div>
  <div class="tabs" id="ts-tabs">${BY.map(([b, label]) => `<span data-by="${b}" class="${b === by ? 'on' : ''}" style="${per[b] ? '' : 'opacity:.4;cursor:default'}">${label}</span>`).join('')}</div>
  <div class="grid g4" id="ts-kpis">
    <div class="kpi"><div class="l">上榜语句</div><div class="v tnum">${rows.length}<small>条</small></div><div class="d">按${esc(BY.find(([b]) => b === by)[1])}</div></div>
    <div class="kpi"><div class="l">上榜总调用</div><div class="v tnum">${fmtInt(sumCalls)}<small>次</small></div><div class="d">自视图重置</div></div>
    <div class="kpi"><div class="l">上榜总耗时</div><div class="v tnum">${fmtSec(sumSec)}</div><div class="d">${sumSec >= 60 ? `= ${fmtInt(Math.round(sumSec))} s` : "上榜语句合计"}</div></div>
    <div class="kpi ${top1 >= 40 ? 'warn' : ''}"><div class="l">Top 1 占上榜</div><div class="v tnum">${top1.toFixed(1)}<small>%</small></div><div class="d">${esc(brief(rows[0] ? rows[0].query : '', 40))}</div></div>
  </div>
  <div class="card"><h2>上榜合计内的耗时占比 <span class="tag">报告只有 Top N,没有全量合计</span></h2>
    <div id="ts-stack">${stack(rows.map((r, i) => ({ share: share(r), color: PAL[i % PAL.length] })))}</div>
    ${legend(rows.slice(0, 5).map((r, i) => ({ label: `S${i + 1} ${share(r).toFixed(1)}%`, color: PAL[i] })))}</div>
  <div class="card"><h2>榜单 <span class="tag">脚本按确定性口径排序 · 模型只写解读</span></h2>
    <div class="tw"><table><thead><tr><th>#</th><th>sql_id</th><th>语句摘要</th><th class="r">调用</th><th class="r">总耗时</th><th class="r">平均</th><th class="r">返回行</th><th style="width:140px">占上榜</th><th></th></tr></thead>
    <tbody id="ts-rows">${rows.map((r, i) => `<tr><td class="tnum" style="color:var(--dim)">S${i + 1}</td><td class="mono">${esc(r.sql_id)}</td>
      <td style="max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(brief(r.query, 90))}</td>
      <td class="r tnum">${fmtInt(r.calls)}</td><td class="r tnum">${fmtSec(r.total_sec)}</td><td class="r tnum">${fmtMs(r.avg_ms)}</td><td class="r tnum">${fmtInt(r.rows)}</td>
      <td>${bar(share(r), share(r) > 30 ? 'warn' : '')}</td>
      <td>${isSkipped(tunes[i]) ? `<span style="color:var(--dim);font-size:12px">系统 SQL</span>` : tunes[i] ? `<a href="#S${i + 1}" class="dig">看建议 ↓</a>` : `<a class="dig" href="#" data-dig="${esc(tunePrompt(r))}" data-dig-title="SQL 调优">调优 →</a>`}</td></tr>`).join('')}</tbody></table></div></div>
  <div class="grid g2" id="ts-cards">${rows.slice(0, 4).map((r, i) => `<div id="S${i + 1}">${card(r, i, tunes[i])}</div>`).join('')}</div>`;

  root.querySelectorAll('#ts-tabs [data-by]').forEach((el) => {
    if (!per[el.dataset.by]) return;
    el.addEventListener('click', () => { writeBy(el.dataset.by); render(root, ctx); });
  });
  bindDigLinks(root);
}
