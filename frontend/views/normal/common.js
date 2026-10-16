// 普通风格的版式部件:页头、便当盒卡片、智能体解读卡、主视觉统计、空 / 出错状态、报告选择菜单。

import { esc, stamp } from '../../lib/fmt.js';
import { I, glyph, lname, primary } from '../../lib/ui.js';
import { rise } from '../../lib/motion.js';
import { menu } from '../../lib/overlay.js';
import { getModel } from '../../lib/store.js';
import { entryLevel } from '../../pages/health.js';

export function pageHead(crumb, statusTag, actions, desc) {
  return `<div class="crumb">诊断大盘<span>/</span><b>${esc(crumb)}</b></div>
    <div class="page-head"><h1>${esc(crumb)}</h1>${statusTag || ''}<div class="actions">${actions || ''}</div></div>
    <div class="page-desc">${desc.map(([k, v]) => `<span>${esc(k)}：<b>${v}</b></span>`).join('')}</div>`;
}

export const tile = (o) => `<section class="tile ${o.span || 'b12'} rise ${o.cls || ''}"${rise()}${o.id ? ` id="${o.id}"` : ''}>${o.title ? `<div class="tile-h"><h3>${o.title}</h3>${o.desc ? `<span class="desc">${o.desc}</span>` : ''}<div class="tools">${o.tools || ''}</div></div>` : ''}<div class="tile-b ${o.flush ? 'flush' : ''}">${o.body}</div></section>`;

/** 智能体解读卡:结论、要点(带证据小签)、建议追问、提问框。提问框与追问都开 opencode 会话(data-dig / data-askform)。 */
export const insightCard = (o) => `<div class="glass insight rise"${rise()}>
    <div class="who"><span class="orb">${I.spark}</span><b>智能体解读</b><span class="src">${esc(o.src)}</span></div>
    <h2>${o.head}</h2>
    <ul class="lines">${o.lines.filter(Boolean).map((l) => `<li><span>${l}</span></li>`).join('')}</ul>
    <div class="chips">${o.chips.map(([l, p]) => `<button class="chip-ask" data-dig="${esc(p)}">${I.spark}${esc(l)}</button>`).join('')}</div>
    <form class="askbox" data-askform data-ctx="${esc(o.ctx || '')}">${I.spark}<input name="q" autocomplete="off" placeholder="${esc(o.ask)}"><button class="send" aria-label="在会话里问">${I.send}</button></form>
  </div>`;

export const heroStats = (items) => `<div class="hero-stats">${items.map((x) => `<div class="glass rise"${rise()}><div class="l">${x.l}</div><div class="v num">${x.v}<small>${esc(x.u || '')}</small></div><div class="s">${x.s || ''}</div></div>`).join('')}</div>`;

export const emptyHero = (title, text, prompt, label) => `<section class="hero rise"${rise()}><div class="empty-state">${glyph(null)}<h4>${esc(title)}</h4><p>${esc(text)}</p>${primary(prompt, label)}</div></section>`;
export const errorHero = (error) => `<section class="hero"><div class="empty-state">${glyph(2)}<h4>报告读取失败</h4><p>${esc(error || '')}</p></div></section>`;

/** 「巡检 09-22 14:03 ▾」:点开列出最近的报告,选一份即切过去(data-pick)。 */
export const runPicker = (skill, entry, noun) => (entry ? `<button class="btn" data-run-menu="${skill}">${I.cal}${esc(noun)} ${esc(stamp(entry.at))}${I.chev}</button>` : '');
export function openRunMenu(anchor, skill) {
  const m = getModel(skill); if (!m || !m.idx) return;
  menu(anchor, `<div class="mh">${skill === 'wdr' ? '分析过的窗口' : '巡检报告'}，最近的在上</div>${[...m.idx].reverse().map((e) =>
    `<div class="mi ${m.entry && m.entry.file === e.file ? 'on' : ''}" data-pick="${esc(skill)}|${esc(e.file)}">${glyph(entryLevel(e))}<span class="num">${esc(stamp(e.at))}</span><span class="r">${esc(lname(entryLevel(e)))}</span></div>`).join('')}`, true);
}
