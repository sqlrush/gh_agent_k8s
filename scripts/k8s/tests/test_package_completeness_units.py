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


def test_package_ships_the_whole_docs_tree_not_a_hardcoded_list():
    """文档清单写死过一次,加了两份手册却忘了加进那一行 —— 客户拿到的包里没有最重要的
    两份,而打包脚本一声不响地成功了。改成整个 docs/ 打进去,只排除内部用的。
    """
    body = _PACKAGER.read_text(encoding="utf-8")
    assert re.search(r"tar czf .*-k8s\.tar\.gz.*\\\n(.*\\\n)*\s*k8s docs\s*$", body, re.MULTILINE), \
        "k8s 包应该整个打 docs/,不要再列具体文件名"
    # specs/ 里是备选方案、退路、残余风险与未定项 —— 交付文档不写我们的讨论过程,
    # 而那一份从头到尾都是讨论过程。它随包发出去过,发现时补上这条守卫。
    # prototypes/ 是大盘设计稿:示例数字、给 user 拍板的方案对比、我们的建议 —— 全是讨论过程
    for internal in ("docs/plans", "docs/security", "docs/specs", "docs/prototypes"):
        assert "--exclude='%s'" % internal in body, "内部文档 %s 应该排除在交付包外" % internal


def test_package_does_not_ship_macos_resource_forks():
    """在 Mac 上打包时 bsdtar 会给每个带扩展属性的文件塞一个 `._<原名>`。

    客户解开后 docs/ 里会多出 `._参数手册.md` 这一堆,还带着打包机的
    com.apple.provenance 属性 —— 交付给客户的包里不该有这些。
    """
    body = _PACKAGER.read_text(encoding="utf-8")
    assert "COPYFILE_DISABLE=1 tar czf" in body, "打包 tar 前要设 COPYFILE_DISABLE=1"
    for pat in ("._*", ".DS_Store"):
        assert "--exclude='%s'" % pat in body, "还要显式排除 %s" % pat


def test_deploy_doc_handles_the_arch_suffix_the_packager_actually_writes():
    """**离线包里的标签带不带 `-<arch>` 后缀,取决于在哪台机器上打的包。**

    package-images.sh 里 `SUFFIX=""; [ "$ARCH" != "$HOST_ARCH" ] && SUFFIX="-$ARCH"` ——
    在 Mac(arm64)上打包时,arm64 那份没有后缀、amd64 那份是
    `gaussdb-agent-runtime:<TAG>-amd64`。而部署手册原来写的期望输出与重打标签
    命令都用不带后缀的标签:客户在 x86 集群上照着做,到 `docker tag` 那一步
    就是 "No such image",而前一步 `docker load` 是成功的。
    """
    pkg = _PACKAGER.read_text(encoding="utf-8")
    assert 'SUFFIX="-$ARCH"' in pkg, "打包脚本的架构后缀规则变了,这条守卫要跟着改"

    doc = (_ROOT / "docs" / "部署手册-从零到上线.md").read_text(encoding="utf-8")
    load = doc[doc.index("### 1.2"):doc.index("### 1.4")]
    assert "-amd64" in load, "部署手册没提离线包标签带架构后缀"
    assert "MANIFEST.txt" in load, "应指向 MANIFEST.txt —— 那里写的是实际标签"
    assert re.search(r"docker tag gaussdb-agent-\$img:\$TAG\$SFX\s+gaussdb-agent-\$img:\$TAG",
                     load), "缺「先去掉架构后缀」那一步"


def test_customer_facing_docs_exist_where_the_package_expects_them():
    """客户照着做的那几份必须都在 docs/ 下。"""
    for name in ("接入手册-SSO与K8s.md", "部署手册-从零到上线.md",
                 "命令卡-测试环境搭建.md", "快速搭建-K8s.md", "单机演示-不用K8s.md",
                 "参数手册.md", "对接清单-各方要做什么.md", "功能清单-容器版.md",
                 "仓库结构说明.md"):
        assert (_ROOT / "docs" / name).is_file(), "缺 docs/%s" % name
