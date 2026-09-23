// 健康检查大盘。数据:/reports/health/<key>/latest.json(一份 gaussdb-health 报告出全页)
// + index.json(最近 N 份的 overall 与 findings 计数,历史两卡用)。
// 数字全部来自技能脚本的确定性判定,这里只摆放,不改级别。

import { fetchLatest, fetchIndex } from '../lib/data.js';
import { esc, lv, lvn, stamp, cellTd } from '../lib/fmt.js';
import { ring, timeline, sparkline } from '../lib/charts.js';
import { bindDigLinks } from '../lib/dig.js';

// 维度名 = gaussdb-health/scripts/model.py 的 DIM_* 常量(契约守卫逐个核对,技能改名这里会先红)。
// 子技能的发现带它们自己的维度名(如 DB Time),不在这张表里就原样显示。
export const TITLES = {
  'Overview': '总览', 'Slow SQL': '慢 SQL', 'Long & Idle Transactions': '长事务与空闲事务',
  'Connections': '连接', 'Checkpoint / WAL / Archiving': '检查点 / WAL / 归档', 'Replication / Standby': '主备复制',
  'Schema / Objects': '对象与索引', 'Transactions / Concurrency': '事务并发', 'Wait Events': '等待事件',
  'Dead Tuples & Bloat': '死元组与膨胀', 'Lightweight Locks (LWLock)': '轻量锁', 'Transaction Locks & Blocking Chains': '锁与阻塞链',
};
const title = (dim) => TITLES[dim] || dim;

/** 一份报告的级别;所有维度都没采到时是 null —— 那不是「正常」,是「不知道」。 */
function entryLevel(e) {
  const dm = e && e.dims;
  if (dm && dm.total && dm.na === dm.total) return null;
  return e ? e.overall | 0 : null;
}
const levelText = (l) => (l === null ? '采集失败' : lvn(l));
const levelCls = (l) => (l === null ? 'na' : lv(l));
const SEV_COLOR = { crit: '#d64545', warn: '#e07a1f', notice: '#c9862d' };

function emptyCard(msg, prompt) {
  return `<div class="card"><div class="empty"><b>${esc(msg)}</b>
    <a class="dig" href="#" data-dig="${esc(prompt)}" data-dig-title="健康检查">在会话里跑一次健康检查 →</a></div></div>`;
}

function counts(entry) {
  const c = (entry && entry.counts) || {};
  return { notice: +c['1'] || 0, warn: +c['2'] || 0, crit: +c['3'] || 0 };
}

function dimTable(d) {
  const rows = (d.rows || []).slice(0, 3);
  // 不可用的维度:headline 已经是「不可用:<原因>」,note 是同一句话,不重复打
  if (!rows.length) return d.note && !(d.headline || '').includes(d.note) ? `<div style="font-size:12.5px;color:var(--dim)">${esc(d.note)}</div>` : '';
  const heads = d.headers || [];
  return `<div class="tw"><table><thead><tr>${heads.map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map(cellTd).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function findingRow(f) {
  const prompt = f.sql_id
    ? `请解释并优化 sql_id 为 ${f.sql_id} 的语句:健康检查发现 ${f.code},${f.metric}=${f.value}(阈值 ${f.threshold})。`
    : `请针对健康检查发现 ${f.code}(${f.metric}=${f.value},阈值 ${f.threshold})做深入分析:${f.evidence}`;
  return `<div class="verdict ${lv(f.severity)}"><i class="lv"></i>
    <div class="t"><b>${lvn(f.severity)}</b> · ${esc(title(f.dimension))} · ${esc(f.metric)} = <b>${esc(f.value)}</b>(阈值 ${esc(f.threshold)})
      <small>${esc(f.evidence)}</small></div>
    <a class="dig" href="#" data-dig="${esc(prompt)}">${f.sql_id ? '看执行计划 →' : '深挖 →'}</a></div>`;
}

export async function render(root, ctx) {
  if (!ctx.key) {
    root.innerHTML = `<div class="crumb">大盘 › <b>健康检查</b></div>` + emptyCard('还没有健康检查报告', '请对当前库做一次健康检查。');
    bindDigLinks(root);
    return;
  }
  const [latest, index] = await Promise.all([fetchLatest('health', ctx.key), fetchIndex('health', ctx.key)]);
  if (!latest.ok) {
    root.innerHTML = `<div class="crumb">大盘 › <b>健康检查</b></div>` + (latest.status === 404
      ? emptyCard('这个实例还没有健康检查报告', '请对当前库做一次健康检查。')
      : `<div class="card"><div class="empty"><b>报告读取失败</b>${esc(latest.error)}</div></div>`);
    bindDigLinks(root);
    return;
  }
  const d = latest.data;
  const idx = index.ok && Array.isArray(index.data) ? index.data : [];
  const findings = [...(d.findings || [])].sort((a, b) => (b.severity | 0) - (a.severity | 0));
  const worst = {};
  for (const f of findings) worst[f.dimension] = Math.max(worst[f.dimension] || 0, f.severity | 0);
  const dims = (d.dims || []).map((x) => ({ ...x, level: x.available === false ? null : (worst[x.dimension] || 0) }));
  const n = { warn: dims.filter((x) => x.level >= 2).length, notice: dims.filter((x) => x.level === 1).length,
              ok: dims.filter((x) => x.level === 0).length, na: dims.filter((x) => x.level === null).length };
  const last = idx[idx.length - 1], prev = idx[idx.length - 2];
  // 全部维度都没采到:overall 是 0 只因为没有发现,不能画成「正常」
  const allNa = dims.length > 0 && n.na === dims.length;
  const nowLevel = allNa ? null : d.overall | 0;
  const dimNames = new Set(dims.map((x) => x.dimension));
  const orphan = findings.filter((f) => !dimNames.has(f.dimension));
  const orphanTop = orphan.reduce((m, f) => Math.max(m, f.severity | 0), 0);
  const delta = prev ? (counts(last).warn + counts(last).crit) - (counts(prev).warn + counts(prev).crit) : null;
  const subs = d.sub_skills || [];

  root.innerHTML = `
  <div class="crumb">大盘 › <b>健康检查</b></div>
  <div class="head" id="hd-head">
    <div>
      <h1>健康检查 <span class="badge ${levelCls(nowLevel)}">${levelText(nowLevel)}</span>${!allNa && n.na ? ` <span class="tag">${n.na} 个维度没采到,结论不完整</span>` : ''}</h1>
      <div class="meta">目标 <b>${esc((ctx.target && ctx.target.label) || d.conn || '—')}</b> · 执行人 <b>${esc(ctx.whoami || '—')}</b> · 报告时间 <b>${esc(stamp(last && last.at))}</b></div>
    </div>
    <div class="right">${prev ? `上一次:${esc(stamp(prev.at))} · ${levelText(entryLevel(prev))}<br>比上次 <span class="${delta > 0 ? 'up' : delta < 0 ? 'down' : ''}">${delta > 0 ? '新增 ' + delta + ' 条告警' : delta < 0 ? '减少 ' + (-delta) + ' 条告警' : '告警数持平'}</span>` : '首次巡检'}</div>
  </div>

  <div class="grid g32">
    <div class="card">
      <h2>${dims.length} 个维度 · 一眼看全</h2>
      <div class="ring"><div id="hd-ring">${ring(dims.map((x) => ({ level: x.level })))}</div>
        <div>
          <div style="font-size:15px;font-weight:600;margin-bottom:6px">${n.warn} 个维度告警 · ${n.notice} 个关注 · ${n.ok} 个正常${n.na ? ` · ${n.na} 个不可用` : ''}</div>
          ${orphan.length ? `<div class="sub">另有 ${orphan.length} 条来自子技能的发现(${[...new Set(orphan.map((f) => title(f.dimension)))].map(esc).join('、')}),最高 ${lvn(orphanTop)},见下方发现列表</div>` : ''}
          <div class="band" id="hd-band">${dims.map((x) => `<span class="${x.level === null ? 'na' : lv(x.level)}">${esc(title(x.dimension))}</span>`).join('')}</div>
        </div></div>
    </div>
    <div class="card" id="hd-sub">
      <h2>子技能执行</h2>
      <div class="sub">health 汇总子技能,任一失败也出报告</div>
      ${subs.length ? subs.map((s) => `<div class="kv"><span>${esc(s.skill)}</span><span class="${s.ok ? 'ok' : 'crit'}">${s.ok ? '✓ 正常' : '✗ ' + esc(s.error || '失败')}</span></div>`).join('') : '<div style="font-size:12.5px;color:var(--dim)">本次未运行子技能</div>'}
    </div>
  </div>

  <div class="card">
    <h2>越过阈值的发现 <span class="tag">脚本按阈值判定 · 模型不改级别</span>
      <a class="dig" href="#" data-dig="请基于最近一次健康检查报告做一次深入分析,给出处理优先级。">在会话里深挖 →</a></h2>
    <div id="hd-findings">${findings.length ? findings.map(findingRow).join('')
      : allNa ? '<div style="color:var(--dim);padding:8px 0">所有维度都没采到数据,这份报告<b>不能说明库是健康的</b>。失败原因见各维度卡与子技能执行。</div>'
      : '<div style="color:var(--ok);padding:8px 0">没有越过阈值的发现</div>'}</div>
  </div>

  <div class="grid g3" id="hd-dims">${dims.map((x) => `<div class="card" style="${x.level === null ? 'opacity:.6' : ''}">
    <h2>${esc(title(x.dimension))} <span class="pill ${x.level === null ? 'dim' : lv(x.level)}">${x.level === null ? '不可用' : lvn(x.level)}</span>
      ${x.level === null ? '' : `<a class="dig" href="#" data-dig="${esc('请深入分析健康检查的「' + title(x.dimension) + '」维度:' + (x.headline || ''))}">深挖 →</a>`}</h2>
    <div class="sub">${esc(x.headline || '')}</div>${dimTable(x)}</div>`).join('')}</div>

  <div class="grid g2">
    <div class="card">
      <h2>最近 ${idx.length} 次巡检</h2>
      <div class="sub">每次的综合级别,最右是本次</div>
      <div id="hd-history">${idx.length ? timeline(idx.map(entryLevel)) : '<div class="loading">还没有历史</div>'}</div>
      <div class="tlx"><span>${esc(stamp(idx[0] && idx[0].at).slice(0, 5))}</span><span>${esc(stamp(last && last.at).slice(0, 5))}</span></div>
    </div>
    <div class="card">
      <h2>发现数趋势</h2>
      <div class="sub">按级别,最近 ${idx.length} 次</div>
      ${idx.length >= 2
        ? `<svg width="100%" height="70" viewBox="0 0 400 70" id="hd-trend">${['notice', 'warn', 'crit'].map((k) => sparkline(idx.map((e) => counts(e)[k]), SEV_COLOR[k])).join('')}</svg>`
        : `<div id="hd-trend" style="height:70px;display:flex;align-items:center;color:var(--dim);font-size:12.5px">只有 1 次巡检,再跑一次才有趋势。本次:严重 ${counts(last).crit} · 告警 ${counts(last).warn} · 关注 ${counts(last).notice}</div>`}
      <div class="legend"><span style="--c:${SEV_COLOR.crit}">严重</span><span style="--c:${SEV_COLOR.warn}">告警</span><span style="--c:${SEV_COLOR.notice}">关注</span></div>
    </div>
  </div>`;
  bindDigLinks(root);
}
