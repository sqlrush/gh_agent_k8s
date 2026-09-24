// 知识库大盘。全体共享一份知识库,不分实例。数据:/reports/kb/health.json(gaussdb-kb health --json)
// + /reports/kb/queries.jsonl(本人的检索日志,最近 7 天)。

import { fetchKbHealth, fetchKbQueries, fetchKbCatalog } from '../lib/data.js';
import { casesView, rulesView, allRules, SEV } from '../lib/kb-docs.js';
import { graphView } from '../lib/kb-graph.js';
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

// 总览页签:就是原来整页的内容(知识库状态报告 + 本人检索)。返回 HTML,由 render 装进外壳。
function overview(h, q, cat) {
  if (!h.ok) {
    return `${catalogCards(cat)}<div class="card"><div class="empty"><b>${h.status === 404 ? '还没有知识库状态报告' : '报告读取失败:' + esc(h.error)}</b>
      <a class="dig" href="#" data-dig="请查看知识库的健康状态。" data-dig-title="知识库">在会话里查一次知识库状态 →</a></div></div>`;
  }
  const d = h.data, st = d.status || {}, c = st.counts || {}, ix = d.index_state || {};
  if (st.attached === false) {
    return `<div class="head"><div><h1>知识库 <span class="badge crit">未接入</span></h1></div></div>
      <div class="card"><div class="empty"><b>知识库未接入</b>${esc(st.reason || '')}<br><span style="font-size:12.5px">收件目录:${esc(d.inbox || '')}</span></div></div>`;;
  }
  const cases = +c['docs.case'] || 0, rules = +c['docs.rule'] || 0, raws = +c['docs.raw'] || 0;
  const cov = pctOf(st.vector);
  const pending = d.pending || [], warns = d.file_warnings || [], misses = d.misses || [];
  const nIssues = pending.length + warns.length + (cov !== null && cov < 100 ? 1 : 0);
  // 关系图:配了图库看 index_state 的边数;没配(graph=none,走图文件)时边数只写在 status.graph 那句话里
  // 关系图边数:优先用知识库目录里实际读到的已确认边;没有目录时退回状态报告(配了图库看 index_state,没配时只写在 status.graph 那句话里)
  const gEdges = cat && cat.attached ? (cat.edges || []).length : ix.graph && ix.graph !== "none" ? (+ix.edges_confirmed || 0) : +((/(\d+)\s*条已确认边/.exec(st.graph || "") || [])[1] ?? NaN);
  const gKind = !st.graph ? "未配置" : /neo4j|图库/i.test(st.graph) && !/未配置/.test(st.graph) ? "图库" : "图文件";
  const since = Date.now() - 7 * 86400e3;
  const queries = (q.ok ? q.data : []).filter((r) => new Date(r.at).getTime() >= since).sort((a, b) => (b.at > a.at ? 1 : -1)).slice(0, 20);

  return `
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
      `<a class="dig" href="#" data-tab="cases">看全部案例 →</a>`)}
    ${storeCard(C.rule, '条款', '规范 / 标准 / 制度里可引用的条文', fmtInt(rules), '条',
      [['引用可校验', 'cite-check', 'ok'], ['最近导入', esc(ix.indexed_at || '—')]],
      `<a class="dig" href="#" data-tab="rules">看全部条款 →</a>`)}
    ${storeCard(C.graph, '关系图', '现象 / 根因 / 处置 / 条款 / 对象 之间的边', Number.isFinite(gEdges) ? fmtInt(gEdges) : "—", "条已确认边",
      [['存储', esc(st.graph || '未配置')]],
      `<a class="dig" href="#" data-tab="graph">打开关系图 →</a>`)}
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
}

// ---------------------------------------------------------------- 外壳:四个页签 + 事件委托
// 页签记在地址的 # 上(#cases / #rules / #graph),刷新与后退都停在原处;选中项记在 # 的参数里。
const TABS = [['overview', '总览'], ['cases', '案例'], ['rules', '条款'], ['graph', '关系图']];
const S = { tab: 'overview', caseId: '', ruleId: '', node: '', q: '', sys: '' };

function readHash() {
  const [tab, arg] = decodeURIComponent(location.hash.replace(/^#/, '')).split('=');
  S.tab = TABS.some(([k]) => k === tab) ? tab : 'overview';
  if (arg && S.tab === 'cases') S.caseId = arg;
  if (arg && S.tab === 'rules') S.ruleId = arg;
  if (S.tab === 'graph') S.node = arg || '';
}
function writeHash() {
  const arg = S.tab === 'cases' ? S.caseId : S.tab === 'rules' ? S.ruleId : S.tab === 'graph' ? S.node : '';
  const h = '#' + S.tab + (arg ? '=' + encodeURIComponent(arg) : '');
  if (location.hash !== h) history.replaceState(null, '', h);
}

// 没有状态报告(没人跑过知识库自检)时,总览至少给出目录里的三个数
function catalogCards(cat) {
  if (!cat || !cat.attached) return '';
  const n = [(cat.cases || []).length, allRules(cat).length, (cat.edges || []).length];
  return `<div class="grid g3">${[['#2fa79a', '案例', n[0], '条', 'cases', '看全部案例 →'], ['#4176e6', '条款', n[1], '条现行', 'rules', '看全部条款 →'],
    ['#8b6be0', '关系图', n[2], '条已确认边', 'graph', '打开关系图 →']].map(([c, t, v, u, k, a]) =>
    `<div class="card" style="border-top:4px solid ${c}"><h2>${t}</h2><div style="font-size:30px;font-weight:700;margin:6px 0 4px">${fmtInt(v)}<small style="font-size:12.5px;color:var(--dim);margin-left:5px">${u}</small></div>
      <a class="dig" href="#" data-tab="${k}">${a}</a></div>`).join('')}</div>`;
}

function recentCases(cat) {
  const cs = [...((cat && cat.cases) || [])].sort((a, b) => String(b.occurred_at).localeCompare(String(a.occurred_at))).slice(0, 5);
  return cs.length ? `<div class="card"><h2>最近的案例 <span class="tag">按发生时间</span></h2>${cs.map((c) =>
    `<a class="kb-ref" data-case="${esc(c.id)}"><span class="pill ${SEV[c.severity] || 'dim'}">${esc(c.severity || '—')}</span> ${esc(c.title)}
      <span style="color:var(--dim);font-size:12px;margin-left:6px">${esc(c.system)} · ${esc(c.occurred_at)}</span></a>`).join('')}</div>` : '';
}

export async function render(root) {
  readHash();
  const [h, q, c] = await Promise.all([fetchKbHealth(), fetchKbQueries(), fetchKbCatalog()]);
  const cat = c.ok ? c.data : null;
  const counts = cat && cat.attached ? { cases: (cat.cases || []).length, rules: allRules(cat).length, graph: (cat.edges || []).length } : {};
  const draw = () => {
    writeHash();
    let body;
    if (S.tab !== 'overview' && !(cat && cat.attached)) {
      body = `<div class="card"><div class="empty"><b>读不到知识库目录</b>${esc(cat ? cat.reason || '' : (c.status === 404 ? '当前镜像版本不提供知识库目录' : c.error))}</div></div>`;
    } else if (S.tab === 'cases') body = casesView(cat, S);
    else if (S.tab === 'rules') body = rulesView(cat, S);
    else if (S.tab === 'graph') body = graphView(cat, S);
    else {
      const ov = overview(h, q, cat);
      // 总览 = 原来的整页;在它的三张卡与健康自检之间插入「最近的案例」
      body = h.ok ? ov.replace('<div class="grid g2">', recentCases(cat) + '<div class="grid g2">') : ov + recentCases(cat);
    }
    const nav = `<div class="kb-subnav" id="kb-tabs">${TABS.map(([k, l]) =>
      `<a href="#" data-tab="${k}" class="${S.tab === k ? 'on' : ''}">${l}${counts[k] !== undefined ? `<small>${counts[k]}</small>` : ''}</a>`).join('')}</div>`;
    const head = S.tab === 'overview' ? '' : `<div class="head"><div><h1>知识库</h1>
      <div class="meta">全体共享一份 · 只读 · 目录生成于 <b>${esc(cat && cat.built_at || '—')}</b>${cat && cat.warnings ? ` · <span class="warn">${cat.warnings} 个文件解析有问题,未列出</span>` : ''}</div></div>
      <div class="right">✓ 每条都能指回原件<br>导入由知识库管理员在导入环境完成</div></div>`;
    root.innerHTML = `<div class="crumb">大盘 › <b>知识库</b></div>${head}${S.tab === 'overview' ? nav.replace('kb-subnav', 'kb-subnav kb-subnav-top') : nav}${body}`;
    if (S.tab === 'overview') {                   // 总览:页签条放在原页头下面
      const hd = root.querySelector('#kb-head'), tabs = root.querySelector('#kb-tabs');
      if (hd && tabs) hd.after(tabs);
    }
    bindDigLinks(root);
  };
  const go = (patch) => { Object.assign(S, patch); draw(); window.scrollTo(0, 0); };
  if (!root.dataset.kbBound) {                    // 事件只挂一次:root 在切实例时会被整页重画
    root.dataset.kbBound = '1';
    root.addEventListener('click', (ev) => {
      const t = ev.target.closest('[data-tab],[data-case],[data-rule],[data-node],[data-sys]');
      if (!t || !root.contains(t) || t.hasAttribute('data-dig')) return;
      ev.preventDefault();
      const go = root._kbGo, draw = root._kbDraw;
      if (t.dataset.tab !== undefined) go({ tab: t.dataset.tab, q: '', node: t.dataset.tab === 'graph' ? S.node : '' });
      else if (t.dataset.case !== undefined) go({ tab: 'cases', caseId: t.dataset.case, q: S.tab === 'cases' ? S.q : '', sys: S.tab === 'cases' ? S.sys : '' });
      else if (t.dataset.rule !== undefined) go({ tab: 'rules', ruleId: t.dataset.rule, q: S.tab === 'rules' ? S.q : '' });
      else if (t.dataset.node !== undefined) { S.tab = 'graph'; S.node = t.dataset.node; draw(); }
      else if (t.dataset.sys !== undefined) { S.sys = t.dataset.sys; draw(); }
    });
    root.addEventListener('input', (ev) => {
      if (!ev.target.classList.contains('kb-q')) return;
      const pos = ev.target.selectionStart;
      S.q = ev.target.value; root._kbDraw();
      const i = root.querySelector('.kb-q'); if (i) { i.focus(); i.setSelectionRange(pos, pos); }
    });
  }
  root._kbDraw = draw; root._kbGo = go;
  draw();
}
