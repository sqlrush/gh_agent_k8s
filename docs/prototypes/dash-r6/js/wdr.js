/* R6 WDR:主视觉 = DB Time 与构成 + 智能体解读 + 八个指标的对比哑铃;其下 = 窗口 Top SQL、结论、检查点、窗口历史、IO。
   对比口径:与上一份报告的窗口比;倍数 ≥1.5 写成 ×N,百分比写差值 pt,MiB 过千换 GiB。 */
(function (R) {
  'use strict';
  const { D, S, esc, I, lk, lname, glyph, tipAttr, dig, num, fmtInt, stamp, cn, colFmt, split } = R;

  const WD = { loadprofile: 'Load Profile', dbstat: 'Database Stat', topsql: 'Top SQL', waits: 'Wait Events / Classes', checkpoint: 'Checkpoint / BgWriter / Redo', cache: 'Cache / Memory', fileio: 'File IO' };
  const WT = { 'Load Profile': '负载概况', 'Database Stat': '数据库统计', 'Top SQL': '窗口 Top SQL', 'Wait Events / Classes': '等待事件', 'Checkpoint / BgWriter / Redo': '检查点与 Redo', 'Cache / Memory': '缓存与内存', 'File IO': '文件 IO' };
  const wdim = (d, k) => ((d && d.dims) || []).find((x) => x.dimension === WD[k]);
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
  /** 数值 + 单位;MiB 过千换 GiB,万/亿进单位。 */
  const showK = (v, u) => { const [x, xu] = u === 'MiB' && Math.abs(v) >= 1024 ? [fmtK(v / 1024), 'GiB'] : [fmtK(v), u]; const [n, u2] = split(x); return [n, [u2, xu].filter(Boolean).join(' ')]; };
  /** 与上一窗口比的变化:cls ∈ bad/worse/better/flat,与 R5 同口径。 */
  R.change = (k, cur, prev) => {
    const r = prev ? cur / prev : null, diff = cur - prev, worse = k.worse === 'up' ? diff > 0 : k.worse === 'down' ? diff < 0 : false;
    const cls = worse ? (r && (r >= 3 || r <= 1 / 3) ? 'bad' : 'worse') : k.worse && diff !== 0 ? 'better' : 'flat';
    const text = !prev ? '新出现' : r && Math.abs(r) >= 1.5 ? '×' + r.toFixed(1) : k.u === '%' ? Math.abs(diff).toFixed(1) + ' pt' : diff === 0 ? '持平' : Math.abs((diff / prev) * 100).toFixed(0) + '%';
    return { cls, text, dir: Math.sign(diff), r, diff };
  };

  function insightFor(cur, d, p, fs) {
    const dbt = kpiVal(KPIS[0], d), pdb = kpiVal(KPIS[0], p), cpu = wval(d, 'loadprofile', 'CPU占DBtime'), pcpu = wval(p, 'loadprofile', 'CPU占DBtime');
    const fl = (f) => `<span class="ev" data-wdim="${esc(f.dimension)}">${glyph(f.severity)}${esc(WT[f.dimension] || f.dimension)}</span> ${esc(cn(f.metric))} <b>${esc(f.value)}</b>，阈值 ${esc(f.threshold)}${(() => { const x = R.times(f); return x && x >= 1.05 ? `，是阈值的 <b>${x.toFixed(1)} 倍</b>` : ''; })()}。`;
    // 平均活跃会话由 DB Time 换算,标题已说过,这里从第三项起;明显变坏的排前面
    const moved = p ? KPIS.slice(2).map((k) => ({ k, c: kpiVal(k, d), p: kpiVal(k, p) })).filter((x) => x.c !== null && x.p !== null && x.k.worse).map((x) => ({ ...x, ch: R.change(x.k, x.c, x.p) })).filter((x) => x.ch.cls === 'bad' || x.ch.cls === 'worse').sort((a, b) => (b.ch.cls === 'bad') - (a.ch.cls === 'bad')) : [];
    const mv = moved.map((x) => { const [a, au] = showK(x.p, x.k.u), [b, bu] = showK(x.c, x.k.u); return x.k.u === '%' ? `${x.k.l}从 ${a}% 到 ${b}%（${x.ch.diff > 0 ? '+' : '−'}${x.ch.text}）` : `${x.k.l}从 ${a} ${au} 到 <b>${b} ${bu}</b>（${x.ch.text}）`; });
    const head0 = pdb && dbt ? (dbt / pdb >= 1.5 ? `DB Time 是上一窗口的 ${(dbt / pdb).toFixed(1)} 倍` : dbt / pdb <= 0.67 ? `DB Time 降到上一窗口的 ${((dbt / pdb) * 100).toFixed(0)}%` : 'DB Time 与上一窗口相当') + (fs[0] ? `，${esc(cn(fs[0].metric))} ${esc(fs[0].value)}` : '')
      : fs[0] ? `${esc(cn(fs[0].metric))} ${esc(fs[0].value)}，${lname(fs[0].severity)}` : '窗口内没有越过阈值的发现';
    const head = R.pangu(head0);
    const chips = [];
    const sid = fs.map((f) => (/sqlid\s+(\w+)/.exec(f.evidence || '') || [])[1]).find(Boolean);
    if (sid) chips.push([`${sid} 为什么占这么多 DB Time`, `请分析 WDR 窗口内 sql_id 为 ${sid} 的语句为什么占这么多 DB Time。`]);
    if (fs.some((f) => /IO_EVENT/.test(f.metric))) chips.push(['IO 等待从哪来', '请分析这个 WDR 窗口里 IO_EVENT 等待的来源：哪些对象、哪些文件、哪些语句。']);
    if (moved.some((x) => x.k.l === '临时溢出')) chips.push(['临时溢出怎么降', '这个 WDR 窗口临时溢出明显上升，请找出溢出的语句并给出 work_mem 或改写建议。']);
    chips.push(['逐项对比两个窗口', '请逐项对比这个 WDR 窗口和上一窗口，说明负载变化的主要原因。']);
    return { src: `依据 snap ${d.window.begin_id}–${d.window.end_id} 窗口报告 · 级别由脚本判定`, ask: '问问这个窗口，比如：负载为什么翻倍？', head, chips: chips.slice(0, 4),
      lines: [fs[0] && fl(fs[0]), fs[1] && fl(fs[1]), mv.length ? `与上一窗口相比，${mv.slice(0, 3).join('，')}。` : p ? '其余指标与上一窗口相比没有明显变坏。' : '这是第一份报告，没有可对比的窗口。',
        cpu !== null && pcpu !== null && Math.abs(cpu - pcpu) >= 5 ? `CPU 占 DB Time 从 ${pcpu}% ${cpu < pcpu ? '降' : '升'}到 ${cpu}%，等待占比${cpu < pcpu ? '升' : '降'}到 <b>${(100 - cpu).toFixed(0)}%</b>。` : ''] };
  }

  function hb(dim, valCol, unit, hero) {
    if (!dim || !(dim.rows || []).length) return `<p class="t3">${esc(cn((dim && dim.note) || '未采集'))}</p>`;
    const h = dim.headers || [], F = h.map((x, i) => colFmt(dim.rows, i)), mx = Math.max(1, ...dim.rows.map((r) => num(r[valCol]) || 0)), other = h.map((x, i) => i).filter((i) => i && i !== valCol);
    return `<div class="hb">${dim.rows.map((r) => `<div class="r"${tipAttr(`<b>${esc(r[0])}</b>${h.map((x, i) => (i ? `<div class="d">${esc(x)}：${esc(F[i](r[i]))}</div>` : '')).join('')}`)}><div class="l">${esc(r[0])}${other.length && !hero ? `<small>${other.map((i) => `${esc(h[i])} ${esc(F[i](r[i]))}`).join('，')}</small>` : ''}</div><div class="b"><i class="grow" style="width:${((num(r[valCol]) || 0) / mx) * 100}%"></i></div><div class="v">${esc(F[valCol](r[valCol]))}${unit ? `<small>${esc(unit)}</small>` : ''}</div></div>`).join('')}</div>`;
  }

  function hero(cur, d, p, fs) {
    const w = d.window || {}, dbt = kpiVal(KPIS[0], d), pdb = kpiVal(KPIS[0], p), cpu = wval(d, 'loadprofile', 'CPU占DBtime'), waits = wdim(d, 'waits');
    const ch = dbt !== null && pdb !== null ? R.change(KPIS[0], dbt, pdb) : null;
    const comp = cpu === null ? '' : `<div class="stackbar"><i style="flex:${cpu};background:var(--sb-cpu)"${tipAttr(`<b>CPU ${cpu.toFixed(1)}%</b>`)}></i><i style="flex:${100 - cpu};background:var(--sb-wait)"${tipAttr(`<b>等待 ${(100 - cpu).toFixed(1)}%</b><div class="d">锁、IO、睡眠等</div>`)}></i></div>
      <div class="legend2"><span><i class="sw" style="background:var(--sb-cpu)"></i>CPU ${cpu.toFixed(1)}%</span><span><i class="sw" style="background:var(--sb-wait)"></i>等待 ${(100 - cpu).toFixed(1)}%</span></div>`;
    const dbBox = `<div class="glass dbt rise"${R.rise()}><div class="title">${I.clock}DB Time<span class="t3">${esc(w.duration_min)} 分钟窗口</span></div>
      <div class="big num">${dbt === null ? '—' : R.cu(fmtInt(Math.round(dbt)))}<small>秒</small></div>
      <div class="sub">平均 <b>${w.duration_min && dbt !== null ? (dbt / (w.duration_min * 60)).toFixed(1) : '—'}</b> 个活跃会话${ch ? `<span class="chg ${ch.cls}">${ch.dir > 0 ? I.up : ch.dir < 0 ? I.dn : ''}${esc(ch.text)}</span><span class="t3">上一窗口 ${esc(fmtInt(Math.round(pdb)))} 秒</span>` : ''}</div>
      ${comp}<div class="subt">等待按等待类拆开（占等待总时间）</div>${hb(waits, waits ? (waits.headers || []).length - 1 : 0, '%', true)}</div>`;
    const dumbs = `<div class="db-head"><b>负载对比</b><span>空心点是上一窗口${p ? `（snap ${esc(p.window.begin_id)}–${esc(p.window.end_id)}）` : ''}，实心点是本窗口；比例尺从 0 开始</span><span class="legend2" style="margin:0 0 0 auto"><span><i class="sw" style="background:var(--h-crit)"></i>明显变坏</span><span><i class="sw" style="background:var(--h-warn)"></i>变坏</span><span><i class="sw" style="background:var(--h-ok)"></i>变好</span><span><i class="sw" style="background:var(--h-t3)"></i>无好坏之分</span></span></div>
      <div class="dumbbells">${KPIS.map((k) => R.dumbbell(k, kpiVal(k, d), kpiVal(k, p), showK)).join('')}</div>`;
    return `<section class="hero"><div class="hero-grid">${dbBox}${R.insight(insightFor(cur, d, p, fs))}</div>${dumbs}</section>`;
  }

  function render() {
    const cur = R.current('wdr', S.key), ph = (st, act, desc) => R.pageHead('WDR 分析', st, act, desc);
    if (!cur) return ph('', '', []) + R.emptyHero('还没有 WDR 报告', '在会话里对一个快照窗口做分析，报告会自动出现在这里。', '请列出最近的 WDR 快照，并对最近一个窗口做分析。', '做一次 WDR 分析');
    const d = cur.data, w = d.window || {}, p = cur.prev && cur.prev.data, pw = p && p.window, native = d.native || {};
    if (w.wdr_enabled === false) return ph('', '', []) + R.emptyHero('这个库没有开启 WDR 快照', '需要把 enable_wdr_snapshot 设为 on，并有快照权限。', '请检查当前库的 WDR 快照是否开启，说明如何开启以及需要的权限。', '在会话里检查');
    const fs = [...(d.findings || [])].sort((a, b) => (b.severity | 0) - (a.severity | 0)), ts = wdim(d, 'topsql');
    const verdicts = fs.length ? `<div class="list">${fs.map((f) => `<div class="it">${glyph(f.severity)}<div class="m"><b>${esc(cn(f.metric))}：${esc(f.value)}</b><small>${esc(lname(f.severity))}，阈值 ${esc(f.threshold)}。${esc(cn(f.evidence))}</small></div><a href="#" class="r"${R.ask(`请针对 WDR 发现 ${f.code}（${f.metric}=${f.value}）做深入分析：${f.evidence}`)}>问智能体</a></div>`).join('')}</div>` : `<div class="empty-state" style="padding:24px">${glyph(0)}<p style="margin-top:10px">窗口内没有越过阈值的发现。</p></div>`;
    const tsh = (ts && ts.headers) || [], shareI = tsh.findIndex((h) => String(h).startsWith('占DB')), TF = tsh.map((x, i) => colFmt((ts && ts.rows) || [], i));
    const tsTable = ts && (ts.rows || []).length ? `<div class="scroll"><table class="tbl"><thead><tr>${tsh.map((h, i) => `<th class="${i && i !== tsh.length - 1 && i !== shareI ? 'r' : ''}">${esc(h)}</th>`).join('')}</tr></thead><tbody>${ts.rows.map((r) => `<tr>${r.map((c, i) => (i === 0 ? `<td class="id"><a href="#"${R.ask(`请分析 WDR 窗口内 sql_id 为 ${c} 的语句。`)}>${esc(c)}</a></td>` : i === shareI ? `<td><div class="share"><div class="b"><i class="grow" style="width:${Math.min(100, num(c) || 0)}%"></i></div><b>${esc(c)}%</b></div></td>` : i === r.length - 1 ? `<td class="q" title="${esc(c)}">${esc(c)}</td>` : `<td class="r">${esc(TF[i](c))}</td>`)).join('')}</tr>`).join('')}</tbody></table></div>` : '<p class="t3" style="padding:0 20px">未采集</p>';
    const ck = wdim(d, 'checkpoint');
    const ckBody = ck && (ck.rows || [])[0] ? `<p class="t2" style="margin:0 0 14px;font-size:13px">${esc(cn(ck.headline || ''))}</p><div class="kvs" style="--cols:${(ck.headers || []).length}">${(ck.headers || []).map((h, i) => `<div><div class="kv"><div class="k">${esc(h)}</div><div class="v num" style="font-size:20px">${esc(ck.rows[0][i])}</div></div></div>`).join('')}</div>` : '<p class="t3">未采集</p>';
    const acts = R.runPicker('wdr', '窗口') + (native.generated ? `<button class="btn" data-toast="原型：真实环境会下载 openGauss 原生 WDR 报告（HTML，${fmtInt(Math.round((native.bytes || 0) / 1024))} KB）">${I.down}原生报告</button>` : '') + R.primary('请解读这个 WDR 窗口，说明负载变化的主要原因。', '在会话里分析');
    return ph(R.tag(d.overall | 0), acts, [['实例', esc(d.conn)], ['窗口', `snap ${esc(w.begin_id)} 至 ${esc(w.end_id)}`], ['时间', `${esc(String(w.begin_ts || '').slice(5, 16))} 至 ${esc(String(w.end_ts || '').slice(11, 16))}，${esc(w.duration_min)} 分钟`], ['范围', `${esc(w.scope || '')} ${esc(w.node || '')}`], ['对比', pw ? `snap ${esc(pw.begin_id)} 至 ${esc(pw.end_id)}` : '没有上一窗口']])
      + hero(cur, d, p, fs)
      + `<div class="bento">${R.tile({ title: '窗口内 Top SQL', desc: esc(cn((ts && ts.headline) || '')), body: tsTable, flush: true })}
        ${R.tile({ span: 'b4', title: `结论<span class="cnt">${fs.length}</span>`, desc: '脚本按阈值判定', body: verdicts })}
        ${R.tile({ span: 'b4', title: '检查点与 Redo', body: ckBody })}
        ${R.tile({ span: 'b4', title: '窗口历史', desc: `最近 ${cur.index.length} 个窗口，点格子切换`, body: R.rhythm('wdr', S.key, cur.entry.file) })}
        ${R.tile({ span: 'b6', title: '物理读最多的对象', body: hb(wdim(d, 'cache'), 1, '块') })}
        ${R.tile({ span: 'b6', title: '文件 IO', desc: '按物理读', body: hb(wdim(d, 'fileio'), 1, '') })}</div>`;
  }

  function openWdim(name) {
    const d = R.current('wdr', S.key).data, x = (d.dims || []).find((y) => y.dimension === name); if (!x) return;
    const h = x.headers || [], F = h.map((_, j) => colFmt(x.rows || [], j)), fs = (d.findings || []).filter((f) => f.dimension === name), l = fs.reduce((a, f) => Math.max(a, f.severity | 0), 0);
    const table = (x.rows || []).length ? `<div class="scroll boxed"><table class="tbl"><thead><tr>${h.map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${x.rows.map((row) => `<tr>${row.map((c, j) => `<td class="${/^-?[\d.,]+$/.test(String(c)) ? 'r' : String(c).length > 40 ? 'q' : ''}" title="${esc(c)}">${esc(F[j](c))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : '<p class="t3">没有数据行</p>';
    R.drawer(esc(WT[name] || name), R.tag(l), `<div class="sec">概况</div><p class="t2" style="margin:0">${esc(cn(x.headline || ''))}</p>${fs.length ? `<div class="sec">发现</div>${fs.map((f) => `<div class="alert" data-lv="${lk(f.severity)}">${glyph(f.severity)}<div><b>${esc(cn(f.metric))}：${esc(f.value)}，阈值 ${esc(f.threshold)}</b><p>${esc(cn(f.evidence))}</p></div></div>`).join('')}` : ''}<div class="sec">明细</div>${table}`,
      `<button class="btn primary"${R.ask(`请深入分析 WDR 窗口的「${WT[name] || name}」：${x.headline || ''}`)}>${I.spark}问智能体</button><button class="btn" data-close>关闭</button>`);
  }

  function assist() {
    const cur = R.current('wdr', S.key);
    if (!cur) return { ctx: ['WDR 分析', `实例 ${S.key}`, '还没有报告'], sugg: [['做一次 WDR 分析', '列出快照并分析最近一个窗口', '请列出最近的 WDR 快照，并对最近一个窗口做分析。']] };
    const d = cur.data, w = d.window || {};
    return { ctx: ['WDR 分析', `实例 ${d.conn}`, `snap ${w.begin_id}–${w.end_id}`, `${w.duration_min} 分钟`],
      sugg: [['负载为什么变了？', '和上一窗口逐项对比', '请逐项对比这个 WDR 窗口和上一窗口，说明负载变化的主要原因。'],
        ...(d.findings || []).slice(0, 2).map((f) => [`${cn(f.metric)} ${f.value}`, `阈值 ${f.threshold}，问问原因`, `请针对 WDR 发现 ${f.code}（${f.metric}=${f.value}）做深入分析：${f.evidence}`]),
        ['这是算力型还是等待型负载？', '看 CPU 与等待的构成', '请分析最近一个 WDR 窗口的 DB Time 构成，说明是算力型还是等待型负载。']] };
  }
  function commands() {
    const cur = R.current('wdr', S.key); if (!cur) return [];
    return (cur.data.dims || []).map((x) => ({ grp: 'WDR', label: WT[x.dimension] || x.dimension, sub: cn(x.headline || ''), ic: 'clock', go: () => { location.hash = '#/wdr'; setTimeout(() => openWdim(x.dimension), 0); } }));
  }
  R.pages.wdr = { render, assist, commands, openWdim };
})(window.R6);
