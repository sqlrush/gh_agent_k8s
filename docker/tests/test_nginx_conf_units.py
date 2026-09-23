"""前端镜像的 nginx 配置守卫。起真 nginx 太重,对 conf 文本钉住几条不能回退的约束:

· 只服务 /dash/,别的路径 404 —— 这个容器只端大盘页面,不该顺手端出别的东西;
· /dash/health 这类无斜杠路径要 try_files 回落到 index.html(shell.js 按 pathname 切页);
· /dash 无斜杠要 301 到 /dash/(否则相对解析全错);
· no-cache:大盘改版要立刻生效;
· autoindex off:目录列表关掉;
· /healthz 给探针;
· 监听 8080:nginx-unprivileged 镜像非 root,绑不了 80。
"""
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CONF = (_ROOT / "docker" / "nginx-dash.conf").read_text(encoding="utf-8")
_DOCKERFILE = (_ROOT / "docker" / "Dockerfile.frontend").read_text(encoding="utf-8")


def test_listens_on_8080_as_unprivileged_image_requires():
    assert re.search(r"listen\s+8080\s*;", _CONF)
    assert re.search(r"^FROM\s+nginxinc/nginx-unprivileged:\d[\w.\-]*", _DOCKERFILE, re.M), "基础镜像要钉明确标签,不能 latest"


def test_only_dash_is_served_and_root_is_404():
    assert re.search(r"location\s+/dash/\s*\{", _CONF)
    assert re.search(r"location\s+/\s*\{[^}]*return\s+404", _CONF, re.S), "非 /dash/ 路径要 404"


def test_spa_fallback_and_slashless_redirect():
    assert re.search(r"try_files\s+\$uri\s+\$uri/\s+/dash/index\.html", _CONF), "/dash/health 要回落到 index.html"
    assert re.search(r"location\s*=\s*/dash\s*\{[^}]*return\s+301\s+/dash/", _CONF, re.S), "/dash 要 301 到 /dash/"


def test_no_cache_no_autoindex_and_healthz():
    assert re.search(r"add_header\s+Cache-Control\s+\"?no-cache", _CONF)
    assert re.search(r"autoindex\s+off", _CONF)
    assert re.search(r"location\s*=\s*/healthz\s*\{[^}]*return\s+200", _CONF, re.S)


def test_dockerfile_copies_frontend_and_conf_only():
    assert "COPY frontend/ /usr/share/nginx/html/dash/" in _DOCKERFILE
    assert "COPY docker/nginx-dash.conf /etc/nginx/conf.d/default.conf" in _DOCKERFILE
    assert "agent/" not in _DOCKERFILE and "gateway/" not in _DOCKERFILE, "前端镜像里不该有技能或网关代码"
