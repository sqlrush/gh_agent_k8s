"""网关入口:`python3 -m gateway`。

启动顺序有意为之:**先把配置全部校验完再碰 k8s**。配置不全时要在启动这一刻就
失败退出,而不是跑起来之后在第一个用户请求上才暴露 —— 那时候看起来像「用户进不去」,
排查方向会被带到 SSO 那边去。
"""
from __future__ import annotations

import pathlib
import signal
import sys

from . import config as gwconfig
from . import kube as gwkube
from . import pods, reaper, server


def main(argv=None) -> int:
    import os
    try:
        cfg = gwconfig.load(os.environ)
    except gwconfig.ConfigError as exc:
        print("配置错误,网关拒绝启动:%s" % exc, file=sys.stderr)
        return 2

    try:
        kube = gwkube.Kube.in_cluster(cfg.namespace)
    except gwkube.KubeError as exc:
        print("拿不到 k8s 访问凭据:%s" % exc, file=sys.stderr)
        return 2

    templates = pathlib.Path(os.environ.get("GATEWAY_TEMPLATES") or "/opt/gateway/templates")
    missing = [k for k in ("runtime", "kb-import") if not (templates / ("%s.yaml" % k)).is_file()]
    if missing:
        print("缺 Pod 模板 %s(找的是 %s)" % (missing, templates), file=sys.stderr)
        return 2

    mgr = pods.Manager(kube, namespace=cfg.namespace, image_tag=cfg.image_tag,
                       pull_policy=cfg.pull_policy, templates=templates,
                       activity_interval_s=cfg.activity_interval_s)
    app = server.App(cfg, mgr)
    rp = reaper.Reaper(mgr, mode=cfg.reap_mode, idle_s=cfg.idle_seconds,
                       sweep_s=cfg.sweep_seconds, log=app.log)
    rp.start()

    httpd = server.serve(app)
    app.log("网关就绪:0.0.0.0:%d · 命名空间 %s · 用户镜像标签 %s · 回收 %s(闲置 %d 秒)· "
            "信任来源 %s · 控制 API %s"
            % (cfg.port, cfg.namespace, cfg.image_tag, cfg.reap_mode, cfg.idle_seconds,
               "共享密钥" + ("+网段" if cfg.trust_cidrs else "") if cfg.trust_secret else "网段",
               "已启用" if cfg.admin_token else "未启用"))

    def stop(_signum, _frame):
        app.log("收到停止信号,正在退出(不回收任何用户 Pod)")
        rp.stop()
        # 另起一个线程关,避免在信号处理里 join 自己
        import threading
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
