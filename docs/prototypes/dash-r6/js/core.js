/* R6 共用:数据访问、级别、格式化、页面部件。口径与 R5 / frontend/ 一致,页面只摆放,不改级别。
   各页面模块挂在 window.R6 上;本文件最先加载。 */
(function () {
  'use strict';
  const R = (window.R6 = { pages: {} });
  const D = (R.D = window.DASH);
  R.$ = (s, r = document) => r.querySelector(s);
  R.$$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (R.esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])));

  /* ------------------------------------------------------------ 级别:四档 + 采集失败,形状各不相同 */
  const LVN = ['正常', '关注', '告警', '严重'];
  const G = {
    0: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="7" fill="currentColor"/><path d="m5.1 8.1 2 2 3.9-4" fill="none" stroke="#fff" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    1: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="7" fill="currentColor"/><path d="M8 4.6v4.1" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/><circle cx="8" cy="11.2" r=".95" fill="#fff"/></svg>',
    2: '<svg viewBox="0 0 16 16"><path d="M7.1 1.9a1 1 0 0 1 1.8 0l6.2 11.2a1 1 0 0 1-.9 1.5H1.8a1 1 0 0 1-.9-1.5z" fill="currentColor"/><path d="M8 6v3.6" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/><circle cx="8" cy="11.8" r=".9" fill="#fff"/></svg>',
    3: '<svg viewBox="0 0 16 16"><path d="M5.2 1h5.6L15 5.2v5.6L10.8 15H5.2L1 10.8V5.2z" fill="currentColor"/><path d="m5.8 5.8 4.4 4.4m0-4.4-4.4 4.4" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/></svg>',
    na: '<svg viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6.4" stroke="currentColor" stroke-width="1.5" stroke-dasharray="2.4 2"/><path d="M5.4 8h5.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  };
  const lk = (R.lk = (l) => (l === null || l === undefined ? 'na' : String(Math.max(0, Math.min(3, l | 0)))));
  const lname = (R.lname = (l) => (l === null || l === undefined ? '采集失败' : LVN[l | 0] || '正常'));
  const glyph = (R.glyph = (l) => `<span class="glyph" data-lv="${lk(l)}">${G[lk(l)]}</span>`);
  R.status = (l, text) => `<span class="status" data-lv="${lk(l)}">${glyph(l)}${esc(text ?? lname(l))}</span>`;
  R.tag = (l, text) => `<span class="tag" data-lv="${lk(l)}">${glyph(l)}${esc(text ?? lname(l))}</span>`;

  const sv = (d, w = 1.9) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
  R.I = {
    chev: sv('<path d="m6 9 6 6 6-6"/>', 2),
    chat: sv('<path d="M4 5h16v11H9l-5 4z"/>'),
    redo: sv('<path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5"/>'),
    close: sv('<path d="M6 6l12 12M18 6 6 18"/>'),
    down: sv('<path d="M12 4v12m-5-5 5 5 5-5M5 20h14"/>'),
    info: sv('<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><circle cx="12" cy="8" r=".6" fill="currentColor"/>'),
    cal: sv('<rect x="3.5" y="5" width="17" height="15" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/>', 1.8),
    search: sv('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>', 2),
    send: sv('<path d="M5 12h13M13 6l6 6-6 6"/>', 2.2),
    arrow: sv('<path d="M5 12h14M13 6l6 6-6 6"/>', 2),
    ext: sv('<path d="M8 16 16 8M9 8h7v7"/>', 2),
    spark: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2.5c.5 4.6 2.9 7 7.5 7.5-4.6.5-7 2.9-7.5 7.5-.5-4.6-2.9-7-7.5-7.5 4.6-.5 7-2.9 7.5-7.5z"/><path d="M19 15.5c.2 1.8 1.1 2.7 2.9 2.9-1.8.2-2.7 1.1-2.9 2.9-.2-1.8-1.1-2.7-2.9-2.9 1.8-.2 2.7-1.1 2.9-2.9z" opacity=".7"/></svg>',
    up: '<svg viewBox="0 0 12 12" width="10" height="10"><path d="M6 2.5 10 8H2z" fill="currentColor"/></svg>',
    dn: '<svg viewBox="0 0 12 12" width="10" height="10"><path d="M6 9.5 2 4h8z" fill="currentColor"/></svg>',
    sortd: '<svg width="10" height="10" viewBox="0 0 12 12" style="margin-left:4px;vertical-align:-1px"><path d="M6 9.5 2 4h8z" fill="currentColor"/></svg>',
    pulse: sv('<path d="M3 12h4l2.5-6 4 12 2.5-6H21"/>', 1.7),
    list: sv('<path d="M4 6h16M4 12h11M4 18h6"/>', 1.7),
    clock: sv('<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3.5 2"/>', 1.7),
    book: sv('<path d="M5 4.5h10a3 3 0 0 1 3 3V20H8a3 3 0 0 1-3-3z"/><path d="M5 17a3 3 0 0 1 3-3h10"/>', 1.7),
    db: sv('<ellipse cx="12" cy="5.5" rx="7.5" ry="3"/><path d="M4.5 5.5v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-6"/><path d="M4.5 11.5v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-6"/>', 1.8),
    code: sv('<path d="m8 8-4 4 4 4M16 8l4 4-4 4"/>', 1.8),
    grid: sv('<rect x="4" y="4" width="7" height="7" rx="2"/><rect x="13" y="4" width="7" height="7" rx="2"/><rect x="4" y="13" width="7" height="7" rx="2"/><rect x="13" y="13" width="7" height="7" rx="2"/>', 1.7),
  };

  /* ------------------------------------------------------------ 格式化(与 frontend/lib/fmt.js 同口径) */
  const num = (R.num = (v) => { const n = parseFloat(String(v ?? '').replace(/,/g, '')); return Number.isFinite(n) ? n : null; });
  const fmtInt = (R.fmtInt = (n) => { n = Number(n); if (!Number.isFinite(n)) return '—'; if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + ' 亿'; if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(1) + ' 万'; return n.toLocaleString('en-US'); });
  R.fmtSec = (s) => { s = Number(s); if (!Number.isFinite(s)) return '—'; if (s >= 3600) return (s / 3600).toFixed(1) + ' h'; if (s >= 60) return (s / 60).toFixed(1) + ' min'; return s.toFixed(s < 10 ? 1 : 0) + ' s'; };
  R.fmtMs = (ms) => { ms = Number(ms); if (!Number.isFinite(ms)) return '—'; return ms >= 1000 ? (ms / 1000).toFixed(1) + ' s' : ms.toFixed(1) + ' ms'; };
  R.split = (s) => { const m = /^(-?[\d.,]+)\s*(.*)$/.exec(String(s)); return m ? [m[1], m[2]] : [String(s), '']; };
  const TZ = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
  const parts = (d) => { const p = TZ.formatToParts(d), g = (t) => (p.find((x) => x.type === t) || {}).value; return { mo: g('month'), d: g('day'), h: g('hour'), mi: g('minute') }; };
  const toDate = (s) => { const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(s || ''); return m ? new Date(Date.UTC(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6])) : new Date(s); };
  R.stamp = (s) => { const d = toDate(s); if (isNaN(d)) return String(s || ''); const p = parts(d); return `${p.mo}-${p.d} ${p.h}:${p.mi}`; };
  R.day = (s) => { const d = toDate(s); if (isNaN(d)) return ''; const p = parts(d); return `${p.mo}-${p.d}`; };
  R.colFmt = (rows, i) => { const mx = Math.max(0, ...rows.map((r) => Math.abs(num(r[i]) || 0))); return (c) => { const n = num(c); if (!/^-?[\d.]+$/.test(String(c)) || n === null) return String(c); return mx >= 1e6 ? fmtInt(n) : Number.isInteger(n) ? n.toLocaleString('en-US') : n.toLocaleString('en-US', { maximumFractionDigits: 2 }); }; };
  const pangu = (R.pangu = (s) => String(s).replace(/([㐀-鿿])([A-Za-z0-9])/g, '$1 $2').replace(/([A-Za-z0-9%])([㐀-鿿])/g, '$1 $2'));
  /** 报告里的中文句子常混着半角标点:挨着汉字的 , : ; ( ) 换成全角,再补中英文间距。 */
  const FW = { ',': '，', ':': '：', ';': '；' };
  R.cn = (s) => pangu(String(s ?? '')
    .replace(/([㐀-鿿）])\s*([,:;])\s*/g, (m, a, p) => a + FW[p])
    .replace(/\s*([,:;])\s*(?=[㐀-鿿（])/g, (m, p) => FW[p])
    .replace(/\(([^()]*[㐀-鿿][^()]*)\)/g, '（$1）'));
  R.joinCn = (xs) => (xs.length <= 1 ? xs.join('') : xs.slice(0, -1).join('、') + '和' + xs[xs.length - 1]);

  /* ------------------------------------------------------------ 状态与报告 */
  R.S = { page: 'health', key: 'og5', run: {}, by: null, kbTab: 'overview', chat: [] };
  const real = (R.real = (skill, key) => { const x = D[skill] && D[skill][key]; return x ? x.index.filter((e) => !e.demo && x.runs[e.file]) : []; });
  R.current = (skill, key) => {
    const ents = real(skill, key);
    if (!ents.length) return null;
    const f = R.S.run[skill + ':' + key], e = ents.find((x) => x.file === f) || ents[ents.length - 1], i = ents.indexOf(e);
    return { entry: e, data: D[skill][key].runs[e.file], index: D[skill][key].index, real: ents, pos: i, prev: i > 0 ? { entry: ents[i - 1], data: D[skill][key].runs[ents[i - 1].file] } : null };
  };
  R.entryLevel = (e) => { const dm = e && e.dims; if (dm && dm.total && dm.na === dm.total) return null; return e ? e.overall | 0 : null; };
  R.repLevel = (r) => { const ds = (r && r.dims) || []; if (ds.length && ds.every((d) => d.available === false)) return null; return r ? r.overall | 0 : null; };
  R.tipAttr = (h) => ` data-tip="${esc(h)}"`;
  R.dig = (p) => ` data-dig="${esc(p)}"`;
  R.ask = (p) => ` data-ask="${esc(p)}"`;
  R.parseCmp = (t) => { const m = /^\s*(>=|>|<=|<)\s*([\d,.]+)\s*(%|ms|s|MB|GB|MiB|GiB)?\s*$/.exec(String(t || '')); return m ? { op: m[1], v: num(m[2]) } : null; };
  R.parseVal = (t) => { const m = /^\s*([\d,.]+)\s*(%|ms|s|MB|GB|MiB|GiB)?\s*$/.exec(String(t || '')); return m ? num(m[1]) : null; };
  /** 发现值是阈值的几倍;组合条件或非「大于」型阈值返回 null。 */
  R.times = (f) => { const thr = R.parseCmp(f.threshold), v = R.parseVal(f.value); return thr && v !== null && thr.v && thr.op[0] === '>' ? v / thr.v : null; };

  /* ------------------------------------------------------------ 页面部件 */
  let seq = 0;
  R.resetSeq = () => { seq = 0; };
  /** 编排入场:每个块带一个序号,CSS 用它错开出现时间。 */
  R.rise = () => ` style="--i:${seq++}"`;
  R.tile = (o) => `<section class="tile ${o.span || 'b12'} rise ${o.cls || ''}"${R.rise()}>${o.title ? `<div class="tile-h"><h3>${o.title}</h3>${o.desc ? `<span class="desc">${o.desc}</span>` : ''}<div class="tools">${o.tools || ''}</div></div>` : ''}<div class="tile-b ${o.flush ? 'flush' : ''}">${o.body}</div></section>`;
  R.pageHead = (crumb, statusTag, actions, desc) => `<div class="crumb">诊断大盘<span>/</span><b>${esc(crumb)}</b></div>
    <div class="page-head"><h1>${esc(crumb)}</h1>${statusTag || ''}<div class="actions">${actions || ''}</div></div>
    <div class="page-desc">${desc.map(([k, v]) => `<span>${esc(k)}：<b>${v}</b></span>`).join('')}</div>`;
  R.runPicker = (skill, noun) => { const cur = R.current(skill, R.S.key); return cur ? `<button class="btn" id="run-btn" data-skill="${skill}">${R.I.cal}${esc(noun)} ${esc(R.stamp(cur.entry.at))}${R.I.chev}</button>` : ''; };
  R.primary = (p, label) => `<button class="btn primary"${R.dig(p)}>${R.I.chat}${esc(label)}</button>`;
  /** 滚动数字:元素里先放最终文字(截图、关动效时就是它),入场时由 shell 从 0 滚到它。 */
  R.cu = (text) => `<span data-count>${esc(text)}</span>`;
  /** 智能体解读卡:结论、要点(带证据小签)、建议追问、提问框。文字由报告确定性生成,不另外编数。 */
  R.insight = (o) => `<div class="glass insight rise"${R.rise()}>
      <div class="who"><span class="orb">${R.I.spark}</span><b>智能体解读</b><span class="src">${esc(o.src)}</span></div>
      <h2>${o.head}</h2>
      <ul class="lines">${o.lines.filter(Boolean).map((l) => `<li><span>${l}</span></li>`).join('')}</ul>
      <div class="chips">${o.chips.map(([l, p]) => `<button class="chip-ask"${R.ask(p)}>${R.I.spark}${esc(l)}</button>`).join('')}</div>
      <form class="askbox" data-askform>${R.I.spark}<input name="q" autocomplete="off" placeholder="${esc(o.ask)}"><button class="send" aria-label="发送">${R.I.send}</button></form>
    </div>`;
  R.heroStats = (items) => `<div class="hero-stats">${items.map((x) => `<div class="glass rise"${R.rise()}><div class="l">${x.l}</div><div class="v num">${x.v}<small>${esc(x.u || '')}</small></div><div class="s">${x.s || ''}</div></div>`).join('')}</div>`;
  R.emptyHero = (title, text, prompt, label) => `<section class="hero rise"${R.rise()}><div class="empty-state">${R.glyph(null)}<h4>${esc(title)}</h4><p>${esc(text)}</p>${R.primary(prompt, label)}</div></section>`;
})();
