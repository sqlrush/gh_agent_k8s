/* R5「云控制台」—— 大盘设计原型的交互逻辑。
   数据：data.js(window.DASH)，形状与用户 Pod 4097 端出的报告一致。
   口径照搬 frontend/:级别四档 + 采集失败（所有维度没采到 ≠ 正常）;WDR 对比上一份报告的窗口；
   Top SQL 一份报告一个排序维度，没取过的不可选。页面只摆放，不改级别。深挖类按钮在原型里只弹提示。 */
(function () {
  'use strict';
  const D = window.DASH;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /* ------------------------------------------------------------ 级别 */
  const LVN = ['正常', '关注', '告警', '严重'];
  const G = {
    0: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="7" fill="currentColor"/><path d="m5.1 8.1 2 2 3.9-4" fill="none" stroke="#fff" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    1: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="7" fill="currentColor"/><path d="M8 4.6v4.1" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/><circle cx="8" cy="11.2" r=".95" fill="#fff"/></svg>',
    2: '<svg viewBox="0 0 16 16"><path d="M7.1 1.9a1 1 0 0 1 1.8 0l6.2 11.2a1 1 0 0 1-.9 1.5H1.8a1 1 0 0 1-.9-1.5z" fill="currentColor"/><path d="M8 6v3.6" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/><circle cx="8" cy="11.8" r=".9" fill="#fff"/></svg>',
    3: '<svg viewBox="0 0 16 16"><path d="M5.2 1h5.6L15 5.2v5.6L10.8 15H5.2L1 10.8V5.2z" fill="currentColor"/><path d="m5.8 5.8 4.4 4.4m0-4.4-4.4 4.4" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/></svg>',
    na: '<svg viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6.4" stroke="currentColor" stroke-width="1.5" stroke-dasharray="2.4 2"/><path d="M5.4 8h5.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  };
  const lk = (l) => (l === null || l === undefined ? 'na' : String(Math.max(0, Math.min(3, l | 0))));
  const lname = (l) => (l === null || l === undefined ? '采集失败' : LVN[l | 0] || '正常');
  const glyph = (l) => `<span class="glyph" data-lv="${lk(l)}">${G[lk(l)]}</span>`;
  const status = (l, text) => `<span class="status" data-lv="${lk(l)}">${glyph(l)}${esc(text ?? lname(l))}</span>`;
  const tag = (l, text) => `<span class="tag" data-lv="${lk(l)}">${glyph(l)}${esc(text ?? lname(l))}</span>`;
  const I = {
    chev: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>',
    chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16v11H9l-5 4z"/></svg>',
    redo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5"/></svg>',
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v12m-5-5 5 5 5-5M5 20h14"/></svg>',
    up: '<svg viewBox="0 0 12 12"><path d="M6 2.5 10 8H2z" fill="currentColor"/></svg>',
    dn: '<svg viewBox="0 0 12 12"><path d="M6 9.5 2 4h8z" fill="currentColor"/></svg>',
    info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><circle cx="12" cy="8" r=".6" fill="currentColor"/></svg>',
    cal: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="5" width="17" height="15" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/></svg>',
    sortd: '<svg width="10" height="10" viewBox="0 0 12 12" style="margin-left:4px;vertical-align:-1px"><path d="M6 9.5 2 4h8z" fill="currentColor"/></svg>',
  };

  /* ------------------------------------------------------------ 格式化（与 frontend/lib/fmt.js 同口径） */
  const num = (v) => { const n = parseFloat(String(v ?? '').replace(/,/g, '')); return Number.isFinite(n) ? n : null; };
  function fmtInt(n) { n = Number(n); if (!Number.isFinite(n)) return '—'; if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + ' 亿'; if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(1) + ' 万'; return n.toLocaleString('en-US'); }
  function fmtSec(s) { s = Number(s); if (!Number.isFinite(s)) return '—'; if (s >= 3600) return (s / 3600).toFixed(1) + ' h'; if (s >= 60) return (s / 60).toFixed(1) + ' min'; return s.toFixed(s < 10 ? 1 : 0) + ' s'; }
  function fmtMs(ms) { ms = Number(ms); if (!Number.isFinite(ms)) return '—'; return ms >= 1000 ? (ms / 1000).toFixed(1) + ' s' : ms.toFixed(1) + ' ms'; }
  const split = (s) => { const m = /^(-?[\d.,]+)\s*(.*)$/.exec(String(s)); return m ? [m[1], m[2]] : [String(s), '']; };
  const TZ = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
  function parts(d) { const p = TZ.formatToParts(d), g = (t) => (p.find((x) => x.type === t) || {}).value; return { y: g('year'), mo: g('month'), d: g('day'), h: g('hour'), mi: g('minute') }; }
  function toDate(s) { const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(s || ''); return m ? new Date(Date.UTC(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6])) : new Date(s); }
  const stamp = (s) => { const d = toDate(s); if (isNaN(d)) return String(s || ''); const p = parts(d); return `${p.mo}-${p.d} ${p.h}:${p.mi}`; };
  const day = (s) => { const d = toDate(s); if (isNaN(d)) return ''; const p = parts(d); return `${p.mo}-${p.d}`; };
  const colFmt = (rows, i) => { const mx = Math.max(0, ...rows.map((r) => Math.abs(num(r[i]) || 0))); return (c) => { const n = num(c); if (!/^-?[\d.]+$/.test(String(c)) || n === null) return String(c); return mx >= 1e6 ? fmtInt(n) : Number.isInteger(n) ? n.toLocaleString('en-US') : n.toLocaleString('en-US', { maximumFractionDigits: 2 }); }; };
  const pangu = (s) => String(s).replace(/([\u3400-\u9fff])([A-Za-z0-9])/g, '$1 $2').replace(/([A-Za-z0-9])([\u3400-\u9fff])/g, '$1 $2');
  const joinCn = (xs) => (xs.length <= 1 ? xs.join('') : xs.slice(0, -1).join('、') + '和' + xs[xs.length - 1]);

  /* ------------------------------------------------------------ 状态 */
  const S = { page: 'health', key: 'og5', run: {}, by: null, kbTab: 'overview' };
  const real = (skill, key) => { const x = D[skill] && D[skill][key]; return x ? x.index.filter((e) => !e.demo && x.runs[e.file]) : []; };
  function current(skill, key) {
    const ents = real(skill, key);
    if (!ents.length) return null;
    const f = S.run[skill + ':' + key], e = ents.find((x) => x.file === f) || ents[ents.length - 1], i = ents.indexOf(e);
    return { entry: e, data: D[skill][key].runs[e.file], index: D[skill][key].index, real: ents, pos: i, prev: i > 0 ? { entry: ents[i - 1], data: D[skill][key].runs[ents[i - 1].file] } : null };
  }
  const entryLevel = (e) => { const dm = e && e.dims; if (dm && dm.total && dm.na === dm.total) return null; return e ? e.overall | 0 : null; };
  const repLevel = (r) => { const ds = (r && r.dims) || []; if (ds.length && ds.every((d) => d.available === false)) return null; return r ? r.overall | 0 : null; };
  const tipAttr = (h) => ` data-tip="${esc(h)}"`;
  const dig = (p) => ` data-dig="${esc(p)}"`;

  /* ------------------------------------------------------------ 共用部件 */
  const panel = (title, desc, body, tools = '', span = 's12', flush = false) => `<section class="panel ${span}"><div class="panel-h"><h3>${title}</h3>${desc ? `<span class="desc">${desc}</span>` : ''}<div class="tools">${tools}</div></div><div class="panel-b ${flush ? 'flush' : ''}">${body}</div></section>`;
  function pageHead(crumb, title, statusTag, actions, desc) {
    return `<div class="crumb">诊断大盘<span>/</span><b>${esc(crumb)}</b></div>
      <div class="page-head"><h1>${esc(title)}</h1>${statusTag || ''}<div class="actions">${actions || ''}</div></div>
      <div class="page-desc">${desc.map(([k, v]) => `<span>${esc(k)}：<b>${v}</b></span>`).join('')}</div>`;
  }
  function runPicker(skill, noun) {
    const cur = current(skill, S.key);
    return cur ? `<button class="btn" id="run-btn" data-skill="${skill}">${I.cal}${esc(noun)} ${esc(stamp(cur.entry.at))}${I.chev}</button>` : '';
  }
  const primary = (p, label) => `<button class="btn primary"${dig(p)}>${I.chat}${esc(label)}</button>`;
  const viewSeg = () => `<div class="seg" data-views><button class="on" data-v="chart">图表</button><button data-v="table">表格</button></div>`;
  const emptyPanel = (title, text, p, label) => panel(title, '', `<div class="empty">${glyph(null)}<h4>还没有报告</h4><p>${esc(text)}</p>${primary(p, label)}</div>`);

  /** 运行历史：按级别堆叠的发现数柱状图（带坐标轴），下方一排级别圆点；示意条目只描边、点不开。 */
  function historyChart(skill, key, curFile, unit, W = 800) {
    const idx = D[skill][key].index;
    if (!idx.length) return '<p class="t3">还没有历史</p>';
    const H = 236, L = 34, R = 8, T = 12, B = 64, pw = W - L - R, ph = H - T - B;
    const tot = (e) => Object.values(e.counts || {}).reduce((a, b) => a + (+b || 0), 0);
    const ymax = Math.max(2, Math.ceil(Math.max(...idx.map(tot)) / 2) * 2);
    const slot = pw / idx.length, bw = Math.min(24, slot * 0.56);
    const y = (v) => T + ph - (v / ymax) * ph;
    const grid = [0, ymax / 2, ymax].map((v) => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/>`).join('');
    const ylab = [0, ymax / 2, ymax].map((v) => `<text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${v}</text>`).join('');
    const colors = { 3: 'var(--crit)', 2: 'var(--warn)', 1: 'var(--notice)' };
    const every = idx.length > 10 ? 2 : 1;
    const cols = idx.map((e, i) => {
      const cx = L + slot * i + slot / 2, x = cx - bw / 2, c = e.counts || {}, l = entryLevel(e);
      let acc = 0; const segs = [];
      [3, 2, 1].forEach((s) => { const n = +c[s] || 0; if (!n) return; const y1 = y(acc + n), y0 = y(acc); segs.push(`<rect class="bar ${e.demo ? 'demo' : ''}" x="${x}" y="${y1 + (acc ? 0 : 0)}" width="${bw}" height="${Math.max(1, y0 - y1 - (acc + n < tot(e) ? 2 : 0))}" rx="${acc + n === tot(e) ? 3 : 0}" fill="${colors[s]}" stroke="${colors[s]}"/>`); acc += n; });
      const tipH = `<b>${esc(stamp(e.at))}</b>${e.demo ? '<span class="demo-mark">示意</span>' : ''}<div class="d">${esc(lname(l))}；严重 ${+c['3'] || 0}，告警 ${+c['2'] || 0}，关注 ${+c['1'] || 0}${e.dims && e.dims.na ? `；未采集 ${e.dims.na}/${e.dims.total} 个维度` : ''}</div>`;
      const dotC = l === null ? 'var(--na)' : ['var(--ok)', 'var(--notice)', 'var(--warn)', 'var(--crit)'][l];
      const cur = e.file && e.file === curFile;
      const dot = `<circle cx="${cx}" cy="${T + ph + 16}" r="${cur ? 5 : 4}" fill="${e.demo || l === null ? 'var(--panel)' : dotC}" stroke="${dotC}" stroke-width="1.5"${l === null ? ' stroke-dasharray="2 1.5"' : ''}/>${cur ? `<circle cx="${cx}" cy="${T + ph + 16}" r="8.5" fill="none" stroke="var(--primary)" stroke-width="1.5"/>` : ''}`;
      const lab = i % every === 0 || i === idx.length - 1 ? `<text x="${cx}" y="${T + ph + 44}" text-anchor="middle"${cur ? ' style="fill:var(--primary);font-weight:600"' : ''}>${esc(day(e.at))}</text>` : '';
      return `<g${e.demo ? '' : ` data-run="${esc(skill)}|${esc(e.file)}" style="cursor:pointer"`}${tipAttr(tipH)}><rect x="${cx - slot / 2}" y="${T}" width="${slot}" height="${ph + 26}" fill="transparent"/>${segs.join('')}${dot}${lab}</g>`;
    }).join('');
    const table = `<table class="tbl"><thead><tr><th>时间</th><th>级别</th><th class="r">严重</th><th class="r">告警</th><th class="r">关注</th><th class="r">未采集维度</th></tr></thead><tbody>${[...idx].reverse().map((e) => { const c = e.counts || {}; return `<tr><td class="num">${esc(stamp(e.at))}${e.demo ? '<span class="demo-mark">示意</span>' : ''}</td><td>${status(entryLevel(e))}</td><td class="r">${+c['3'] || 0}</td><td class="r">${+c['2'] || 0}</td><td class="r">${+c['1'] || 0}</td><td class="r">${e.dims ? e.dims.na + ' / ' + e.dims.total : '—'}</td></tr>`; }).join('')}</tbody></table>`;
    return `<div class="v-chart"><svg class="chart" viewBox="0 0 ${W} ${H}"><g class="grid">${grid}</g>${ylab}${cols}<text x="${L - 8}" y="${T + ph + 20}" text-anchor="end">级别</text></svg>
      <div class="legend"><span data-lv="3"><i class="sw"></i>严重</span><span data-lv="2"><i class="sw"></i>告警</span><span data-lv="1"><i class="sw"></i>关注</span><span><i class="sw hollow"></i>原型补充的示意${unit}</span><span class="t3">柱高为发现条数，点一列切换到那次报告</span></div></div>
      <div class="v-table scroll" hidden>${table}</div>`;
  }

  /* ------------------------------------------------------------ 健康检查 */
  const TITLES = {
    'Overview': '总览', 'Slow SQL': '慢 SQL', 'Long & Idle Transactions': '长事务与空闲事务', 'Connections': '连接',
    'Checkpoint / WAL / Archiving': '检查点与归档', 'Replication / Standby': '主备复制', 'Schema / Objects': '对象与索引',
    'Transactions / Concurrency': '事务并发', 'Wait Events': '等待事件', 'Dead Tuples & Bloat': '死元组与膨胀',
    'Lightweight Locks (LWLock)': '轻量锁', 'Transaction Locks & Blocking Chains': '锁与阻塞链',
  };
  const dt = (d) => TITLES[d] || d;
  const dimLevel = (d) => (d.available === false ? null : (d.findings || []).reduce((a, f) => Math.max(a, f.severity | 0), 0));
  const ci = (d, n) => (d.headers || []).findIndex((h) => String(h).startsWith(n));
  const cell = (d, r, n) => { const i = ci(d, n), row = (d.rows || [])[r]; return i < 0 || !row ? null : row[i]; };
  const KEYFIG = {
    'Overview': (d) => { const h = num(cell(d, 0, 'cache_hit')); return { v: h === null ? '—' : h.toFixed(1), u: '% 缓存命中', cap: `连接 ${cell(d, 0, 'connections') ?? '—'} / ${cell(d, 0, 'max_conn') ?? '—'}` }; },
    'Slow SQL': (d) => { if (!(d.rows || []).length) return { v: '0', u: '条', cap: '没有慢 SQL' }; const [v, u] = split(fmtMs(num(cell(d, 0, 'avg_ms')))); return { v, u: u + ' 平均耗时', cap: `最慢的是 ${cell(d, 0, 'sql_id')}，调用 ${fmtInt(num(cell(d, 0, 'calls')))} 次` }; },
    'Long & Idle Transactions': (d) => { const r = d.rows || []; if (!r.length) return { v: '0', u: '个', cap: '没有长事务' }; const mx = Math.max(...r.map((_, i) => num(cell(d, i, '时长')) || 0)); const [v, u] = split(fmtSec(mx)); return { v, u: u + ' 最长', cap: `${r.length} 个会话，pid ${cell(d, 0, 'pid')}` }; },
    'Connections': (d) => { const r = d.rows || [], t = r.reduce((a, x) => a + (num(x[1]) || 0), 0); return { v: String(t), u: '个会话', cap: r.map((x) => `${x[0] === 'active' ? '活跃' : x[0] === 'idle' ? '空闲' : x[0]} ${x[1]}`).join(',') }; },
    'Checkpoint / WAL / Archiving': (d) => { const row = (d.rows || []).find((x) => /timed\/req/.test(x[0])), arc = (d.rows || []).find((x) => /archive/.test(x[0])); if (!row) return null; const [t, q] = String(row[1]).split('/').map(num); return { v: (t + q ? (q / (t + q)) * 100 : 0).toFixed(1), u: '% 请求型', cap: `checkpoint ${fmtInt(t + q)} 次，归档 ${arc ? arc[1] : '—'}` }; },
    'Replication / Standby': (d) => { const r = d.rows || []; if (!r.length) return { v: '0', u: '个备机', cap: '没有备机' }; const [v, u] = split(String(cell(d, 0, 'replay_lag') || '—')); return { v, u: u + ' 回放延迟', cap: `${r.length} 个备机，${cell(d, 0, 'state') || ''} ${cell(d, 0, 'sync') || ''}` }; },
    'Schema / Objects': (d) => { const it = (d.rows || []).filter((x) => x[0] !== '观测窗口'); return { v: String(it.length), u: '项问题对象', cap: [...new Set(it.map((x) => x[0]))].join('、') || '没有问题对象' }; },
    'Transactions / Concurrency': (d) => { const dl = (d.rows || []).find((x) => /deadlock/.test(x[0])), cr = (d.rows || []).find((x) => /commit/.test(x[0])); const rb = cr ? (/([\d.]+)%/.exec(cr[1]) || [])[1] : null; return { v: dl ? String(dl[1]) : '—', u: '次死锁', cap: `回滚率 ${rb ?? '—'}%` }; },
  };
  function verdict(lvl, fs, dims) {
    if (lvl === null) return '这次一个维度都没有采到，没有发现不等于没有问题。请先排查采集为什么失败。';
    if (!fs.length) return `${dims.length} 个维度都在阈值以内。`;
    const by = (s) => [...new Set(fs.filter((f) => (f.severity | 0) === s).map((f) => dt(f.dimension)))];
    const p = [];
    if (by(3).length) p.push(`${joinCn(by(3))}达到严重级别`);
    if (by(2).length) p.push(`${joinCn(by(2))}越过告警阈值`);
    if (by(1).length) p.push(`${joinCn(by(1))}需要关注`);
    return p.join('；') + '。';
  }
  function since(cur) {
    if (!cur.prev) return '这是该实例的第一次巡检。';
    const pc = cur.prev.entry.counts || {}, cc = cur.entry.counts || {};
    const bits = [[3, '严重'], [2, '告警'], [1, '关注']].map(([s, n]) => { const d = (+cc[s] || 0) - (+pc[s] || 0); return d > 0 ? `多 ${d} 条${n}` : d < 0 ? `少 ${-d} 条${n}` : ''; }).filter(Boolean);
    return `与上次巡检（${stamp(cur.prev.entry.at)}，${lname(entryLevel(cur.prev.entry))}）相比：${bits.length ? bits.join(',') : '发现没有变化'}。`;
  }
  function parseCmp(t) { const m = /^\s*(>=|>|<=|<)\s*([\d,.]+)\s*(%|ms|s|MB|GB|MiB|GiB)?\s*$/.exec(String(t || '')); return m ? { op: m[1], v: num(m[2]) } : null; }
  function parseVal(t) { const m = /^\s*([\d,.]+)\s*(%|ms|s|MB|GB|MiB|GiB)?\s*$/.exec(String(t || '')); return m ? num(m[1]) : null; }
  function over(f) {
    const thr = parseCmp(f.threshold), v = parseVal(f.value);
    if (!thr || v === null || !thr.v || thr.op[0] !== '>') return '<span class="t3">组合条件，见证据</span>';
    const sc = Math.max(v, thr.v) * 1.1;
    return `<div class="over" data-lv="${lk(f.severity)}"><div class="bar"><i style="width:${(v / sc) * 100}%"></i><span class="thr" style="left:${(thr.v / sc) * 100}%"></span></div><div class="x">阈值的 ${(v / thr.v).toFixed(1)} 倍</div></div>`;
  }
  const fPrompt = (f) => (f.sql_id ? `请解释并优化 sql_id 为 ${f.sql_id} 的语句：健康检查发现 ${f.code},${f.metric}=${f.value}（阈值 ${f.threshold})。` : `请针对健康检查发现 ${f.code}(${f.metric}=${f.value}，阈值 ${f.threshold}）做深入分析：${f.evidence}`);

  function renderHealth() {
    const cur = current('health', S.key);
    const head0 = (st, act, desc) => pageHead('健康检查', '健康检查', st, act, desc);
    if (!cur) return head0('', '', []) + `<div class="row">${emptyPanel('巡检报告', '这个实例还没有健康检查报告。在会话里做一次巡检，报告会自动出现在这里。', '请对当前实例做一次健康检查。', '做一次健康检查')}</div>`;
    const r = cur.data, lvl = repLevel(r), dims = r.dims || [], fs = [...(r.findings || [])].sort((a, b) => (b.severity | 0) - (a.severity | 0));
    const cnt = { 0: 0, 1: 0, 2: 0, 3: 0, na: 0 };
    dims.forEach((d) => { const l = dimLevel(d); cnt[l === null ? 'na' : l]++; });
    const pc = (cur.prev && cur.prev.entry.counts) || {}, cc = cur.entry.counts || {};
    const dTot = Object.values(cc).reduce((a, b) => a + (+b || 0), 0) - Object.values(pc).reduce((a, b) => a + (+b || 0), 0);
    const statsBlock = lvl === null ? `<div class="stats"><div><div class="l">${glyph(null)}采集失败的维度</div><div class="v">${cnt.na}<small>/ ${dims.length}</small></div><div class="s">一个维度都没有采到</div></div></div>`
      : `<div class="stats"><div><div class="l">越过阈值的发现</div><div class="v">${fs.length}<small>条</small></div><div class="s">${cur.prev ? (dTot > 0 ? `<span class="delta bad">${I.up}${dTot}</span> 比上次多` : dTot < 0 ? `<span class="delta better">${I.dn}${-dTot}</span> 比上次少` : '与上次持平') : '第一次巡检'}</div></div>
        <div><div class="l">${glyph(2)}告警维度</div><div class="v">${cnt[2] + cnt[3]}<small>个</small></div><div class="s">${cnt[3] ? `其中严重 ${cnt[3]} 个` : '没有严重'}</div></div>
        <div><div class="l">${glyph(1)}关注维度</div><div class="v">${cnt[1]}<small>个</small></div><div class="s">需要跟进</div></div>
        <div><div class="l">${glyph(0)}正常维度</div><div class="v">${cnt[0]}<small>个</small></div><div class="s">共 ${dims.length} 个维度</div></div></div>`;
    const overview = `<div class="split"><div class="overall">${glyph(lvl)}<div><div class="lvl">${esc(lname(lvl))}</div><p>${esc(pangu(verdict(lvl, fs, dims)))}</p><div class="since">${esc(pangu(since(cur)))}</div>
        ${lvl === null ? `<div class="note">原因：${esc([...new Set(dims.map((d) => d.note || d.headline))].join(';'))}。常见是中间件不通、执行账号没有权限或实例地址填错。</div>` : ''}</div></div>${statsBlock}</div>`;
    const ftable = fs.length ? `<div class="scroll"><table class="tbl"><thead><tr><th>级别</th><th>维度</th><th>指标</th><th class="r">当前值</th><th class="r">阈值</th><th>超出程度</th><th>证据</th><th class="r">操作</th></tr></thead><tbody>${fs.map((f) => `<tr><td>${status(f.severity)}</td><td>${esc(dt(f.dimension))}</td><td class="wrap">${esc(f.metric)}</td><td class="r"><b style="font-weight:500">${esc(f.value)}</b></td><td class="r t2">${esc(f.threshold)}</td><td>${over(f)}</td><td class="ev" title="${esc(f.evidence)}">${esc(f.evidence)}</td><td class="ops"><a href="#"${dig(fPrompt(f))}>分析</a>${f.sql_id ? `<a href="#"${dig(fPrompt(f))}>执行计划</a>` : ''}</td></tr>`).join('')}</tbody></table></div>`
      : `<div class="empty" style="padding:32px">${glyph(lvl === null ? null : 0)}<p style="margin-top:10px">${lvl === null ? '这次什么都没采到，所以也没有发现。' : '没有越过阈值的发现。'}</p></div>`;
    const tiles = `<div class="dims">${dims.map((d, i) => { const l = dimLevel(d), k = d.available === false ? null : (KEYFIG[d.dimension] || (() => null))(d); const s = k || { v: '—', u: '', cap: d.available === false ? '未采集' : d.headline || '' };
      return `<button class="dim" data-dim="${i}"${tipAttr(`<b>${esc(dt(d.dimension))}</b><div class="d">${esc(d.headline || '')}</div>`)}><div class="h"><b>${esc(dt(d.dimension))}</b>${status(l)}</div><div class="v num">${esc(s.v)}<small>${esc(s.u)}</small></div><div class="c">${esc(s.cap)}</div></button>`; }).join('')}</div>`;
    const subs = `<div class="list">${(r.sub_skills || []).map((s) => `<div class="it">${glyph(s.ok ? 0 : null)}<div class="m"><b>${esc(s.skill.replace('gaussdb-', ''))}</b>${s.ok ? '' : `<small>${esc(s.error)}</small>`}</div><span class="r">${s.ok ? '已纳入汇总' : '执行失败'}</span></div>`).join('')}</div>
      <div class="note">不在巡检里的能力：${esc((r.uncovered_capabilities || []).map((x) => x.replace('gaussdb-', '')).join('、'))}。需要时在会话里单独运行。<br>知识库参照：${(r.kb_refs && r.kb_refs.status && r.kb_refs.status.attached) ? `已接入，命中 ${(r.kb_refs.items || []).length} 条` : `未接入（${esc((r.kb_refs && r.kb_refs.status && r.kb_refs.status.reason) || '未配置')})`}。</div>`;
    return head0(tag(lvl), runPicker('health', '巡检') + `<button class="btn"${dig('请对当前实例重新做一次完整健康检查。')}>${I.redo}重新巡检</button>` + primary('请逐条解释本次健康检查的发现，并给出处置顺序。', '在会话里分析'),
      [['实例', esc(r.conn)], ['执行人', esc(D.whoami.user_id)], ['巡检时间', esc(stamp(cur.entry.at))], ['巡检次数', `第 ${cur.pos + 1} 次`]])
      + `<div class="row">${panel('巡检概况', '级别由脚本按阈值判定，模型不改', overview)}</div>
      <div class="row">${panel(`需要处理的发现<span class="t3" style="font-weight:400;margin-left:6px">(${fs.length})</span>`, '按级别从高到低', ftable, '', 's12', !!fs.length)}</div>
      <div class="row">${panel('维度状态', `共 ${dims.length} 个维度，点击查看明细`, tiles)}</div>
      <div class="row">${panel('巡检趋势', `最近 ${cur.index.length} 次`, historyChart('health', S.key, cur.entry.file, '次', 760), viewSeg(), 's8')}${panel('子技能执行', '任一失败也照常出报告', subs, '', 's4')}</div>`;
  }
  function openDim(i) {
    const r = current('health', S.key).data, d = r.dims[i], l = dimLevel(d), h = d.headers || [], F = h.map((x, j) => colFmt(d.rows || [], j));
    const table = (d.rows || []).length ? `<div class="scroll" style="border:1px solid var(--line);border-radius:6px"><table class="tbl"><thead><tr>${h.map((x) => `<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${d.rows.map((row) => `<tr>${row.map((c, j) => `<td class="${/^-?[\d.,]+$/.test(String(c)) ? 'r' : String(c).length > 40 ? 'q' : ''}" title="${esc(c)}">${esc(F[j](c))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : `<p class="t3">${esc(d.note || '没有数据行')}</p>`;
    const fl = (d.findings || []).map((f) => `<div class="alert" data-lv="${lk(f.severity)}" style="margin-bottom:8px">${glyph(f.severity)}<div><b>${esc(f.metric)}：${esc(f.value)}，阈值 ${esc(f.threshold)}</b><p>${esc(f.evidence)}</p></div></div>`).join('');
    drawer(`${esc(dt(d.dimension))}`, tag(l), `<div class="sec">概况</div><p style="margin:0" class="t2">${esc(d.headline || '')}</p>${fl ? `<div class="sec">发现</div>${fl}` : ''}<div class="sec">明细</div>${table}`,
      primary(`请深入分析健康检查的「${dt(d.dimension)}」维度：${d.headline}`, '在会话里分析这个维度') + '<button class="btn" data-close>关闭</button>');
  }

  /* ------------------------------------------------------------ Top SQL */
  const BY = [['time', '总耗时', 'total_sec', fmtSec], ['avg', '平均耗时', 'avg_ms', fmtMs], ['calls', '调用次数', 'calls', fmtInt], ['reads', '逻辑读', null, null], ['rows', '返回行', 'rows', fmtInt]];
  const KW = /\b(SELECT|FROM|WHERE|GROUP BY|ORDER BY|LEFT JOIN|JOIN|ON|AND|OR|UPDATE|SET|INSERT INTO|VALUES|DELETE|AS|IN|LIMIT|HAVING|sum|count)\b/gi;
  const hl = (sql) => esc(sql).replace(KW, (m) => `<span class="kw">${m}</span>`).replace(/(\$\d+)/g, '<span class="ph">$1</span>');
  function topsqlData() {
    const x = D.topsql[S.key], ents = real('topsql', S.key);
    if (!x || !ents.length) return null;
    const per = {}; ents.forEach((e) => { per[x.runs[e.file].by] = e; });
    const by = S.by && per[S.by] ? S.by : x.runs[ents[ents.length - 1].file].by;
    return { per, by, e: per[by], rep: x.runs[per[by].file], tune: D.sqltune[S.key] || {} };
  }
  function renderTopsql() {
    const t = topsqlData();
    const head0 = (act, desc) => pageHead('Top SQL', 'Top SQL', '', act, desc);
    if (!t) return head0('', []) + `<div class="row">${emptyPanel('SQL 榜单', '这个实例还没有 Top SQL 报告。在会话里取一次，结果会自动出现在这里。', '请列出当前实例总耗时最高的 SQL。', '取一次 Top SQL')}</div>`;
    const { per, by, e, rep, tune } = t, rows = rep.rows || [];
    const [, label, field, fmt] = BY.find((b) => b[0] === by);
    const sumT = rows.reduce((a, r) => a + (+r.total_sec || 0), 0) || 1, sumC = rows.reduce((a, r) => a + (+r.calls || 0), 0);
    const top1 = rows[0] ? ((+rows[0].total_sec || 0) / sumT) * 100 : 0;
    const tagOf = (r) => { const x = tune[r.sql_id]; return x && x.skipped ? '<span class="tag gray">系统 SQL</span>' : x ? '<span class="tag blue">已调优</span>' : '<span class="t3">未调优</span>'; };
    const seg = `<div class="seg">${BY.map(([b, l]) => `<button data-by="${b}" class="${b === by ? 'on' : ''}" ${per[b] ? '' : 'disabled'}${per[b] ? '' : tipAttr(`还没按「${esc(l)}」取过<div class="d">在会话里说「按${esc(l)}看 Top SQL」</div>`)}>${esc(l)}</button>`).join('')}</div>`;
    const [tv, tu] = split(fmtSec(sumT)), [cv, cu] = split(fmtInt(sumC));
    const stats = `<div class="stats"><div><div class="l">上榜语句</div><div class="v">${rows.length}<small>条</small></div><div class="s">按${esc(label)}取前 ${esc(rep.limit)} 条</div></div>
      <div><div class="l">上榜总调用</div><div class="v">${esc(cv)}<small>${esc(cu ? cu + ' ' : '')}次</small></div><div class="s">自统计视图重置以来</div></div>
      <div><div class="l">上榜总耗时</div><div class="v">${esc(tv)}<small>${esc(tu)}</small></div><div class="s">合计 ${fmtInt(Math.round(sumT))} 秒</div></div>
      <div><div class="l">第一名占上榜耗时</div><div class="v">${top1.toFixed(1)}<small>%</small></div><div class="s">${esc(rows[0] ? rows[0].sql_id : '')}</div></div></div>`;
    const th = (name, f) => `<th class="r">${name}${f === field ? I.sortd : ''}</th>`;
    const table = `<div class="scroll"><table class="tbl"><thead><tr><th>排名</th><th>sql_id</th><th>语句</th>${th('调用次数', 'calls')}${th('总耗时', 'total_sec')}${th('平均耗时', 'avg_ms')}${th('返回行', 'rows')}<th>占上榜耗时</th><th>调优</th><th class="r">操作</th></tr></thead><tbody>${rows.map((r, i) => `<tr><td class="t3">${i + 1}</td><td class="id"><a href="#" data-sql="${esc(r.sql_id)}">${esc(r.sql_id)}</a></td><td class="q" title="${esc(r.query)}">${esc(r.query)}</td><td class="r">${fmtInt(r.calls)}</td><td class="r">${fmtSec(r.total_sec)}</td><td class="r">${fmtMs(r.avg_ms)}</td><td class="r">${fmtInt(r.rows)}</td><td><div class="share"><div class="b"><i style="width:${(((+r.total_sec || 0) / sumT) * 100).toFixed(1)}%"></i></div><b>${(((+r.total_sec || 0) / sumT) * 100).toFixed(1)}%</b></div></td><td>${tagOf(r)}</td><td class="ops"><a href="#" data-sql="${esc(r.sql_id)}">详情</a><a href="#"${dig(`请调优 sql_id 为 ${r.sql_id} 的语句：${r.query}`)}>调优</a></td></tr>`).join('')}</tbody></table></div>`;
    return head0(`<button class="btn"${dig('请重新取一次当前实例的 Top SQL。')}>${I.redo}重新取数</button>` + primary('请分析当前实例 Top SQL 榜单，指出最值得先调的语句。', '在会话里分析'),
      [['实例', esc(rep.conn)], ['取数时间', esc(stamp(e.at))], ['口径', '累计统计视图，不是时间窗口；含系统与监控 SQL']])
      + `<div class="row">${panel('概况', '', stats)}</div>
      <div class="row">${panel('SQL 榜单', `按${esc(label)}排序，点 sql_id 查看详情与调优结论`, table, seg, 's12', true)}</div>`;
  }
  function openSql(id) {
    const t = topsqlData(); if (!t) return;
    const rows = t.rep.rows || [], s = rows.find((r) => r.sql_id === id); if (!s) return;
    const x = t.tune[s.sql_id];
    let tuneHtml;
    if (x && x.skipped) tuneHtml = `<div class="alert" data-lv="na">${glyph(null)}<div><b>系统 SQL，按策略不调优</b><p>只引用了系统对象 ${esc((x.system_objects || []).join('、'))}。这类慢多半来自采集频率或系统压力，不是语句本身的问题。</p></div></div>`;
    else if (x && x.evidence) { const sv = { info: 1, warn: 2, crit: 3 };
      tuneHtml = (x.evidence.findings || []).map((f) => `<div class="alert" data-lv="${lk(sv[f.severity] ?? 1)}" style="margin-bottom:8px">${glyph(sv[f.severity] ?? 1)}<div><b>${esc(f.detail)}</b><p>建议：${esc(f.advice)}</p></div></div>`).join('')
        + `<div class="sec">执行计划<span class="t3" style="font-weight:400;font-size:13px;margin-left:8px">${x.evidence.analyzed ? '实际执行' : '估算'},${esc(x.evidence.version)}</span></div><div class="plan">${esc(x.evidence.plan)}</div>`; }
    else tuneHtml = `<div class="alert" data-lv="na">${glyph(null)}<div><b>还没有调优过</b><p>在会话里调优后，建议与执行计划会出现在这里。</p></div></div>`;
    drawer(`SQL 详情 ${esc(s.sql_id)}`, x && x.skipped ? '<span class="tag gray">系统 SQL</span>' : x ? '<span class="tag blue">已调优</span>' : '',
      `<div class="sec">语句</div><div class="sql">${hl(s.query)}</div>
       <div class="sec">执行统计</div><div class="kvs" style="--cols:4"><div><div class="kv"><div class="k">调用次数</div><div class="v num">${fmtInt(s.calls)}</div></div></div><div><div class="kv"><div class="k">总耗时</div><div class="v num">${fmtSec(s.total_sec)}</div></div></div><div><div class="kv"><div class="k">平均耗时</div><div class="v num">${fmtMs(s.avg_ms)}</div></div></div><div><div class="kv"><div class="k">返回行</div><div class="v num">${fmtInt(s.rows)}</div></div></div></div>
       <div class="sec">调优结论</div>${tuneHtml}`,
      primary(`请调优 sql_id 为 ${s.sql_id} 的语句（调用 ${s.calls} 次，平均 ${fmtMs(s.avg_ms)}):${s.query}`, x && !x.skipped ? '在会话里继续调优' : '在会话里调优') + `<button class="btn" data-copy="${esc(s.sql_id)}">复制 sql_id</button>`);
  }

  /* ------------------------------------------------------------ WDR */
  const WD = { loadprofile: 'Load Profile', dbstat: 'Database Stat', topsql: 'Top SQL', waits: 'Wait Events / Classes', checkpoint: 'Checkpoint / BgWriter / Redo', cache: 'Cache / Memory', fileio: 'File IO' };
  const wdim = (d, k) => (d && d.dims || []).find((x) => x.dimension === WD[k]);
  const wval = (d, k, c) => { const x = wdim(d, k); if (!x || !(x.rows || []).length) return null; const i = (x.headers || []).findIndex((h) => String(h).startsWith(c)); return i < 0 ? null : num(x.rows[0][i]); };
  const ratio = (a, s) => (a === null || !s ? null : a / s);
  const KPIS = [
    { l: 'DB Time', dim: 'loadprofile', col: 'DB time', u: 's', worse: 'up' },
    { l: '平均活跃会话', calc: (d, s) => ratio(wval(d, 'loadprofile', 'DB time'), s), u: '', worse: 'up' },
    { l: '事务 TPS', calc: (d, s) => ratio(wval(d, 'loadprofile', 'commits'), s), u: '/s', worse: null },
    { l: 'CPU 占 DB Time', dim: 'loadprofile', col: 'CPU占DBtime', u: '%', worse: null },
    { l: '缓存命中率', dim: 'dbstat', col: 'cache_hit', u: '%', worse: 'down' },
    { l: '物理读', dim: 'loadprofile', col: '物理读', u: '块', worse: 'up' },
    { l: '临时溢出', dim: 'dbstat', col: '临时溢出', u: 'MiB', worse: 'up' },
    { l: '回滚率', dim: 'dbstat', col: '回滚率', u: '%', worse: 'up' },
  ];
  const kpiVal = (k, d) => { if (!d) return null; const s = (Number(d.window && d.window.duration_min) || 0) * 60; return k.calc ? k.calc(d, s) : wval(d, k.dim, k.col); };
  const fmtK = (v) => (Math.abs(v) >= 1e4 ? fmtInt(v) : Math.abs(v) >= 100 ? fmtInt(Math.round(v)) : String(+v.toFixed(v < 1 ? 3 : 1)));
  const showK = (v, u) => (u === 'MiB' && Math.abs(v) >= 1024 ? [fmtK(v / 1024), 'GiB'] : [fmtK(v), u]);
  function kpi(k, cur, prev) {
    if (cur === null) return `<div><div class="l">${esc(k.l)}</div><div class="na">未采集</div></div>`;
    let d = '<span class="t3">没有可对比的窗口</span>';
    if (prev !== null && prev !== undefined) {
      const r = prev ? cur / prev : null, diff = cur - prev;
      const worse = k.worse === 'up' ? diff > 0 : k.worse === 'down' ? diff < 0 : false;
      const cls = worse ? (r && (r >= 3 || r <= 1 / 3) ? 'bad' : 'worse') : k.worse && diff !== 0 ? 'better' : 'flat';
      const ch = !prev ? '新出现' : r && Math.abs(r) >= 1.5 ? '×' + r.toFixed(1) : k.u === '%' ? Math.abs(diff).toFixed(1) + ' pt' : diff === 0 ? '持平' : Math.abs((diff / prev) * 100).toFixed(0) + '%';
      const [pv, pu] = showK(prev, k.u);
      d = `<span class="delta ${cls}">${diff > 0 ? I.up : diff < 0 ? I.dn : ''}${esc(ch)}</span><span>上一窗口 ${esc(pv)}${pu ? ' ' + esc(pu) : ''}</span>`;
    }
    const [cv, cu] = showK(cur, k.u), [n, u2] = split(cv);
    return `<div><div class="l">${esc(k.l)}</div><div class="v num">${esc(n)}<small>${esc((u2 ? u2 + ' ' : '') + cu)}</small></div><div class="s">${d}</div></div>`;
  }
  function hb(dim, valCol, unit) {
    if (!dim || !(dim.rows || []).length) return `<p class="t3">${esc(dim && dim.note || '未采集')}</p>`;
    const h = dim.headers || [], F = h.map((x, i) => colFmt(dim.rows, i)), mx = Math.max(1, ...dim.rows.map((r) => num(r[valCol]) || 0));
    const other = h.map((x, i) => i).filter((i) => i && i !== valCol);
    return `<div class="hb">${dim.rows.map((r) => `<div class="r"${tipAttr(`<b>${esc(r[0])}</b>${h.map((x, i) => (i ? `<div class="d">${esc(x)}:${esc(F[i](r[i]))}</div>` : '')).join('')}`)}><div class="l">${esc(r[0])}${other.length ? `<small>${other.map((i) => `${esc(h[i])} ${esc(F[i](r[i]))}`).join(',')}</small>` : ''}</div><div class="b"><i style="width:${((num(r[valCol]) || 0) / mx) * 100}%"></i></div><div class="v">${esc(F[valCol](r[valCol]))}${unit ? `<small>${esc(unit)}</small>` : ''}</div></div>`).join('')}</div>`;
  }
  function renderWdr() {
    const cur = current('wdr', S.key);
    const head0 = (st, act, desc) => pageHead('WDR 分析', 'WDR 分析', st, act, desc);
    if (!cur) return head0('', '', []) + `<div class="row">${emptyPanel('WDR 报告', '这个实例还没有 WDR 报告。在会话里对一个快照窗口做分析，报告会自动出现在这里。', '请列出最近的 WDR 快照，并对最近一个窗口做分析。', '做一次 WDR 分析')}</div>`;
    const d = cur.data, w = d.window || {}, p = cur.prev && cur.prev.data, pw = p && p.window, native = d.native || {};
    if (w.wdr_enabled === false) return head0('', '', []) + `<div class="row">${panel('WDR 快照', '', `<div class="empty">${glyph(null)}<h4>这个库没有开启 WDR 快照</h4><p>需要把 enable_wdr_snapshot 设为 on，并有快照权限。</p>${primary('请检查当前库的 WDR 快照是否开启，说明如何开启以及需要的权限。', '在会话里检查')}</div>`)}</div>`;
    const fs = [...(d.findings || [])].sort((a, b) => (b.severity | 0) - (a.severity | 0));
    const cpu = wval(d, 'loadprofile', 'CPU占DBtime'), dbt = wval(d, 'loadprofile', 'DB time'), waits = wdim(d, 'waits'), ts = wdim(d, 'topsql');
    const win = `<div class="kvs" style="--cols:4">
      <div><div class="kv"><div class="k">分析窗口</div><div class="v num">snap ${esc(w.begin_id)} 至 ${esc(w.end_id)}</div></div><div class="kv"><div class="k">时间</div><div class="v num">${esc(String(w.begin_ts || '').slice(0, 16))} 至 ${esc(String(w.end_ts || '').slice(11, 16))}</div></div></div>
      <div><div class="kv"><div class="k">窗口时长</div><div class="v">${esc(w.duration_min)} 分钟</div></div><div class="kv"><div class="k">范围</div><div class="v">${esc(w.scope || '')} ${esc(w.node || '')}</div></div></div>
      <div><div class="kv"><div class="k">对比窗口</div><div class="v num">${pw ? `snap ${esc(pw.begin_id)} 至 ${esc(pw.end_id)}` : '没有'}</div></div><div class="kv"><div class="k">对比说明</div><div class="v">${pw ? `上一份报告，${esc(pw.duration_min)} 分钟` : '这是第一份报告'}</div></div></div>
      <div><div class="kv"><div class="k">原生 WDR 报告</div><div class="v">${native.generated ? `${status(0, '已生成')} <span class="t3">${fmtInt(Math.round((native.bytes || 0) / 1024))} KB</span>` : status(null, '未生成')}</div></div>${native.generated ? `<div class="kv"><div class="k">下载</div><div class="v"><a href="#" data-toast="原型：真实环境会下载 openGauss 原生 WDR 报告（HTML)">下载 HTML 报告</a></div></div>` : ''}</div></div>`;
    const comp = cpu === null ? '<p class="t3">未采集</p>' : `<div class="stackbar"><i style="flex:${cpu};background:var(--primary)"${tipAttr(`<b>CPU ${cpu.toFixed(1)}%</b>`)}></i><i style="flex:${100 - cpu};background:var(--gray-mark)"${tipAttr(`<b>等待 ${(100 - cpu).toFixed(1)}%</b><div class="d">锁、IO、睡眠等</div>`)}></i></div>
      <div class="legend" style="margin-top:10px"><span><i class="sw" style="--c:var(--primary)"></i>CPU ${cpu.toFixed(1)}%</span><span><i class="sw" style="--c:var(--gray-mark)"></i>等待（锁、IO、睡眠）${(100 - cpu).toFixed(1)}%</span></div>`;
    const dbtBody = `<div class="big num">${dbt === null ? '—' : esc(fmtInt(Math.round(dbt)))}<small>秒，平均 ${w.duration_min && dbt !== null ? (dbt / (w.duration_min * 60)).toFixed(1) : '—'} 个活跃会话</small></div>${comp}
      <div class="subt">等待时间按等待类拆开（占等待总时间）</div>${hb(waits, waits ? (waits.headers || []).length - 1 : 0, '%')}`;
    const verdicts = fs.length ? `<div class="list">${fs.map((f) => `<div class="it">${glyph(f.severity)}<div class="m"><b>${esc(f.metric)}：${esc(f.value)}</b><small>${esc(lname(f.severity))}，阈值 ${esc(f.threshold)}。${esc(f.evidence)}</small></div><a href="#" class="r"${dig(`请针对 WDR 发现 ${f.code}(${f.metric}=${f.value}）做深入分析：${f.evidence}`)}>分析</a></div>`).join('')}</div>` : `<div class="empty" style="padding:24px">${glyph(0)}<p style="margin-top:10px">窗口内没有越过阈值的发现。</p></div>`;
    const tsh = (ts && ts.headers) || [], shareI = tsh.findIndex((h) => String(h).startsWith('占DB')), TF = tsh.map((x, i) => colFmt((ts && ts.rows) || [], i));
    const tsTable = ts && (ts.rows || []).length ? `<div class="scroll"><table class="tbl"><thead><tr>${tsh.map((h, i) => `<th class="${i && i !== tsh.length - 1 && i !== shareI ? 'r' : ''}">${esc(h)}</th>`).join('')}</tr></thead><tbody>${ts.rows.map((r) => `<tr>${r.map((c, i) => (i === 0 ? `<td class="id"><a href="#"${dig(`请分析 WDR 窗口内 sql_id 为 ${c} 的语句。`)}>${esc(c)}</a></td>` : i === shareI ? `<td><div class="share"><div class="b"><i style="width:${Math.min(100, num(c) || 0)}%"></i></div><b>${esc(c)}%</b></div></td>` : i === r.length - 1 ? `<td class="q" title="${esc(c)}">${esc(c)}</td>` : `<td class="r">${esc(TF[i](c))}</td>`)).join('')}</tr>`).join('')}</tbody></table></div>` : '<p class="t3" style="padding:0 20px">未采集</p>';
    const ck = wdim(d, 'checkpoint');
    const ckBody = ck && (ck.rows || [])[0] ? `<p class="t2" style="margin:0 0 14px;font-size:13px">${esc(ck.headline || '')}</p><div class="kvs" style="--cols:${(ck.headers || []).length}">${(ck.headers || []).map((h, i) => `<div><div class="kv"><div class="k">${esc(h)}</div><div class="v num" style="font-size:20px">${esc(ck.rows[0][i])}</div></div></div>`).join('')}</div>` : '<p class="t3">未采集</p>';
    return head0(tag(d.overall | 0), runPicker('wdr', '窗口') + primary('请解读这个 WDR 窗口，说明负载变化的主要原因。', '在会话里分析'), [['实例', esc(d.conn)], ['执行人', esc(D.whoami.user_id)], ['生成时间', esc(stamp(cur.entry.at))]])
      + `<div class="row">${panel('分析窗口', '', win)}</div>
      <div class="row">${panel('负载概况', '与上一窗口对比', `<div class="stats grid4">${KPIS.map((k) => kpi(k, kpiVal(k, d), kpiVal(k, p))).join('')}</div>`)}</div>
      <div class="row">${panel('DB Time 构成', 'CPU 与等待', dbtBody, `<a href="#"${dig('请分析最近一个 WDR 窗口的 DB Time 构成，说明是算力型还是等待型负载。')}>分析</a>`, 's8')}${panel(`结论<span class="t3" style="font-weight:400;margin-left:6px">(${fs.length})</span>`, '脚本按阈值判定', verdicts, '', 's4')}</div>
      <div class="row">${panel('窗口内 Top SQL', esc(ts && ts.headline || ''), tsTable, '', 's12', true)}</div>
      <div class="row">${panel('检查点与 Redo', '', ckBody, '', 's4')}${panel('物理读最多的对象', '', hb(wdim(d, 'cache'), 1, '块'), '', 's4')}${panel('文件 IO', '按物理读', hb(wdim(d, 'fileio'), 1, ''), '', 's4')}</div>
      <div class="row">${panel('窗口历史', `最近 ${cur.index.length} 个分析过的窗口`, historyChart('wdr', S.key, cur.entry.file, '窗口', 1140), viewSeg())}</div>`;
  }

  /* ------------------------------------------------------------ 知识库 */
  function renderKb() {
    const h = D.kb.health, st = h.status || {}, cat = D.kb.catalog, c = st.counts || {};
    const cov = num((/覆盖\s*([\d.]+)%/.exec(st.vector || '') || [])[1]);
    const issues = (h.file_warnings || []).length + (cov !== null && cov < 95 ? 1 : 0) + (h.pending || []).length;
    const rules = cat.groups.flatMap((g) => g.rules.map((r) => ({ ...r, group: g.title, file: g.file })));
    const cases = [...cat.cases].sort((a, b) => String(b.occurred_at).localeCompare(String(a.occurred_at)));
    const tabs = `<div class="tabs">${[['overview', '总览', ''], ['cases', '案例', cat.cases.length], ['rules', '条款', rules.length], ['graph', '关系图', cat.edges.length]].map(([k, l, n]) => `<button data-kbtab="${k}" class="${S.kbTab === k ? 'on' : ''}">${l}${n !== '' ? `<span class="n">${n}</span>` : ''}</button>`).join('')}</div>`;
    const top = pageHead('知识库', '知识库', issues ? tag(1, `${issues} 项待处理`) : tag(0, '健康'), '', [['模式', esc(st.mode)], ['版本', esc(st.version)], ['本环境', h.readonly ? '只读' : '可写'], ['维护', '全体共享，由知识库管理员在导入环境维护']]) + tabs;
    const caseTable = (list, full = true) => `<div class="scroll"><table class="tbl"><thead><tr><th>级别</th><th>案例</th><th>系统</th><th>发生时间</th>${full ? '<th>主因</th>' : ''}<th>结论</th><th class="r">操作</th></tr></thead><tbody>${list.map((x) => `<tr><td><span class="tag line">${esc(x.severity)}</span></td><td class="title" title="${esc(x.title)}"><a href="#" data-case="${esc(x.id)}">${esc(x.title)}</a></td><td>${esc(x.system)}</td><td class="num">${esc(x.occurred_at)}</td>${full ? `<td class="q" title="${esc(x.primary_factor)}">${esc(x.primary_factor)}</td>` : ''}<td>${esc(x.conclusion)}</td><td class="ops"><a href="#" data-case="${esc(x.id)}">查看</a></td></tr>`).join('')}</tbody></table></div>`;
    if (S.kbTab === 'cases') return top + `<div class="row">${panel('全部案例', '按发生时间，点击查看现场、判断、处置与复发标志', caseTable(cases), '', 's12', true)}</div>`;
    if (S.kbTab === 'rules') return top + `<div class="row">${panel('全部条款', '规范、标准与制度中可引用的条文', `<div class="scroll"><table class="tbl"><thead><tr><th>编号</th><th>条文</th><th>理由</th><th>级别</th><th>出处</th></tr></thead><tbody>${rules.map((r) => `<tr><td class="id">${esc(r.id)}</td><td class="wrap">${esc(r.rule)}</td><td class="t2 wrap">${esc(r.rationale)}</td><td>${r.severity === 'error' ? '<span class="tag" data-lv="2">必须</span>' : '<span class="tag gray">建议</span>'}</td><td class="t2 wrap">${esc(r.group)}<br><span class="t3">${esc(r.source)}</span></td></tr>`).join('')}</tbody></table></div>`, '', 's12', true)}</div>`;
    if (S.kbTab === 'graph') return top + `<div class="row">${panel('关系图', '案例、现象、根因、处置与引用的条款；鼠标停在节点上，高亮它所在的整条链', graphSvg(cat, rules))}</div>`;
    const misses = h.misses || [];
    const total = (+c['docs.case'] || 0) + (+c['docs.rule'] || 0);
    const stats = `<div class="stats"><div><div class="l">知识条目</div><div class="v">${fmtInt(total)}<small>条</small></div><div class="s">案例 ${fmtInt(c['docs.case'])}，条款 ${fmtInt(c['docs.rule'])}</div></div>
      <div><div class="l">原始工单</div><div class="v">${fmtInt(c['docs.raw'])}<small>份</small></div><div class="s">知识条目由它们整理而来</div></div>
      <div><div class="l">向量覆盖</div><div class="v">${cov ?? '—'}<small>%</small></div><div class="s">${esc((st.vector || '').replace(/\(.*\)/, ''))}；未覆盖的只能靠关键词命中</div></div>
      <div><div class="l">关系图</div><div class="v">${cat.edges.length}<small>条已确认的边</small></div><div class="s">存储为${esc(st.graph)}</div></div></div>`;
    const qTable = `<div class="scroll"><table class="tbl"><thead><tr><th>时间</th><th>检索内容</th><th class="r">命中案例</th><th class="r">命中条款</th><th>方式</th></tr></thead><tbody>${D.kb.queries.map((q) => `<tr><td class="num t2">${esc(stamp(q.at))}</td><td>${esc(q.q)}</td><td class="r">${q.hits_cases}</td><td class="r">${q.hits_rules}</td><td>${q.how === 'semantic' ? '<span class="tag blue">语义</span>' : '<span class="tag gray">关键词</span>'}</td></tr>`).join('')}</tbody></table></div>`;
    const missFig = { dims: { rows: misses.map((m) => [m.code, String(m.n)]), headers: ['发现代码', '次数'] } };
    const health = `<div class="list">${(h.file_warnings || []).map((x) => `<div class="it">${glyph(1)}<div class="m"><b>文件格式有问题</b><small>${esc(x)}</small></div></div>`).join('')}${cov !== null && cov < 95 ? `<div class="it">${glyph(1)}<div class="m"><b>向量覆盖 ${cov}%</b><small>没覆盖的条目只能靠关键词命中，管理员在导入环境重建索引即可</small></div></div>` : ''}${!issues ? `<div class="it">${glyph(0)}<div class="m"><b>没有待处理项</b></div></div>` : ''}</div>`;
    return top + `<div class="row">${panel('概况', '', stats)}</div>
      <div class="row">${panel('最近的案例', '按发生时间', caseTable(cases.slice(0, 5), false), '<a href="#" data-kbtab="cases">查看全部</a>', 's8', true)}${panel('健康自检', '降级即发现', health, '', 's4')}</div>
      <div class="row">${panel('最近检索', '本人，近 7 天', qTable, '', 's8', true)}${panel('缺口清单', '诊断发现在库里查不到知识的次数', misses.length ? hb(missFig.dims, 1, '次') : '<p class="t3">没有缺口</p>', '', 's4')}</div>`;
  }
  function graphSvg(cat, rules) {
    const cols = [['case', '案例'], ['symptom', '现象'], ['rootcause', '根因'], ['action', '处置'], ['rule', '条款']];
    const nodes = new Map(), links = [], chain = new Map(), rt = new Map(rules.map((r) => [r.id, r.rule]));
    const add = (k, id, label) => { if (!nodes.has(id)) nodes.set(id, { k, id, label }); };
    const mark = (c, ids) => { const s = chain.get(c) || new Set(); ids.forEach((i) => s.add(i)); chain.set(c, s); };
    cat.edges.forEach((e) => { add(e.src.kind, e.src.id, e.src.label); add(e.dst.kind, e.dst.id, e.dst.label); links.push([e.src.id, e.dst.id, e.case]); mark(e.case, [e.src.id, e.dst.id]); });
    cat.cases.forEach((c) => (c.rules || []).forEach((r) => { const rid = 'rule:' + r; add('rule', rid, `${r} ${rt.get(r) || ''}`); links.push(['case:' + c.id, rid, c.id]); mark(c.id, [rid, 'case:' + c.id]); }));
    const W = 1200, NW = 204, NH = 52, GAP = 16, TOP = 34, pos = new Map(), colX = (i) => i * ((W - NW) / (cols.length - 1));
    let H = 0;
    cols.forEach(([k], ci) => { const list = [...nodes.values()].filter((n) => n.k === k); list.forEach((n, i) => pos.set(n.id, { x: colX(ci), y: TOP + i * (NH + GAP) })); H = Math.max(H, TOP + list.length * (NH + GAP)); });
    const clip = (s, n) => (String(s).length > n ? String(s).slice(0, n - 1) + '…' : String(s));
    const path = (a, b) => { const p = pos.get(a), q = pos.get(b); if (!p || !q) return ''; const x1 = p.x + NW, y1 = p.y + NH / 2, x2 = q.x, y2 = q.y + NH / 2, m = (x1 + x2) / 2; return `M${x1},${y1} C${m},${y1} ${m},${y2} ${x2},${y2}`; };
    const casesOf = (id) => [...chain.entries()].filter(([, s]) => s.has(id)).map(([c]) => c).join('|');
    const kn = Object.fromEntries(cols);
    return `<svg class="graph" viewBox="0 0 ${W} ${H}" id="kbgraph">${cols.map(([, l], i) => `<text class="colh" x="${colX(i)}" y="14">${l}</text>`).join('')}
      ${links.map(([a, b, c]) => `<path d="${path(a, b)}" data-case="${esc(c)}"/>`).join('')}
      ${[...nodes.values()].map((n) => { const p = pos.get(n.id); return p ? `<g class="node" data-cases="${esc(casesOf(n.id))}" transform="translate(${p.x},${p.y})"><title>${esc(n.label)}</title><rect width="${NW}" height="${NH}" rx="6"/><text class="k" x="12" y="20">${esc(kn[n.k] || n.k)}</text><text x="12" y="39">${esc(clip(n.label, 15))}</text></g>` : ''; }).join('')}</svg>`;
  }
  function openCase(id) {
    const x = D.kb.catalog.cases.find((c) => c.id === id); if (!x) return;
    const sec = Object.entries(x.sections || {}).map(([k, v]) => `<div class="sec">${esc(k)}</div><p style="margin:0" class="t2">${esc(v)}</p>`).join('');
    drawer(esc(x.title), `<span class="tag line">${esc(x.severity)}</span>`, `<div class="kvs" style="--cols:3"><div><div class="kv"><div class="k">系统</div><div class="v">${esc(x.system)}</div></div></div><div><div class="kv"><div class="k">发生时间</div><div class="v">${esc(x.occurred_at)}</div></div></div><div><div class="kv"><div class="k">结论</div><div class="v">${esc(x.conclusion)}</div></div></div></div>
      <div class="sec">主因</div><p style="margin:0">${esc(x.primary_factor)}</p>${sec}
      <div class="sec">依据条款</div><p style="margin:0">${(x.rules || []).map((r) => `<span class="tag gray" style="margin-right:6px">${esc(r)}</span>`).join('')}</p>
      <div class="sec">出处</div><p style="margin:0" class="t3">${esc(x.source)}</p>`, '<button class="btn" data-close>关闭</button>');
  }

  /* ------------------------------------------------------------ 外壳 */
  const PAGES = { health: renderHealth, topsql: renderTopsql, wdr: renderWdr, kb: renderKb };
  const instLevel = (k) => { const c = D.health[k]; if (!c) return null; const e = c.index.filter((x) => !x.demo); return e.length ? entryLevel(e[e.length - 1]) : null; };
  function chrome() {
    const t = D.targets.find((x) => x.key === S.key) || {};
    $('#inst').innerHTML = `${glyph(instLevel(S.key))}<span class="t3">实例</span>${esc(t.label || S.key)}${I.chev}`;
    $$('#nav a[data-p]').forEach((a) => a.classList.toggle('on', a.dataset.p === S.page));
  }
  function render() { chrome(); $('#page').innerHTML = PAGES[S.page](); }
  function route() { const p = (location.hash.match(/^#\/(\w+)/) || [])[1]; S.page = PAGES[p] ? p : 'health'; render(); window.scrollTo(0, 0); }
  function drawer(title, mark, body, foot) {
    $('#drawer').innerHTML = `<div class="dh"><h2>${title}</h2>${mark || ''}<button class="icon-btn" data-close aria-label="关闭">${I.close}</button></div><div class="db">${body}</div>${foot ? `<div class="df">${foot}</div>` : ''}`;
    $('#drawer').classList.add('on'); $('#scrim').classList.add('on');
  }
  function closeAll() { $('#drawer').classList.remove('on'); $('#scrim').classList.remove('on'); $('#menu').classList.remove('on'); }
  function toast(msg) { const t = $('#toast'); t.innerHTML = `${I.info}<span>${esc(msg)}</span>`; t.classList.add('on'); clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove('on'), 3600); }
  function menu(anchor, html, right) {
    const m = $('#menu'), r = anchor.getBoundingClientRect();
    m.innerHTML = html; m.classList.add('on');
    m.style.top = r.bottom + 4 + window.scrollY + 'px';
    m.style.left = (right ? r.right - m.offsetWidth : r.left) + window.scrollX + 'px';
  }
  document.addEventListener('click', (ev) => {
    const el = ev.target.closest('[data-dig],[data-run],[data-by],[data-sql],[data-dim],[data-views] button,[data-kbtab],[data-case],[data-close],[data-copy],[data-toast],#scrim,#inst,#run-btn,[data-inst],[data-pick]');
    if (!el) { if (!ev.target.closest('#menu')) $('#menu').classList.remove('on'); return; }
    ev.preventDefault();
    if (el.dataset.dig !== undefined) { const p = el.dataset.dig; toast(`原型：真实环境会在新标签页打开本人的 opencode 会话，并带上「${p.slice(0, 36)}${p.length > 36 ? '…' : ''}」`); return; }
    if (el.dataset.run) { const [sk, f] = el.dataset.run.split('|'); S.run[sk + ':' + S.key] = f; render(); return; }
    if (el.dataset.by) { S.by = el.dataset.by; render(); return; }
    if (el.dataset.sql) { openSql(el.dataset.sql); return; }
    if (el.dataset.dim !== undefined) { openDim(+el.dataset.dim); return; }
    if (el.dataset.kbtab) { S.kbTab = el.dataset.kbtab; render(); return; }
    if (el.dataset.case) { openCase(el.dataset.case); return; }
    if (el.dataset.copy) { if (navigator.clipboard) navigator.clipboard.writeText(el.dataset.copy); toast(`已复制 ${el.dataset.copy}`); return; }
    if (el.dataset.toast) { toast(el.dataset.toast); return; }
    if (el.dataset.close !== undefined || el.id === 'scrim') { closeAll(); return; }
    if (el.dataset.inst) { S.key = el.dataset.inst; closeAll(); render(); return; }
    if (el.dataset.pick) { const [sk, f] = el.dataset.pick.split('|'); S.run[sk + ':' + S.key] = f; closeAll(); render(); return; }
    if (el.id === 'inst') { menu(el, `<div class="mh">切换实例（状态为最近一次巡检）</div>${D.targets.map((t) => { const l = instLevel(t.key); return `<div class="mi ${t.key === S.key ? 'on' : ''}" data-inst="${esc(t.key)}">${glyph(l)}${esc(t.label)}<span class="r">${esc(lname(l))},${esc(stamp(t.last_at))}</span></div>`; }).join('')}`); return; }
    if (el.id === 'run-btn') {
      const sk = el.dataset.skill, cur = current(sk, S.key);
      menu(el, `<div class="mh">${sk === 'wdr' ? '分析过的窗口' : '巡检报告'}，最近的在上</div>${[...D[sk][S.key].index].reverse().map((e) => e.demo ? `<div class="mi dis">${glyph(entryLevel(e))}<span class="num">${esc(stamp(e.at))}</span><span class="r">示意</span></div>` : `<div class="mi ${cur && cur.entry.file === e.file ? 'on' : ''}" data-pick="${esc(sk)}|${esc(e.file)}">${glyph(entryLevel(e))}<span class="num">${esc(stamp(e.at))}</span><span class="r">${esc(lname(entryLevel(e)))}</span></div>`).join('')}`, true);
      return;
    }
    const v = el.closest('[data-views]');
    if (v) { $$('button', v).forEach((b) => b.classList.toggle('on', b === el)); const box = v.closest('.panel'); $$('.v-chart', box).forEach((x) => { x.hidden = el.dataset.v !== 'chart'; }); $$('.v-table', box).forEach((x) => { x.hidden = el.dataset.v !== 'table'; }); }
  });
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') closeAll(); });
  const tip = $('#tip');
  document.addEventListener('mousemove', (ev) => {
    const el = ev.target.closest('[data-tip]');
    if (!el) { tip.classList.remove('on'); return; }
    tip.innerHTML = el.dataset.tip;
    tip.style.left = Math.min(ev.clientX + 14, window.innerWidth - tip.offsetWidth - 12) + 'px';
    tip.style.top = Math.min(ev.clientY + 16, window.innerHeight - tip.offsetHeight - 12) + 'px';
    tip.classList.add('on');
  });
  document.addEventListener('mouseover', (ev) => {
    const svg = $('#kbgraph'); if (!svg) return;
    const g = ev.target.closest && ev.target.closest('#kbgraph .node'), cs = g ? g.dataset.cases.split('|').filter(Boolean) : [];
    $$('.node', svg).forEach((n) => n.classList.toggle('hl', cs.some((c) => n.dataset.cases.split('|').includes(c))));
    $$('path', svg).forEach((p) => p.classList.toggle('hl', cs.includes(p.dataset.case)));
  });

  $('#who').textContent = D.whoami.user_id;
  $('#av').textContent = D.whoami.user_id.slice(0, 1).toUpperCase();
  const q = new URLSearchParams(location.search);
  if (q.get('inst')) S.key = q.get('inst');
  if (q.get('by')) S.by = q.get('by');
  if (q.get('tab')) S.kbTab = q.get('tab');
  window.addEventListener('hashchange', route);
  route();
  // 只为截图验收：?dim=<序号> 打开维度抽屉，?sql=<id> 打开 SQL 详情，?case=<id> 打开案例
  if (q.get('dim') && S.page === 'health') openDim(+q.get('dim'));
  if (q.get('sql')) openSql(q.get('sql'));
  if (q.get('case')) openCase(q.get('case'));
})();
