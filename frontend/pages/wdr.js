// WDR 分析大盘。数据:/reports/wdr/<key>/latest.json + index.json(上一份 = index 倒数第二条,
// 有则 KPI 带 ▲▼ 对比,没有就写「无对比窗口」,不造假)。
// 七个维度 = 技能真实维度:loadprofile / dbstat / waits / topsql / checkpoint / cache / fileio。
// KPI 从 loadprofile / dbstat 的 rows 按第一列的行名取值;找不到的行显示「未采集」。

import { fetchLatest, fetchIndex, fetchReport } from '../lib/data.js';
import { esc, lv, lvn, stamp, fmtInt } from '../lib/fmt.js';
import { stack, legend, bar, timeline } from '../lib/charts.js';
import { bindDigLinks } from '../lib/dig.js';

const DIM = { loadprofile: '负载概况', dbstat: '库统计', waits: '等待', topsql: 'Top SQL', checkpoint: 'Checkpoint', cache: '缓存', fileio: '文件 IO' };
// KPI:标签、在哪个维度、行名匹配(前缀,不区分单位后缀)、单位、越大越差?
const KPIS = [
  { l: 'DB Time', dim: 'loadprofile', row: 'DB Time', u: 's', worse: 'up' },
  { l: '平均活跃会话 AAS', dim: 'loadprofile', row: 'AAS', u: '', worse: 'up' },
  { l: '事务 TPS', dim: 'loadprofile', row: 'TPS', u: '/s', worse: null },
  { l: '缓存命中', dim: 'dbstat', row: '缓存命中', u: '%', worse: 'down' },
  { l: '物理读', dim: 'loadprofile', row: '物理读', u: '块/s', worse: 'up' },
  { l: '临时文件', dim: 'loadprofile', row: '临时文件', u: 'GB', worse: 'up' },
  { l: 'WAL 写', dim: 'loadprofile', row: 'WAL', u: 'KB/s', worse: 'up' },
  { l: '回滚率', dim: 'dbstat', row: '回滚率', u: '%', worse: 'up' },
];
const WAIT_COLOR = { CPU: '#4176e6', IO: '#2fa79a', 网络: '#9aa3ad', 其他等待: '#e07a1f' };

const dimOf = (d, name) => (d.dims || []).find((x) => x.dimension === name);
const num = (v) => { const n = parseFloat(String(v ?? '').replace(/,/g, '')); return Number.isFinite(n) ? n : null; };

/** 在维度的 rows 里按第一列行名(前缀匹配)取第二列的值。 */
export function pick(dim, rowName) {
  if (!dim || !dim.rows) return null;
  const r = dim.rows.find((row) => String(row[0] || '').startsWith(rowName));
  return r ? num(r[1]) : null;
}

function kpiCard(k, cur, prev) {
  if (cur === null) return `<div class="kpi"><div class="l">${esc(k.l)}</div><div class="na">未采集</div></div>`;
  let d = '无对比窗口', cls = '', level = 'ok';
  if (prev !== null && prev !== undefined) {
    const ratio = prev ? cur / prev : null, diff = cur - prev;
    const arrow = diff > 0 ? '▲' : diff < 0 ? '▼' : '＝';
    d = `${arrow} ${ratio && Math.abs(ratio) >= 1.5 ? '×' + ratio.toFixed(1) : (k.u === '%' ? Math.abs(diff).toFixed(1) + ' pt' : Math.abs(diff / (prev || 1) * 100).toFixed(0) + '%')} · 上窗 ${fmtInt(prev)}${k.u ? ' ' + k.u : ''}`;
    const worse = k.worse === 'up' ? diff > 0 : k.worse === 'down' ? diff < 0 : false;
    if (worse && ratio && (ratio >= 3 || ratio <= 1 / 3)) { level = 'crit'; cls = 'up'; }
    else if (worse && Math.abs(diff) > 0) { level = 'warn'; cls = 'up'; }
    else if (!worse && diff !== 0 && k.worse) { cls = 'down'; }
  }
  return `<div class="kpi ${level}"><div class="l">${esc(k.l)}</div><div class="v tnum">${fmtInt(cur)}<small>${esc(k.u)}</small></div><div class="d"><span class="${cls}">${esc(d)}</span></div></div>`;
}

function table(dim, cols, widthBar) {
  if (!dim || !dim.rows || !dim.rows.length) return `<div style="font-size:12.5px;color:var(--dim)">${esc(dim && dim.note || '未采集')}</div>`;
  const heads = dim.headers || [];
  const maxv = widthBar !== undefined ? Math.max(1, ...dim.rows.map((r) => num(r[widthBar]) || 0)) : 1;
  return `<table><thead><tr>${heads.map((h) => `<th>${esc(h)}</th>`).join('')}${widthBar !== undefined ? '<th style="width:110px"></th>' : ''}</tr></thead><tbody>
    ${dim.rows.slice(0, cols).map((r, i) => `<tr>${r.map((c, j) => `<td class="${j ? 'r tnum' : ''}">${esc(c)}</td>`).join('')}
      ${widthBar !== undefined ? `<td>${bar((num(r[widthBar]) || 0) / maxv * 100, i < 3 ? 'crit' : '')}</td>` : ''}</tr>`).join('')}</tbody></table>`;
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
  const nativeName = native.saved_path ? String(native.saved_path).split('/').pop() : '';
  const last = idx[idx.length - 1];

  root.innerHTML = `${crumb}
  <div class="head" id="wd-head">
    <div><h1>WDR 窗口 <span class="badge ${lv(d.overall)}">${lvn(d.overall)}</span></h1>
      <div class="meta">目标 <b>${esc(d.conn || '')}</b> · 范围 <b>${esc(w.scope || '')}</b>${w.node ? ' · ' + esc(w.node) : ''} · 分析窗口 <b>snap ${esc(w.begin_id)} → ${esc(w.end_id)}</b> · ${esc(w.begin_ts || '')} → ${esc(w.end_ts || '')} · <b>${esc(w.duration_min)} 分钟</b></div>
      <div class="meta">${p ? `对比窗口 snap ${esc(p.window.begin_id)} → ${esc(p.window.end_id)} · ${esc(p.window.duration_min)} 分钟 <span class="tag">上一份报告</span>` : '<span class="tag">无对比窗口:第一份报告</span>'}</div></div>
    <div class="right">原生 WDR 报告 ${native.generated ? `<b class="ok">已生成</b> · ${fmtInt(Math.round((native.bytes || 0) / 1024))} KB${nativeName ? ` · <a class="dig" href="/reports/wdr/${encodeURIComponent(ctx.key)}/${encodeURIComponent(nativeName)}" target="_blank" rel="noopener">下载 HTML →</a>` : ''}` : `<span style="color:var(--dim)">未生成${native.note ? ' · ' + esc(native.note) : ''}</span>`}<br>执行人 ${esc(ctx.whoami || '—')} · ${esc(stamp(last && last.at))}</div>
  </div>
  <div class="grid g4" id="wd-kpis">${KPIS.map((k) => kpiCard(k, pick(dimOf(d, k.dim), k.row), p ? pick(dimOf(p, k.dim), k.row) : null)).join('')}</div>
  <div class="grid g32">
    <div class="card"><h2>DB Time 构成 <span class="tag">按等待类分解</span>
        <a class="dig" href="#" data-dig="请分析最近一个 WDR 窗口的 DB Time 构成,说明是算力型还是等待型负载。" data-dig-title="WDR">在会话里深挖 →</a></h2>
      <div id="wd-dbt">${waitParts.length ? stack(waitParts.map((x) => ({ share: x.share, color: WAIT_COLOR[x.name] || '#c9862d' }))) + legend(waitParts.map((x) => ({ label: `${x.name} ${x.share.toFixed(1)}%`, color: WAIT_COLOR[x.name] || '#c9862d' }))) : '<div style="color:var(--dim)">未采集</div>'}</div></div>
    <div class="card soft" id="wd-verdict"><h2>一眼结论 <span class="tag">脚本按阈值判定</span></h2>
      ${findings.length ? findings.map((f) => `<div class="verdict ${lv(f.severity)}"><i class="lv"></i><div class="t"><b>${esc(f.metric)} ${esc(f.value)}</b>(阈值 ${esc(f.threshold)})<small>${esc(f.evidence)}</small></div>
        <a class="dig" href="#" data-dig="${esc('请针对 WDR 发现 ' + f.code + '(' + f.metric + '=' + f.value + ')做深入分析:' + f.evidence)}">深挖 →</a></div>`).join('') : '<div style="color:var(--ok);padding:8px 0">窗口内没有越过阈值的发现</div>'}</div>
  </div>
  <div class="grid g32">
    <div class="card"><h2>窗口内 Top SQL <span class="tag">${esc(dimOf(d, 'topsql') && dimOf(d, 'topsql').headline || '')}</span></h2><div id="wd-tsql">${table(dimOf(d, 'topsql'), 8, 1)}</div></div>
    <div class="card"><h2>等待事件</h2><div id="wd-waits">${table(waits, 8)}</div></div>
  </div>
  <div class="grid g3" id="wd-small">
    ${['checkpoint', 'cache', 'fileio'].map((n) => `<div class="card"><h2>${DIM[n]}</h2>${table(dimOf(d, n), 6)}</div>`).join('')}
  </div>
  <div class="card"><h2>最近 ${idx.length} 个分析过的窗口</h2><div class="sub">每个窗口的态势,最右是本次</div>
    <div id="wd-history">${idx.length ? timeline(idx.map((e) => e.overall | 0)) : '<div class="loading">还没有历史</div>'}</div>
    <div class="tlx"><span>${esc(stamp(idx[0] && idx[0].at).slice(0, 5))}</span><span>${esc(stamp(last && last.at).slice(0, 5))}</span></div></div>`;
  bindDigLinks(root);
}
