/* R6 图形部件:维度圆环、巡检节奏条、耗时树图、对比哑铃、调用×耗时散点、覆盖环、关系图。
   全部是确定性的 SVG / 绝对定位,数据只来自报告;示意条目画成空心且点不开。 */
(function (R) {
  'use strict';
  const { D, esc, lk, lname, tipAttr, stamp, day, fmtInt, fmtMs, fmtSec } = R;
  const f1 = (v) => v.toFixed(1);

  /** 维度圆环:每个检查项一段;异常段用级别色,正常段素色,采集失败为虚线。中心是总体级别。 */
  R.ring = (segs, lvl, sub) => {
    const C = 120, RA = 94, SW = 18, n = segs.length, per = (2 * Math.PI) / n, gap = n > 1 ? 0.045 : 0;
    const pt = (a) => `${f1(C + RA * Math.cos(a))},${f1(C + RA * Math.sin(a))}`;
    const arcs = segs.map((s, i) => {
      const a0 = -Math.PI / 2 + i * per + gap / 2, a1 = a0 + per - gap, len = f1(RA * (a1 - a0));
      const stroke = s.lv === '0' ? 'var(--h-calm)' : 'var(--hc)';
      return `<path class="arc${s.lv === 'na' ? ' na' : ''}" data-lv="${s.lv}" d="M${pt(a0)} A${RA},${RA} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${pt(a1)}" stroke="${stroke}" stroke-width="${SW}" style="--len:${len};--i:${i}"${s.attr}${tipAttr(s.tip)}/>`;
    }).join('');
    return `<svg class="ring" viewBox="0 0 240 240" role="img" aria-label="总体${esc(lname(lvl))}">
      <circle class="track" cx="${C}" cy="${C}" r="${RA}" fill="none" stroke-width="${SW + 10}"/>
      <circle class="core" cx="${C}" cy="${C}" r="${RA - 22}"/>${arcs}
      <text class="lvl" data-lv="${lk(lvl)}" x="${C}" y="${C + 4}" text-anchor="middle">${esc(lname(lvl))}</text>
      <text class="sub" x="${C}" y="${C + 30}" text-anchor="middle">${esc(sub)}</text></svg>`;
  };

  /** 巡检节奏:上面是发现条数的平滑面积线,下面一排圆角格是每次的级别,两者按同一横坐标对齐。 */
  R.rhythm = (skill, key, curFile, W = 360) => {
    const idx = D[skill][key].index;
    if (!idx.length) return '<p class="t3">还没有历史</p>';
    const n = idx.length, H = 150, T = 18, B = 82, CY = 96, slot = W / n, cs = Math.min(22, slot - 5);
    const tot = (e) => Object.values(e.counts || {}).reduce((a, b) => a + (+b || 0), 0);
    const mx = Math.max(2, ...idx.map(tot)), X = (i) => slot * i + slot / 2, Y = (v) => B - (v / mx) * (B - T);
    const pts = idx.map((e, i) => [X(i), Y(tot(e))]);
    const curve = pts.map(([x, y], i) => (i ? `C${f1((pts[i - 1][0] + x) / 2)},${f1(pts[i - 1][1])} ${f1((pts[i - 1][0] + x) / 2)},${f1(y)} ${f1(x)},${f1(y)}` : `M${f1(x)},${f1(y)}`)).join(' ');
    const gid = 'tg-' + skill;
    const cols = idx.map((e, i) => {
      const l = R.entryLevel(e), lv = lk(l), cur = e.file === curFile, x = X(i), c = e.counts || {};
      const fill = e.demo ? 'none' : lv === '0' ? 'var(--gray-mark)' : lv === 'na' ? 'none' : 'var(--c)';
      const stroke = e.demo ? (lv === '0' ? 'var(--line-2)' : 'var(--c)') : lv === 'na' ? 'var(--na)' : 'none';
      const tip = `<b>${esc(stamp(e.at))}</b>${e.demo ? '<span class="demo-mark">示意</span>' : ''}<div class="d">${esc(lname(l))}；严重 ${+c['3'] || 0}，告警 ${+c['2'] || 0}，关注 ${+c['1'] || 0}${e.dims && e.dims.na ? `；未采集 ${e.dims.na}/${e.dims.total} 个维度` : ''}</div>`;
      const dot = e.demo ? `<circle cx="${f1(x)}" cy="${f1(pts[i][1])}" r="2.5" fill="var(--panel)" stroke="var(--t4)" stroke-width="1.2"/>`
        : `<circle class="dot" cx="${f1(x)}" cy="${f1(pts[i][1])}" r="${cur ? 4.5 : 3.5}"/>`;
      return `<g data-lv="${lv}"${e.demo ? '' : ` data-run="${esc(skill)}|${esc(e.file)}" style="cursor:pointer"`}${tipAttr(tip)}>
        <rect x="${f1(x - slot / 2)}" y="0" width="${f1(slot)}" height="${H}" fill="transparent"/>${dot}
        ${cur ? `<rect x="${f1(x - cs / 2 - 3.5)}" y="${CY - 3.5}" width="${f1(cs + 7)}" height="${f1(cs + 7)}" rx="9" fill="none" stroke="var(--primary)" stroke-width="1.6"/>` : ''}
        <rect class="cell" x="${f1(x - cs / 2)}" y="${CY}" width="${f1(cs)}" height="${f1(cs)}" rx="6" fill="${fill}" stroke="${stroke}" stroke-width="1.4"${lv === 'na' && !e.demo ? ' stroke-dasharray="3 2"' : ''}/></g>`;
    }).join('');
    const ci = idx.findIndex((e) => e.file === curFile);
    const labs = [...new Set([0, n - 1, ci].filter((i) => i >= 0))].map((i) => `<text x="${f1(Math.min(W - 18, Math.max(18, X(i))))}" y="${CY + cs + 18}" text-anchor="middle"${i === ci ? ' class="on"' : ''}>${esc(day(idx[i].at))}</text>`).join('');
    return `<svg class="rhythm" viewBox="0 0 ${W} ${H}">
      <defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(--primary);stop-opacity:.22"/><stop offset="1" style="stop-color:var(--primary);stop-opacity:0"/></linearGradient></defs>
      <line class="base" x1="0" x2="${W}" y1="${B}" y2="${B}"/><text x="0" y="10">发现条数，最多 ${mx}</text>
      <path d="${curve} L${f1(X(n - 1))},${B} L${f1(X(0))},${B} Z" fill="url(#${gid})"/>
      <path class="line" d="${curve}"/>${cols}${labs}</svg>
      <div class="legend"><span><i class="sw calm"></i>正常</span><span data-lv="1"><i class="sw"></i>关注</span><span data-lv="2"><i class="sw"></i>告警</span><span data-lv="3"><i class="sw"></i>严重</span><span><i class="sw hollow"></i>示意</span></div>`;
  };

  /** 方形化树图(squarified):面积 = 上榜总耗时占比。返回带 x/y/w/h 的新数组,不改入参。 */
  function squarify(items, box) {
    const out = [];
    let { x, y, w, h } = box, rest = items;
    while (rest.length) {
      const short = Math.min(w, h);
      const worst = (row) => { const s = row.reduce((a, b) => a + b.a, 0), mx = Math.max(...row.map((z) => z.a)), mn = Math.min(...row.map((z) => z.a)); return Math.max((short * short * mx) / (s * s), (s * s) / (short * short * mn)); };
      let k = 1;
      while (k < rest.length && worst(rest.slice(0, k + 1)) <= worst(rest.slice(0, k))) k++;
      const row = rest.slice(0, k), s = row.reduce((a, b) => a + b.a, 0);
      if (w >= h) { const cw = s / h; let cy = y; row.forEach((z) => { const ch = z.a / cw; out.push({ ...z, x, y: cy, w: cw, h: ch }); cy += ch; }); x += cw; w -= cw; }
      else { const rh = s / w; let cx = x; row.forEach((z) => { const cw = z.a / rh; out.push({ ...z, x: cx, y, w: cw, h: rh }); cx += cw; }); y += rh; h -= rh; }
      rest = rest.slice(k);
    }
    return out;
  }
  // 面积已经编码占比,颜色只区分业务 / 系统;两种底色由样式表的 --tm-biz / --tm-sys 给出
  const BIZ = 'var(--tm-biz)', SYS = 'var(--tm-sys)';
  R.treemap = (rows, tune) => {
    const VW = 1000, VH = 440, sum = rows.reduce((a, r) => a + (+r.total_sec || 0), 0) || 1;
    const items = [...rows].sort((a, b) => b.total_sec - a.total_sec).map((r) => ({ r, a: ((+r.total_sec || 0) / sum) * VW * VH }));
    return `<div class="treemap">${squarify(items, { x: 0, y: 0, w: VW, h: VH }).map((c, i) => {
      const r = c.r, sys = tune[r.sql_id] && tune[r.sql_id].skipped, pct = ((+r.total_sec || 0) / sum) * 100;
      const bg = sys ? SYS : BIZ;
      const size = c.w < 150 || c.h < 90 ? 'xs' : c.w < 260 || c.h < 140 ? 'sm' : '';
      const tip = `<b>${esc(r.sql_id)}</b>${sys ? '<span class="demo-mark">系统 SQL</span>' : ''}<div class="d">占上榜耗时 ${f1(pct)}%，总耗时 ${esc(fmtSec(r.total_sec))}</div><div class="d">调用 ${esc(fmtInt(r.calls))} 次，平均 ${esc(fmtMs(r.avg_ms))}</div>`;
      return `<button class="cell ${size}" data-sql="${esc(r.sql_id)}" style="--i:${i};left:calc(${f1((c.x / VW) * 100)}% + 3px);top:calc(${f1((c.y / VH) * 100)}% + 3px);width:calc(${f1((c.w / VW) * 100)}% - 6px);height:calc(${f1((c.h / VH) * 100)}% - 6px);background:${bg}"${tipAttr(tip)}>
        <span class="id">${esc(r.sql_id)}${sys && size !== 'xs' ? '<span class="sys">系统</span>' : ''}</span><span class="pct num">${f1(pct)}<small>%</small></span>
        <span class="q">${esc(r.query)}</span><span class="m num">${esc(fmtInt(r.calls))} 次 · 平均 ${esc(fmtMs(r.avg_ms))}</span></button>`;
    }).join('')}</div>`;
  };

  /** 对比哑铃:空心点是上一窗口,实心点是本窗口,连线颜色说明变好还是变坏。比例尺从 0 开始,不放大差异。 */
  R.dumbbell = (k, cur, prev, fmtPair) => {
    if (cur === null) return `<div class="db-row glass"><div class="top"><span class="n">${esc(k.l)}</span><span class="v t3">未采集</span></div></div>`;
    const W = 240, L = 8, PW = W - 16, mx = k.u === '%' ? 100 : Math.max(cur, prev || 0) * 1.1 || 1, X = (v) => L + (Math.max(0, v) / mx) * PW;
    const has = prev !== null && prev !== undefined, ch = has ? R.change(k, cur, prev) : null;
    const col = { bad: 'var(--h-crit)', worse: 'var(--h-warn)', better: 'var(--h-ok)', flat: 'var(--h-t3)' }[ch ? ch.cls : 'flat'];
    const [cv, cu] = fmtPair(cur, k.u), [pv, pu] = has ? fmtPair(prev, k.u) : ['', ''];
    const sp = (u) => (u === '%' ? u : u ? ' ' + u : '');
    return `<div class="db-row glass rise"${R.rise()}${tipAttr(`<b>${esc(k.l)}</b><div class="d">本窗口 ${esc(cv + sp(cu))}</div>${has ? `<div class="d">上一窗口 ${esc(pv + sp(pu))}</div>` : ''}`)}>
      <div class="top"><span class="n">${esc(k.l)}</span><span class="v num">${R.cu(cv)}<small>${esc(cu)}</small></span></div>
      <svg viewBox="0 0 ${W} 22"><line class="trk" x1="${L}" x2="${W - L}" y1="11" y2="11" stroke-width="4" stroke-linecap="round"/>
        ${has ? `<line class="seg" x1="${f1(X(prev))}" x2="${f1(X(cur))}" y1="11" y2="11" stroke="${col}" stroke-width="4" stroke-linecap="round"/><circle class="pv" cx="${f1(X(prev))}" cy="11" r="5" stroke-width="2"/>` : ''}
        <circle class="cv" cx="${f1(X(cur))}" cy="11" r="6" fill="${col}" stroke-width="1.5"/></svg>
      <div class="cap"><span>${has ? `上一窗口 ${esc(pv + sp(pu))}` : '没有可对比的窗口'}</span>${ch ? `<span class="chg ${ch.cls}">${ch.dir > 0 ? R.I.up : ch.dir < 0 ? R.I.dn : ''}${esc(ch.text)}</span>` : ''}</div></div>`;
  };

  /** 调用次数 × 平均耗时(双对数),气泡面积 = 总耗时。业务 SQL 用品牌蓝,系统 SQL 灰。 */
  R.scatter = (rows, tune, W = 640) => {
    const H = 310, L = 58, Rt = 24, T = 34, B = 44, pw = W - L - Rt, ph = H - T - B, PAD = 0.35;
    const lg = (v) => Math.log10(Math.max(v, 1e-3));
    const xs = rows.map((r) => lg(+r.calls || 1)), ys = rows.map((r) => lg(+r.avg_ms || 0.01));
    const x0 = Math.floor(Math.min(...xs) - PAD), x1 = Math.ceil(Math.max(...xs) + PAD), y0 = Math.floor(Math.min(...ys) - PAD), y1 = Math.ceil(Math.max(...ys) + PAD);
    const X = (v) => L + ((v - x0) / Math.max(1, x1 - x0)) * pw, Y = (v) => T + ph - ((v - y0) / Math.max(1, y1 - y0)) * ph;
    const mt = Math.max(...rows.map((r) => +r.total_sec || 0)) || 1;
    const lab = (e) => (e >= 4 ? fmtInt(10 ** e) : e >= 0 ? String(10 ** e) : String(+(10 ** e).toFixed(3)));
    const gx = Array.from({ length: x1 - x0 + 1 }, (_, i) => x0 + i).map((e) => `<line x1="${f1(X(e))}" x2="${f1(X(e))}" y1="${T}" y2="${T + ph}"/><text x="${f1(X(e))}" y="${T + ph + 18}" text-anchor="middle">${lab(e)}</text>`).join('');
    const gy = Array.from({ length: y1 - y0 + 1 }, (_, i) => y0 + i).map((e) => `<line x1="${L}" x2="${L + pw}" y1="${f1(Y(e))}" y2="${f1(Y(e))}"/><text x="${L - 8}" y="${f1(Y(e) + 4)}" text-anchor="end">${lab(e)}</text>`).join('');
    const dots = rows.map((r, i) => {
      const sys = tune[r.sql_id] && tune[r.sql_id].skipped, cx = X(xs[i]), cy = Y(ys[i]), rr = 5 + 22 * Math.sqrt((+r.total_sec || 0) / mt);
      // 右侧 100px 内有别的气泡就把标签放左边,避免压住它
      const crowded = rows.some((_, j) => j !== i && X(xs[j]) > cx && X(xs[j]) - cx < 100 && Math.abs(Y(ys[j]) - cy) < 24);
      const right = !crowded && cx + rr + 70 < L + pw;
      return `<g data-sql="${esc(r.sql_id)}" style="cursor:pointer"${tipAttr(`<b>${esc(r.sql_id)}</b>${sys ? '<span class="demo-mark">系统 SQL</span>' : ''}<div class="d">调用 ${esc(fmtInt(r.calls))} 次，平均 ${esc(fmtMs(r.avg_ms))}</div><div class="d">总耗时 ${esc(fmtSec(r.total_sec))}</div>`)}>
        <circle cx="${f1(cx)}" cy="${f1(cy)}" r="${f1(rr)}" class="${sys ? 'sys' : 'biz'}"/><text class="id" x="${f1(right ? cx + rr + 6 : cx - rr - 6)}" y="${f1(cy + 4)}" text-anchor="${right ? 'start' : 'end'}">${esc(r.sql_id)}</text></g>`;
    }).join('');
    return `<svg class="scatter" viewBox="0 0 ${W} ${H}"><g class="grid">${gx}${gy}</g>${dots}
      <text class="ax" x="${L + pw}" y="${H - 4}" text-anchor="end">调用次数（对数）→</text><text class="ax" x="${L - 50}" y="14">平均耗时 ms（对数）↑</text></svg>
      <div class="legend"><span><i class="sw" style="--c:var(--primary)"></i>业务 SQL</span><span><i class="sw" style="--c:#b8c0cc"></i>系统 SQL</span><span class="t3">气泡越大，总耗时越多；右上角是又多又慢的</span></div>`;
  };

  /** 深底上的小覆盖环。 */
  R.covring = (pct, label) => {
    const r = 36, c = 2 * Math.PI * r, v = Math.max(0, Math.min(100, pct || 0));
    return `<svg class="covring" viewBox="0 0 96 96" width="96" height="96"><defs><linearGradient id="cg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" style="stop-color:var(--cov-1)"/><stop offset=".6" style="stop-color:var(--cov-2)"/><stop offset="1" style="stop-color:var(--cov-3)"/></linearGradient></defs>
      <circle class="trk" cx="48" cy="48" r="${r}" fill="none" stroke-width="8"/>
      <circle class="cov" cx="48" cy="48" r="${r}" fill="none" stroke="url(#cg)" stroke-width="8" stroke-linecap="round" stroke-dasharray="${f1((c * v) / 100)} ${f1(c)}" transform="rotate(-90 48 48)" style="--len:${f1(c)}"/>
      <text class="pct" x="48" y="52" text-anchor="middle" font-size="17" font-weight="600">${esc(pct ?? '—')}%</text></svg><div class="cov-l">${esc(label)}</div>`;
  };

  /* ------------------------------------------------------------ 知识库关系图(全图与主视觉里的缩略图共用一个模型) */
  const COLS = [['case', '案例'], ['symptom', '现象'], ['rootcause', '根因'], ['action', '处置'], ['rule', '条款']];
  function graphModel(cat, rules) {
    const nodes = new Map(), links = [], chain = new Map(), rt = new Map(rules.map((r) => [r.id, r.rule]));
    const add = (k, id, label) => { if (!nodes.has(id)) nodes.set(id, { k, id, label }); };
    const mark = (c, ids) => chain.set(c, new Set([...(chain.get(c) || []), ...ids]));
    cat.edges.forEach((e) => { add(e.src.kind, e.src.id, e.src.label); add(e.dst.kind, e.dst.id, e.dst.label); links.push([e.src.id, e.dst.id, e.case]); mark(e.case, [e.src.id, e.dst.id]); });
    cat.cases.forEach((c) => (c.rules || []).forEach((r) => { const rid = 'rule:' + r; add('rule', rid, `${r} ${rt.get(r) || ''}`); links.push(['case:' + c.id, rid, c.id]); mark(c.id, [rid, 'case:' + c.id]); }));
    const byCol = COLS.map(([k]) => [...nodes.values()].filter((n) => n.k === k));
    return { nodes, links, chain, byCol };
  }
  R.graphSvg = (cat, rules) => {
    const { nodes, links, chain, byCol } = graphModel(cat, rules);
    const W = 1200, NW = 204, NH = 52, GAP = 16, TOP = 34, pos = new Map(), colX = (i) => i * ((W - NW) / (COLS.length - 1));
    byCol.forEach((list, ci) => list.forEach((n, i) => pos.set(n.id, { x: colX(ci), y: TOP + i * (NH + GAP) })));
    const H = Math.max(...byCol.map((l) => TOP + l.length * (NH + GAP)));
    const clip = (s, n) => (String(s).length > n ? String(s).slice(0, n - 1) + '…' : String(s));
    const path = (a, b) => { const p = pos.get(a), q = pos.get(b); if (!p || !q) return ''; const x1 = p.x + NW, y1 = p.y + NH / 2, x2 = q.x, y2 = q.y + NH / 2, m = (x1 + x2) / 2; return `M${x1},${y1} C${m},${y1} ${m},${y2} ${x2},${y2}`; };
    const casesOf = (id) => [...chain.entries()].filter(([, s]) => s.has(id)).map(([c]) => c).join('|');
    const kn = Object.fromEntries(COLS);
    return `<svg class="graph" viewBox="0 0 ${W} ${H}" id="kbgraph">${COLS.map(([, l], i) => `<text class="colh" x="${colX(i)}" y="14">${l}</text>`).join('')}
      ${links.map(([a, b, c]) => `<path d="${path(a, b)}" data-case="${esc(c)}"/>`).join('')}
      ${[...nodes.values()].map((n) => { const p = pos.get(n.id); return `<g class="node" data-cases="${esc(casesOf(n.id))}" transform="translate(${p.x},${p.y})"><title>${esc(n.label)}</title><rect width="${NW}" height="${NH}" rx="10"/><text class="k" x="12" y="20">${esc(kn[n.k] || n.k)}</text><text x="12" y="39">${esc(clip(R.cn(n.label), 15))}</text></g>`; }).join('')}</svg>`;
  };
  R.minigraph = (cat, rules) => {
    const { nodes, links, byCol } = graphModel(cat, rules), W = 420, H = 118, pos = new Map();
    byCol.forEach((list, ci) => list.forEach((n, i) => pos.set(n.id, { x: 22 + ci * ((W - 44) / (COLS.length - 1)), y: 22 + ((i + 1) * (H - 26)) / (list.length + 1) })));
    return `<svg class="minigraph" viewBox="0 0 ${W} ${H}" data-kbtab="graph" style="cursor:pointer"${tipAttr('<b>关系图</b><div class="d">点击查看完整关系图</div>')}>
      ${COLS.map(([k, l], i) => `<text x="${22 + i * ((W - 44) / (COLS.length - 1))}" y="12" text-anchor="middle">${l}</text>`).join('')}
      ${links.map(([a, b]) => { const p = pos.get(a), q = pos.get(b); return p && q ? `<line x1="${f1(p.x)}" y1="${f1(p.y)}" x2="${f1(q.x)}" y2="${f1(q.y)}"/>` : ''; }).join('')}
      ${[...nodes.values()].map((n, i) => { const p = pos.get(n.id); return `<circle cx="${f1(p.x)}" cy="${f1(p.y)}" r="5.5" style="--i:${i}"/>`; }).join('')}</svg>`;
  };
})(window.R6);
