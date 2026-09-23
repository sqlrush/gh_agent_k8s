// 知识库大盘。全体共享一份知识库,不分实例。数据:/reports/kb/health.json(gaussdb-kb health --json)
// + /reports/kb/queries.jsonl(本人的检索日志,最近 7 天)。

import { fetchKbHealth, fetchKbQueries } from '../lib/data.js';
import { esc, fmtInt, stamp } from '../lib/fmt.js';
import { bindDigLinks } from '../lib/dig.js';

const C = { case: '#2fa79a', rule: '#4176e6', graph: '#8b6be0' };

function pctOf(text) {              // "DataVec(覆盖 93%)" → 93;没有数字 → null
  const m = /(\d+)%/.exec(String(text || ''));
  return m ? +m[1] : null;
}

function storeCard(color, title, sub, big, unit, kvs, foot) {
  return `<div class="card" style="border-top:4px solid ${color};padding-top:12px">
    <h2><span style="width:10px;height:10px;border-radius:3px;background:${color}"></span>${esc(title)}</h2>
    <div class="sub">${esc(sub)}</div>
    <div style="font-size:30px;font-weight:700;line-height:1.1;margin:6px 0 10px" class="tnum">${esc(big)}<small style="font-size:12.5px;font-weight:500;color:var(--dim);margin-left:5px">${esc(unit)}</small></div>
    ${kvs.map(([k, v, cls]) => `<div class="kv"><span>${esc(k)}</span><span class="${cls || ''}">${v}</span></div>`).join('')}
    ${foot ? `<div style="margin-top:10px">${foot}</div>` : ''}</div>`;
}

export async function render(root) {
  const crumb = `<div class="crumb">大盘 › <b>知识库</b></div>`;
  const [h, q] = await Promise.all([fetchKbHealth(), fetchKbQueries()]);
  if (!h.ok) {
    root.innerHTML = `${crumb}<div class="card"><div class="empty"><b>${h.status === 404 ? '还没有知识库状态报告' : '报告读取失败:' + esc(h.error)}</b>
      <a class="dig" href="#" data-dig="请查看知识库的健康状态。" data-dig-title="知识库">在会话里查一次知识库状态 →</a></div></div>`;
    bindDigLinks(root); return;
  }
  const d = h.data, st = d.status || {}, c = st.counts || {}, ix = d.index_state || {};
  if (st.attached === false) {
    root.innerHTML = `${crumb}<div class="head"><div><h1>知识库 <span class="badge crit">未接入</span></h1></div></div>
      <div class="card"><div class="empty"><b>知识库未接入</b>${esc(st.reason || '')}<br><span style="font-size:12.5px">收件目录:${esc(d.inbox || '')}</span></div></div>`;
    return;
  }
  const cases = +c['docs.case'] || 0, rules = +c['docs.rule'] || 0, raws = +c['docs.raw'] || 0;
  const cov = pctOf(st.vector);
  const pending = d.pending || [], warns = d.file_warnings || [], misses = d.misses || [];
  const nIssues = pending.length + warns.length + (cov !== null && cov < 100 ? 1 : 0);
  // 关系图:配了图库看 index_state 的边数;没配(graph=none,走图文件)时边数只写在 status.graph 那句话里
  const gEdges = ix.graph && ix.graph !== "none" ? (+ix.edges_confirmed || 0) : +((/(\d+)\s*条已确认边/.exec(st.graph || "") || [])[1] ?? NaN);
  const gKind = !st.graph ? "未配置" : /neo4j|图库/i.test(st.graph) && !/未配置/.test(st.graph) ? "图库" : "图文件";
  const since = Date.now() - 7 * 86400e3;
  const queries = (q.ok ? q.data : []).filter((r) => new Date(r.at).getTime() >= since).sort((a, b) => (b.at > a.at ? 1 : -1)).slice(0, 20);

  root.innerHTML = `${crumb}
  <div class="head" id="kb-head">
    <div><h1>知识库 <span class="badge ${nIssues ? 'notice' : 'ok'}">${nIssues ? nIssues + ' 项待处理' : '健康'}</span></h1>
      <div class="meta">模式 <b>${esc(st.mode || '—')}</b>${st.reason ? `(${esc(st.reason)})` : ''}${st.version ? ` · 版本 <b>${esc(st.version)}</b>` : ''} · 本环境 <b>${d.readonly ? '只读' : '可写'}</b>${ix.indexed_at ? ` · 上次索引 <b>${esc(ix.indexed_at)}</b>` : ''}</div></div>
    <div class="right">✓ 引用必有出处 · 出处指回原件<br>全体共享 · 导入由知识库管理员在导入环境完成</div>
  </div>
  <div class="grid g4" id="kb-top">
    <div class="kpi"><div class="l">知识总量</div><div class="v tnum">${fmtInt(cases + rules)}<small>条</small></div><div class="d">案例 ${fmtInt(cases)} · 条款 ${fmtInt(rules)} · 原始工单 ${fmtInt(raws)}</div></div>
    <div class="kpi"><div class="l">向量</div><div class="v" style="font-size:18px">${esc(st.vector || '未启用')}</div><div class="d">${cov === null ? '文件模式只有关键词检索' : '覆盖 ' + cov + '%'}</div></div>
    <div class="kpi ${nIssues ? 'notice' : 'ok'}"><div class="l">健康度</div><div class="v">${nIssues ? nIssues + ' 项待处理' : '正常'}</div><div class="d">待处理 ${pending.length} · 坏文件 ${warns.length}</div></div>
    <div class="kpi"><div class="l">图</div><div class="v" style="font-size:18px">${esc(gKind)}</div><div class="d" title="${esc(st.graph || '')}">${Number.isFinite(gEdges) ? fmtInt(gEdges) + ' 条已确认边' : '—'}${gKind === '图文件' ? ' · 未配图库,无损失' : ''}</div></div>
  </div>
  <div class="grid g3" id="kb-stores">
    ${storeCard(C.case, '案例', '现场处理过的故障 / 工单:现象 → 根因 → 处置', fmtInt(cases), `条 · 原始工单 ${fmtInt(raws)}`,
      [['向量覆盖', cov === null ? '—' : cov + '%', cov !== null && cov < 100 ? 'notice' : 'ok'], ['检索方式', cov === null ? '关键词' : '语义 + 关键词']],
      `<a class="dig" href="#" data-dig="请列出知识库里最近新增的案例。" data-dig-title="知识库">在会话里查案例 →</a>`)}
    ${storeCard(C.rule, '条款', '规范 / 标准 / 制度里可引用的条文', fmtInt(rules), '条',
      [['引用可校验', 'cite-check', 'ok'], ['最近导入', esc(ix.indexed_at || '—')]],
      `<a class="dig" href="#" data-dig="请列出知识库里的条款来源文档。" data-dig-title="知识库">在会话里查条款 →</a>`)}
    ${storeCard(C.graph, '关系图', '现象 / 根因 / 处置 / 条款 / 对象 之间的边', Number.isFinite(gEdges) ? fmtInt(gEdges) : "—", "条已确认边",
      [['存储', esc(st.graph || '未配置')]],
      `<a class="dig" href="#" data-dig="请查一个现象在知识库关系图里的根因和处置路径。" data-dig-title="知识库">在会话里查图谱 →</a>`)}
  </div>
  <div class="grid g2">
    <div class="card" id="kb-health"><h2>健康自检 <span class="tag">降级即发现</span></h2>
      ${pending.map((s) => `<div class="verdict notice"><i class="lv"></i><div class="t"><b>待处理</b><small>${esc(s)}</small></div><span></span></div>`).join('')}
      ${warns.map((s) => `<div class="verdict warn"><i class="lv"></i><div class="t"><b>坏文件</b><small>${esc(s)}</small></div><span></span></div>`).join('')}
      ${cov !== null && cov < 100 ? `<div class="verdict notice"><i class="lv"></i><div class="t"><b>向量覆盖 ${cov}%</b><small>未覆盖的只能靠关键词命中</small></div><span></span></div>` : ''}
      ${!pending.length && !warns.length && (cov === null || cov >= 100) ? '<div class="verdict ok"><i class="lv"></i><div class="t"><b>没有待处理项</b></div><span></span></div>' : ''}</div>
    <div class="card" id="kb-misses"><h2>缺口清单 <span class="tag">查不到条款/案例的发现</span></h2>
      <div class="sub">诊断发现码或检索词在知识库里查不到对应案例/条款的次数 —— 最值得补的知识(全体共享)</div>
      ${misses.length ? misses.map((m, i) => `<div class="rank"><span class="n">${i + 1}</span><span>${String(m.code).startsWith("q:") ? `检索无命中 · 「${esc(String(m.code).slice(2))}」` : esc(m.code)}</span><span class="tnum">${esc(m.n)} 次</span></div>`).join('') : '<div style="color:var(--dim)">无记录</div>'}</div>
  </div>
  <div class="card" id="kb-queries"><h2>最近检索 <span class="tag">本人 · 近 7 天</span></h2>
    ${queries.length ? `<div class="tw"><table><thead><tr><th>时间</th><th>查询</th><th class="r">命中案例</th><th class="r">命中条款</th><th>怎么命中的</th></tr></thead><tbody>
      ${queries.map((r) => `<tr><td class="tnum">${esc(stamp(r.at))}</td><td>${esc(r.q)}</td><td class="r tnum ${r.hits_cases || r.hits_rules ? '' : 'crit'}">${esc(r.hits_cases)}</td><td class="r tnum ${r.hits_cases || r.hits_rules ? '' : 'crit'}">${esc(r.hits_rules)}</td>
        <td>${r.how === 'semantic' ? '<span class="pill ok">语义</span>' : '<span class="pill dim">关键词</span>'}${!r.hits_cases && !r.hits_rules ? ' · 未命中' : ''}</td></tr>`).join('')}</tbody></table></div>`
      : '<div style="color:var(--dim)">近 7 天没有检索记录</div>'}</div>`;
  bindDigLinks(root);
}
