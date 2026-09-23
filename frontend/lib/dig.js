// 「在会话里深挖 →」:在新标签页打开 opencode Web 的一个新会话,首句已经发出去。
//
// 顺序不能改:**先在点击事件里同步开一个空标签页**,再去建会话。浏览器只放行用户手势里同步
// 发起的 window.open;等 fetch 回来再开,会被当弹窗拦掉。建会话失败就把那个空页关掉,
// 别留一个 about:blank 给用户。
//
// 这里没有任何创建 / 查询 Pod 的逻辑:请求经网关,网关的 ensure_ready 复用已有 Pod
//(在跑 → 直接用;缩到 0 → 拉起;没建过 → 才建)。前端不知道也不需要知道 Pod 的事。

import { toast } from './shell.js';

// opencode Web 的项目路由 = base64url(工作目录);容器里工作目录固定是 /data/state/workspace
const WORKSPACE = '/data/state/workspace';
const b64url = (s) => btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
export const CHAT_HOME = '/' + b64url(WORKSPACE);

async function post(path, body) {
  const r = await fetch(path, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const text = await r.text();
  return text ? JSON.parse(text) : null;
}

/**
 * 深挖:建会话 + 发首句 + 跳过去(新标签页)。
 * @param {string} prompt 首句。**只放这条发现的数据**,不放任何平台配置取值。
 * @param {string} [title] 会话标题
 */
export async function openDig(prompt, title = '大盘深挖') {
  const tab = window.open('about:blank');          // 必须在手势里同步开
  try {
    const s = await post('/session', { title });
    if (!s || !s.id) throw new Error('会话没有 id');
    await post(`/session/${s.id}/prompt_async`, { parts: [{ type: 'text', text: prompt }] });
    const url = `${CHAT_HOME}/session/${s.id}`;
    if (tab) tab.location = url; else window.location.assign(url);
  } catch (e) {
    if (tab) tab.close();
    toast(`打开会话失败:${e && e.message || e}`);
  }
}

/** 给 <a data-dig="..."> 统一挂点击:页面里只写属性,不各自写 handler。 */
export function bindDigLinks(root) {
  root.querySelectorAll('[data-dig]').forEach((el) => {
    el.addEventListener('click', (ev) => {
      ev.preventDefault();
      openDig(el.getAttribute('data-dig'), el.getAttribute('data-dig-title') || '大盘深挖');
    });
  });
}
