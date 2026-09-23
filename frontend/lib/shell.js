// 外壳:侧栏(四个大盘 + 对话链接 + 当前工号 / 实例)、按 pathname 装页面、实例切换、提示条。
//
// 页面在 /dash/ 下端出:/dash/health、/dash/topsql、/dash/wdr、/dash/kb(nginx try_files 都落到
// index.html)。导航用绝对路径,模块导入用相对本文件的路径(../pages/x.js)。

import { fetchWhoami, fetchTargets } from './data.js';
import { setDigTarget } from './dig.js';
import { esc, stamp } from './fmt.js';

const PAGES = [
  { name: 'health', label: '◎ 健康检查', skill: 'health' },
  { name: 'topsql', label: '▤ Top SQL', skill: 'topsql' },
  { name: 'wdr',    label: '◔ WDR 分析', skill: 'wdr' },
  { name: 'kb',     label: '▥ 知识库',   skill: null },     // 全体共享,不分实例
];
const KEY_STORE = 'dash.instance';
const WORKSPACE = '/data/state/workspace';
const b64url = (s) => btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const CHAT_HOME = '/' + b64url(WORKSPACE);

let toastTimer = null;
export function toast(msg) {
  let el = document.querySelector('.toast');
  if (!el) { el = document.createElement('div'); el.className = 'toast'; document.body.appendChild(el); }
  el.textContent = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), 4000);
}

export function pageName() {
  const seg = location.pathname.replace(/\/+$/, '').split('/').pop();
  return PAGES.some((p) => p.name === seg) ? seg : 'health';
}

function readKey() { try { return localStorage.getItem(KEY_STORE) || ''; } catch (_) { return ''; } }
function writeKey(k) { try { localStorage.setItem(KEY_STORE, k); } catch (_) { /* 隐私模式等,忽略 */ } }

/** 当前页的实例:localStorage 里的那个若在 targets 里就用它,否则用最近跑过的那个。 */
export function pickKey(targets) {
  const saved = readKey();
  if (targets.some((t) => t.key === saved)) return saved;
  return targets.length ? targets[0].key : '';
}

function sidebar(current, whoami, targets, key) {
  const nav = PAGES.map((p) => `<a href="/dash/${p.name}" class="${p.name === current ? 'on' : ''}">${p.label}</a>`).join('');
  const inst = targets.length
    ? `<select id="inst">${targets.map((t) => `<option value="${esc(t.key)}" ${t.key === key ? 'selected' : ''}>${esc(t.label || t.conn)} · ${esc(stamp(t.last_at))}</option>`).join('')}</select>`
    : `<div style="font-size:12.5px;color:var(--dim);padding:4px 8px">还没有报告</div>`;
  return `
    <div class="brand"><i></i>GaussDB 智能体<small>v1.1</small></div>
    <div class="sec">大盘</div><div class="nav">${nav}</div>
    <div class="sec">对话</div>
    <div class="nav">
      <a href="${CHAT_HOME}" target="_blank" rel="noopener">⊕ 新对话 ↗</a>
      <a href="${CHAT_HOME}" target="_blank" rel="noopener">◌ 最近对话 ↗</a>
    </div>
    <div class="sec">当前</div>
    <div class="nav"><a href="#" onclick="return false">工号 ${esc(whoami || '—')}</a></div>
    ${current === 'kb' ? '' : `<div class="sec">实例</div>${inst}`}`;
}

export async function mount() {
  const side = document.getElementById('side');
  const main = document.getElementById('main');
  const name = pageName();
  const page = PAGES.find((p) => p.name === name);

  const who = await fetchWhoami();
  const whoami = who.ok ? who.data.user_id : '';
  let targets = [];
  if (page.skill) {
    const t = await fetchTargets(page.skill);
    targets = t.ok && Array.isArray(t.data) ? t.data : [];
  }
  const key = page.skill ? pickKey(targets) : '';
  side.innerHTML = sidebar(name, whoami, targets, key);
  const sel = document.getElementById('inst');
  if (sel) sel.addEventListener('change', () => { writeKey(sel.value); mount(); });

  const ctx = { key, targets, whoami, target: targets.find((t) => t.key === key) || null };
  setDigTarget(ctx.target);          // 深挖首句带上「先登录哪个库」;知识库页没有实例,传 null
  main.innerHTML = '<div class="loading">加载中…</div>';
  try {
    const mod = await import(`../pages/${name}.js`);
    await mod.render(main, ctx);
  } catch (e) {
    main.innerHTML = `<div class="empty"><b>页面加载失败</b>${esc(e && e.message || e)}</div>`;
  }
}
