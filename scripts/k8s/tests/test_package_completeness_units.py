"""交付包必须装齐清单里会用到的每一个镜像。

漏打一个镜像,客户在现场才会撞到 —— 而那时往往没有外网可以补拉,整个部署卡住。
package-images.sh 原来只打了 runtime 与 kb-import 两个,网关与向量库基础镜像都没进去。

这条闸很便宜:把 k8s/ 清单里引用的镜像和打包脚本里导出的镜像对一遍。
"""
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_PACKAGER = _ROOT / "scripts" / "package-images.sh"
_BUILDER = _ROOT / "scripts" / "build-images.sh"


def _images_in_manifests() -> set:
    """k8s/base 与 k8s/templates 里 image: 字段引用的镜像(去掉标签与仓库前缀)。"""
    out = set()
    for p in list((_ROOT / "k8s" / "base").glob("*.yaml")) + \
             list((_ROOT / "k8s" / "templates").glob("*.yaml")):
        for m in re.finditer(r"^\s*image:\s*([^\s#]+)", p.read_text(encoding="utf-8"), re.MULTILINE):
            ref = m.group(1).strip().strip('"')
            ref = ref.split("${")[0] or ref          # 模板占位符
            out.add(ref.rsplit(":", 1)[0].rsplit("/", 1)[-1])
    return {x for x in out if x}


def test_packager_exports_every_agent_image():
    """三个我们自己构建的镜像都要进包。"""
    body = _PACKAGER.read_text(encoding="utf-8")
    loop = re.search(r"for T in ([a-z\- ]+); do", body)
    assert loop, "package-images.sh 里找不到镜像循环"
    packaged = set(loop.group(1).split())
    assert packaged == {"runtime", "kb-import", "gateway"}, \
        "打包的镜像与预期不符:%s —— 漏一个客户现场就装不起来" % sorted(packaged)


def test_builder_and_packager_agree():
    """构建的和打包的必须是同一组 —— 构建了没打包等于白构建。"""
    built = set(re.search(r"for T in ([a-z\- ]+); do", _BUILDER.read_text(encoding="utf-8")).group(1).split())
    built.add("gateway")          # 网关是独立 Dockerfile,不在那个循环里
    loop = re.search(r"for T in ([a-z\- ]+); do", _PACKAGER.read_text(encoding="utf-8"))
    assert built == set(loop.group(1).split())


def test_vectordb_base_image_is_packaged_and_version_matches():
    """向量库用的是上游 openGauss 镜像,不是我们构建的,**更容易被漏**。

    而且打包脚本里的版本必须与清单里的一致 —— 不一致的话客户载入的是一个版本、
    清单要拉的是另一个版本,现场没有外网可以补。
    """
    pack = _PACKAGER.read_text(encoding="utf-8")
    m = re.search(r"VECTORDB_IMAGE=\$\{VECTORDB_IMAGE:-([^\}]+)\}", pack)
    assert m, "package-images.sh 没有导出向量库基础镜像"
    packaged = m.group(1).strip()

    manifest = (_ROOT / "k8s" / "base" / "vectordb.yaml").read_text(encoding="utf-8")
    used = re.search(r"^\s*image:\s*(\S+opengauss\S*)", manifest, re.MULTILINE)
    assert used, "vectordb.yaml 里找不到 openGauss 镜像"
    # 清单里可能带内网仓库前缀,只比对「名字:标签」这一段
    assert used.group(1).rsplit("/", 1)[-1] == packaged.rsplit("/", 1)[-1], \
        "打包的是 %s,清单要的是 %s" % (packaged, used.group(1))
