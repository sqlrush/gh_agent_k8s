// 自绘 SVG / HTML 片段,全部返回字符串。逻辑从 docs/prototypes/*-r1.html 的 <script> 抽出。
// 不引第三方图表库:大盘就这几种图,一个库的体积和升级都比它们贵。

import { esc, lv } from './fmt.js';

const C = { ok: '#3fa552', notice: '#c9862d', warn: '#e07a1f', crit: '#d64545', na: '#dde0e5' };
export const PAL = ['#4176e6', '#2fa79a', '#8b6be0', '#e07a1f', '#c9862d', '#d64545', '#3fa552', '#61666b', '#9aa3ad', '#b8c0c8'];

/** 维度环:segs = [{level:0-3|null}],level null = 不可用(灰)。中心写数量。 */
export function ring(segs, size = 96) {
  const n = segs.length || 1, r = size * 0.375, cx = size / 2, cy = size / 2;
  let h = '';
  segs.forEach((s, i) => {
    const a0 = (i / n) * 2 * Math.PI - Math.PI / 2, a1 = ((i + 0.92) / n) * 2 * Math.PI - Math.PI / 2;
    const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0), x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
    const col = s.level === null || s.level === undefined ? C.na : C[lv(s.level)];
    h += `<path d="M${x0.toFixed(1)} ${y0.toFixed(1)} A${r} ${r} 0 0 1 ${x1.toFixed(1)} ${y1.toFixed(1)}" stroke="${col}" stroke-width="${size / 8}" fill="none"/>`;
  });
  h += `<text x="${cx}" y="${cy + 5}" text-anchor="middle" font-size="15" font-weight="700" fill="#0f1115">${segs.length}</text>`;
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">${h}</svg>`;
}

/** 堆叠条:parts = [{share(0-100), color}]。 */
export function stack(parts) {
  return `<div class="stack">${parts.map((p) => `<i style="width:${Math.max(0, p.share).toFixed(2)}%;background:${p.color}"></i>`).join('')}</div>`;
}

export function legend(items) {
  return `<div class="legend">${items.map((it) => `<span style="--c:${it.color}">${esc(it.label)}</span>`).join('')}</div>`;
}

/** 单根占比条,level 决定颜色。 */
export function bar(pct, level = '') {
  return `<div class="bar ${level}"><i style="width:${Math.max(2, Math.min(100, pct)).toFixed(1)}%"></i></div>`;
}

/** 折线:values 数字数组,按最大值归一。 */
export function sparkline(values, color, W = 400, H = 70) {
  const n = Math.max(values.length, 2), mx = Math.max(4, ...values.map(Number).filter(Number.isFinite));
  const pts = values.map((v, i) => `${(i * (W / (n - 1))).toFixed(1)},${(H - 6 - (Number(v) || 0) / mx * (H - 14)).toFixed(1)}`).join(' ');
  return `<polyline fill="none" stroke="${color}" stroke-width="2" points="${pts}"/>`;
}

/** 级别时间轴:levels = [0-3],最后一根加框。 */
export function timeline(levels) {
  // null = 这一次没采到数据:画灰色,不能当成「正常」的绿色
  const n = levels.length;
  return `<div class="tl">${levels.map((l, i) => `<i class="${l === null || l === undefined ? "na" : lv(l)}${i === n - 1 ? " now" : ""}" style="height:${28 + Number(l || 0) * 5}px"></i>`).join("")}</div>`;
}
