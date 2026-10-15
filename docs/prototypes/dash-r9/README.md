# 四个大盘 · R9 设计原型(智能体工作台)

2026-10-15 定稿保存。这是**设计原型**,不是产品代码;产品前端仍是 `frontend/`。
不再是大盘,而是一个和智能体协作的工作台。参照 2025 年的生成式界面:Claude / ChatGPT 的 Artifacts、Perplexity 带来源的回答、
Datadog Bits AI 与 Grafana Assistant 的排查过程。

- **左:任务栏**。每份报告是一个任务(健康巡检、WDR 窗口、Top SQL 各一次),按日期分组;知识库常驻。点任务即切换。
- **中:线程**。先是任务卡:智能体做了哪几步、哪步失败(全部来自报告字段:维度是否采到、子技能成败、判级、知识库是否接入);
  然后是智能体的回答(要点里的来源小签可点),再往下是内嵌的图表卡(检查项圆环、发现卡、树图、对比哑铃……)和「接着问」。
  底部输入框带着当前任务的上下文;提问直接追加在线程里(原型不连 opencode,只说明真实环境会怎样)。
- **右:证据栏**。默认是这份报告的原始结构;点线程里的来源、条目或「看证据」,详情在这里展开,不再弹抽屉。
- 视觉:暖白底、正文排版优先、几乎无彩色;颜色只给状态与智能体身份。

数据与文字生成沿用 R6(`js/` 下的页面模块),布局在 `js/workspace.js`、`js/threads.js`,样式在 `r9.css`(`base.css` 为 R6 组件样式)。
打开(在 `docs/prototypes` 下起 `python3 -m http.server 18300 --bind 127.0.0.1`):`http://127.0.0.1:18300/dash-r9/index.html#/health`

## 数据与字体

本目录自带数据、字体与脚本,可单独打开。`data.js` 与 R5/R6 相同,由 `build-data.py` 从 `scripts/dash-samples.py` 生成的样例报告打包;
标「示意」或画成空心的条目是原型补充。字体在 `fonts/`(IBM Plex,SIL Open Font License,见 `fonts/LICENSE.txt`)。
截图验收参数同 R6:`?still` 跳过动效,`?inst=` `?dim=` `?sql=` `?case=` `?wdim=` `?cmdk=` `?ask=`。
