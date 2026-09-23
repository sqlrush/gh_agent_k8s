// WDR 分析大盘。数据:/reports/wdr/<key>/latest.json + index.json(上一份 = index 倒数第二条,
// 有则 KPI 带 ▲▼ 对比,没有就写「无对比窗口」,不造假)。
// 七个维度名与列名取自 gaussdb-wdr 的 model.py / collectors.py(见下方 DIMS 与 KPIS)。
// KPI 从 Load Profile / Database Stat 那一行按列名取值,AAS 与 TPS 用窗口时长算;取不到显示「未采集」。

import { fetchLatest, fetchIndex, fetchReport } from '../lib/data.js';
import { esc, lv, lvn, stamp, fmtInt, cellTd } from '../lib/fmt.js';
import { stack, legend, bar, timeline, PAL } from '../lib/charts.js';
import { bindDigLinks } from '../lib/dig.js';

// 维度名 = gaussdb-wdr/scripts/model.py 的 DIM_* 常量(契约守卫逐个核对,技能改名这里会先红)。
// **第一版对着自编的样例写,维度名和行列形状都与真技能不符,真数据上 KPI 全是「未采集」。**
export const DIMS = {
  loadprofile: 'Load Profile', dbstat: 'Database Stat', topsql: 'Top SQL', waits: 'Wait Events / Classes',
  checkpoint: 'Checkpoint / BgWriter / Redo', cache: 'Cache / Memory', fileio: 'File IO',
};
const TITLE = { checkpoint: 'Checkpoint / 刷脏 / Redo', cache: '缓存 / 物理读对象', fileio: '文件 IO' };
// Load Profile 与 Database Stat 各是一行、按列存值;列名是 collectors.py 里的 headers(前缀匹配,容忍单位后缀)。
// KPI 只放报告里真有来源的:没有来源的宁可不放,也不画一格「未采集」充数。
const KPIS = [
  { l: 'DB Time', dim: 'loadprofile', col: 'DB time', u: 's', worse: 'up' },
  { l: '平均活跃会话 AAS', calc: (d, secs) => ratio(val(d, 'loadprofile', 'DB time'), secs), u: '', worse: 'up' },
  { l: '事务 TPS', calc: (d, secs) => ratio(val(d, 'loadprofile', 'commits'), secs), u: '/s', worse: null },
  { l: 'CPU 占 DB Time', dim: 'loadprofile', col: 'CPU占DBtime', u: '%', worse: null },
  { l: '缓存命中', dim: 'dbstat', col: 'cache_hit', u: '%', worse: 'down' },
  { l: '物理读', dim: 'loadprofile', col: '物理读', u: '块', worse: 'up' },
  { l: '临时溢出', dim: 'dbstat', col: '临时溢出', u: 'MiB', worse: 'up' },
  { l: '回滚率', dim: 'dbstat', col: '回滚率', u: '%', worse: 'up' },
];
const WAIT_COLOR = { IO_EVENT: '#2fa79a', LWLOCK_EVENT: '#8b6be0', LOCK_EVENT: '#d64545', STATUS: '#9aa3ad' };

const dimOf = (d, k) => (d && d.dims || []).find((x) => x.dimension === DIMS[k]);
const num = (v) => { const n = parseFloat(String(v ?? '').replace(/,/g, '')); return Number.isFinite(n) ? n : null; };
const ratio = (a, secs) => (a === null || !secs ? null : a / secs);

/** 单行维度里按列名(前缀)取值。 */
export function val(d, k, colName) {
  const dim = dimOf(d, k);
  if (!dim || !dim.rows || !dim.rows.length) return null;
  const i = (dim.headers || []).findIndex((h) => String(h).startsWith(colName));
  return i < 0 ? null : num(dim.rows[0][i]);
}
const kpiVal = (k, d) => {
  if (!d) return null;
  const secs = (Number(d.window && d.window.duration_min) || 0) * 60;
  return k.calc ? k.calc(d, secs) : val(d, k.dim, k.col);
};
const fmtKpi = (v) => (Math.abs(v) >= 100 ? fmtInt(Math.round(v)) : String(+v.toFixed(v < 1 ? 3 : 1)));

// MiB 上千就换成 GiB:19763 MiB 显示成「2.0 万 MiB」没人读得懂
const unitOf = (v, u) => (u === 'MiB' && Math.abs(v) >= 1024 ? [v / 1024, 'GiB'] : [v, u]);
const show = (v, u) => { const [x, uu] = unitOf(v, u); return [fmtKpi(x), uu]; };

function kpiCard(k, cur, prev) {
  if (cur === null) return `<div class="kpi"><div class="l">${esc(k.l)}</div><div class="na">未采集</div></div>`;
  let d = '无对比窗口', cls = '', level = 'ok';
  if (prev !== null && prev !== undefined) {
    const ratio = prev ? cur / prev : null, diff = cur - prev;
    const arrow = diff > 0 ? '▲' : diff < 0 ? '▼' : '＝';
    const [pv, pu] = show(prev, k.u);
    // 上窗是 0 时不算百分比(除以 0 会得出「▲ 1976320%」),只说上窗是 0
    const change = !prev ? '' : ratio && Math.abs(ratio) >= 1.5 ? '×' + ratio.toFixed(1) + ' · '
      : (k.u === '%' ? Math.abs(diff).toFixed(1) + ' pt' : Math.abs(diff / prev * 100).toFixed(0) + '%') + ' · ';
    d = `${arrow} ${change}上窗 ${pv}${pu ? ' ' + pu : ''}`;
    const worse = k.worse === 'up' ? diff > 0 : k.worse === 'down' ? diff < 0 : false;
    if (worse && ratio && (ratio >= 3 || ratio <= 1 / 3)) { level = 'crit'; cls = 'up'; }
    else if (worse && Math.abs(diff) > 0) { level = 'warn'; cls = 'up'; }
    else if (!worse && diff !== 0 && k.worse) { cls = 'down'; }
  }
  const [cv, cu] = show(cur, k.u);
  return `<div class="kpi ${level}"><div class="l">${esc(k.l)}</div><div class="v tnum">${cv}<small>${esc(cu)}</small></div><div class="d"><span class="${cls}">${esc(d)}</span></div></div>`;
}

/** DB Time 构成:先 CPU 与非 CPU(Load Profile 的 CPU占DBtime%),再把等待时间按等待类分(Wait Events / Classes)。
 *  两个比例的分母不同 —— 前者是 DB Time,后者是等待总时间 —— 所以分两条画,不叠成一条。 */
function dbtBlock(d, waitParts) {
  const cpu = val(d, 'loadprofile', 'CPU占DBtime');
  if (cpu === null && !waitParts.length) return '<div style="color:var(--dim)">未采集</div>';
  const colorOf = (name, i) => WAIT_COLOR[name] || PAL[(i + 3) % PAL.length];
  let h = '';
  if (cpu !== null) {
    const c = Math.max(0, Math.min(100, cpu));
    h += `<div class="sub">占 DB Time</div>${stack([{ share: c, color: '#4176e6' }, { share: 100 - c, color: '#e07a1f' }])}
      ${legend([{ label: `CPU ${c.toFixed(1)}%`, color: '#4176e6' }, { label: `等待 / 锁 / IO / 睡眠 ${(100 - c).toFixed(1)}%`, color: '#e07a1f' }])}`;
  }
  if (waitParts.length) {
    h += `<div class="sub" style="margin-top:12px">等待时间按等待类</div>${stack(waitParts.map((x, i) => ({ share: x.share, color: colorOf(x.name, i) })))}
      ${legend(waitParts.map((x, i) => ({ label: `${x.name} ${x.share.toFixed(1)}%`, color: colorOf(x.name, i) })))}`;
  }
  return h;
}

function table(dim, cols, widthBar) {
  if (!dim || !dim.rows || !dim.rows.length) return `<div style="font-size:12.5px;color:var(--dim)">${esc(dim && dim.note || '未采集')}</div>`;
  const heads = dim.headers || [];
  const maxv = widthBar !== undefined ? Math.max(1, ...dim.rows.map((r) => num(r[widthBar]) || 0)) : 1;
  return `<div class="tw"><table><thead><tr>${heads.map((h) => `<th>${esc(h)}</th>`).join('')}${widthBar !== undefined ? '<th style="width:110px"></th>' : ''}</tr></thead><tbody>
    ${dim.rows.slice(0, cols).map((r, i) => `<tr>${r.map(cellTd).join('')}
      ${widthBar !== undefined ? `<td>${bar((num(r[widthBar]) || 0) / maxv * 100, i < 3 ? 'crit' : '')}</td>` : ''}</tr>`).join('')}</tbody></table></div>`;
}

export async function render(root, ctx) {
  const crumb = `<div class="crumb">大盘 › <b>WDR 分析</b></div>`;
  const empty = (msg) => `${crumb}<div class="card"><div class="empty"><b>${esc(msg)}</b>
    <a class="dig" href="#" data-dig="请列出最近的 WDR 快照,并对最近一个窗口做分析。" data-dig-title="WDR">在会话里跑一次 WDR 分析 →</a></div></div>`;
  if (!ctx.key) { root.innerHTML = empty('还没有 WDR 报告'); bindDigLinks(root); return; }
  const [latest, index] = await Promise.all([fetchLatest('wdr', ctx.key), fetchIndex('wdr', ctx.key)]);
  if (!latest.ok) { root.innerHTML = empty(latest.status === 404 ? '这个实例还没有 WDR 报告' : '报告读取失败:' + latest.error); bindDigLinks(root); return; }
  const d = latest.data;
  const idx = index.ok && Array.isArray(index.data) ? index.data : [];
  const w = d.window || {};
  if (w.wdr_enabled === false) {
    root.innerHTML = `${crumb}<div class="card"><div class="empty"><b>这个库没有开启 WDR</b>
      需要把 enable_wdr_snapshot 设为 on 并有快照权限;开启后在会话里说「做一次 WDR 分析」即可。
      <a class="dig" href="#" data-dig="请检查当前库的 WDR 快照是否开启,说明如何开启以及需要的权限。" data-dig-title="WDR">在会话里检查 →</a></div></div>`;
    bindDigLinks(root); return;
  }
  const prevEntry = idx.length >= 2 ? idx[idx.length - 2] : null;
  const prevRep = prevEntry ? await fetchReport('wdr', ctx.key, prevEntry.file) : null;
  const p = prevRep && prevRep.ok ? prevRep.data : null;

  const findings = [...(d.findings || [])].sort((a, b) => (b.severity | 0) - (a.severity | 0));
  const waits = dimOf(d, 'waits');
  const waitParts = (waits && waits.rows || []).map((r) => ({ name: String(r[0]), share: num(r[r.length - 1]) || 0 }));
  const native = d.native || {};
  // 只有原生报告在本实例的报告目录里才给下载链接:显式 --save-html 到 /tmp 的老报告,给链接就是 404
  const nativeName = native.saved_path && String(native.saved_path).includes(`/wdr/${ctx.key}/`) ? String(native.saved_path).split("/").pop() : "";
  const last = idx[idx.length - 1];

  root.innerHTML = `${crumb}
  <div class="head" id="wd-head">
    <div><h1>WDR 窗口 <span class="badge ${lv(d.overall)}">${lvn(d.overall)}</span></h1>
      <div class="meta">目标 <b>${esc((ctx.target && ctx.target.label) || d.conn || '')}</b> · 范围 <b>${esc(w.scope || '')}</b>${w.node ? ' · ' + esc(w.node) : ''} · 分析窗口 <b>snap ${esc(w.begin_id)} → ${esc(w.end_id)}</b> · ${esc(w.begin_ts || '')} → ${esc(w.end_ts || '')} · <b>${esc(w.duration_min)} 分钟</b></div>
      <div class="meta">${p ? `对比窗口 snap ${esc(p.window.begin_id)} → ${esc(p.window.end_id)} · ${esc(p.window.duration_min)} 分钟 <span class="tag">上一份报告</span>` : '<span class="tag">无对比窗口:第一份报告</span>'}</div></div>
    <div class="right">原生 WDR 报告 ${native.generated ? `<b class="ok">已生成</b> · ${fmtInt(Math.round((native.bytes || 0) / 1024))} KB${nativeName ? ` · <a class="dig" href="/reports/wdr/${encodeURIComponent(ctx.key)}/${encodeURIComponent(nativeName)}" target="_blank" rel="noopener">下载 HTML →</a>` : ''}` : `<span style="color:var(--dim)">未生成${native.note ? ' · ' + esc(native.note) : ''}</span>`}<br>执行人 ${esc(ctx.whoami || '—')} · ${esc(stamp(last && last.at))}</div>
  </div>
  <div class="grid g4" id="wd-kpis">${KPIS.map((k) => kpiCard(k, kpiVal(k, d), kpiVal(k, p))).join('')}</div>
  <div class="grid g32">
    <div class="card"><h2>DB Time 构成 <span class="tag">CPU / 等待</span>
        <a class="dig" href="#" data-dig="请分析最近一个 WDR 窗口的 DB Time 构成,说明是算力型还是等待型负载。" data-dig-title="WDR">在会话里深挖 →</a></h2>
      <div id="wd-dbt">${dbtBlock(d, waitParts)}</div></div>
    <div class="card soft" id="wd-verdict"><h2>一眼结论 <span class="tag">脚本按阈值判定</span></h2>
      ${findings.length ? findings.map((f) => `<div class="verdict ${lv(f.severity)}"><i class="lv"></i><div class="t"><b>${esc(f.metric)} ${esc(f.value)}</b>(阈值 ${esc(f.threshold)})<small>${esc(f.evidence)}</small></div>
        <a class="dig" href="#" data-dig="${esc('请针对 WDR 发现 ' + f.code + '(' + f.metric + '=' + f.value + ')做深入分析:' + f.evidence)}">深挖 →</a></div>`).join('') : '<div style="color:var(--ok);padding:8px 0">窗口内没有越过阈值的发现</div>'}</div>
  </div>
  <div class="grid g32">
    <div class="card"><h2>窗口内 Top SQL</h2><div class="sub">${esc(dimOf(d, "topsql") && dimOf(d, "topsql").headline || "")}</div><div id="wd-tsql">${table(dimOf(d, 'topsql'), 8, 2)}</div></div>
    <div class="card"><h2>等待事件</h2><div id="wd-waits">${table(waits, 8)}</div></div>
  </div>
  <div class="grid g3" id="wd-small">
    ${['checkpoint', 'cache', 'fileio'].map((n) => `<div class="card"><h2>${TITLE[n]}</h2>${table(dimOf(d, n), 6)}</div>`).join('')}
  </div>
  <div class="card"><h2>最近 ${idx.length} 个分析过的窗口</h2><div class="sub">每个窗口的态势,最右是本次</div>
    <div id="wd-history">${idx.length ? timeline(idx.map((e) => e.overall | 0)) : '<div class="loading">还没有历史</div>'}</div>
    <div class="tlx"><span>${esc(stamp(idx[0] && idx[0].at).slice(0, 5))}</span><span>${esc(stamp(last && last.at).slice(0, 5))}</span></div></div>`;
  bindDigLinks(root);
}
