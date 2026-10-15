# 四个大盘 · R5 设计原型(云控制台风格)

2026-10-15 定稿保存。相对现行 `frontend/`:字体与排版更精致,整体换成一线云厂商控制台的形态。
这是**设计原型**,不是产品代码;产品前端仍是 `frontend/`。

## 怎么打开

字体文件经 `file://` 打开会被浏览器拦下(跨源限制),要用一个本地 http 服务:

```bash
cd docs/prototypes
python3 -m http.server 18300 --bind 127.0.0.1
# 浏览器打开 http://127.0.0.1:18300/dash-r5/index.html#/health
```

页面:`#/health`(健康检查)、`#/topsql`、`#/wdr`、`#/kb`。顶部「实例」切到 og-std 可看采集失败的样子。

## 设计要点

参照 AWS Cloudscape、腾讯 TDesign、蚂蚁 Ant Design Pro、华为云 DevUI 的共同形态:

- 白色顶栏 + 分组侧栏 + 浅灰内容区,面包屑;内容装在带标题、说明、操作的白色面板里。
- 概况数字同一面板竖线分隔;详情用多列键值对;表格表头浅灰、行细线、蓝色文字操作,详情从右侧抽屉滑出。
- 整页一个品牌蓝(`#2462E8`);状态只用「小图标 + 文字」,级别四档加「采集失败」,图标形状各不相同。
- 字体:IBM Plex Sans(数字与西文)+ 苹方 / 微软雅黑(中文);IBM Plex Mono 只给 SQL 与执行计划。
  字体文件在 `fonts/`,SIL Open Font License(见 `fonts/LICENSE.txt`),可随产品离线分发。
- 中文全角标点,中英文之间留空格;表格默认不折行。

## 数据

`data.js` 由 `build-data.py` 从 `scripts/dash-samples.py` 生成的样例报告原样打包,字段与用户容器 4097 端口端出的报告一致;
口径照搬 `frontend/`(级别判定、WDR 对比上一窗口、Top SQL 按报告的排序维度)。
巡检历史与窗口历史在真实条目之前补了若干条 `demo: true` 的示意条目,页面上画成空心、点不开。

```bash
bash scripts/dash-preview.sh 18290          # 生成样例报告(Ctrl-C 结束后目录仍在 /tmp)
python3 docs/prototypes/dash-r5/build-data.py <样例报告目录> > docs/prototypes/dash-r5/data.js
```

深挖、调优等按钮在原型里只弹提示,不连 opencode。
