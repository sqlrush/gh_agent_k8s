"""kb.py 的存储侧子命令:setup / index / feedback / eval(query / health 在查询 skill gaussdb-kb)。

全部确定性:连库、建表、索引、检索、打分。模型不参与。
口令来自 common.credential(凭据名写在 kb.yaml),这里不接收、不打印口令。
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional, Tuple

import yaml

from common.kb import config as kbconfig
from common.kb import indexer, query as kbquery, render
from common.kb import store_graph as sg
from common.kb import store_pg as spg
from common.kb.embed import Embedder, EmbedError
from common.kb import lock as kblock
from common.kb.atomic import write_text_atomic


class StoreCmdError(Exception):
    pass


def _cfg(kb: pathlib.Path) -> kbconfig.KbConfig:
    try:
        return kbconfig.load(kb)
    except kbconfig.KbConfigError as exc:
        raise StoreCmdError(str(exc))


def _password(name: str) -> str:
    from common.credential import CredentialError, load_secret
    try:
        return load_secret(name)
    except CredentialError as exc:
        raise StoreCmdError(
            f"取不到凭据 {name!r}:{exc}\n先运行 `python3 -m common.credential_cli set {name}` 加密保存口令")


def open_pg(cfg: kbconfig.KbConfig) -> spg.PgStore:
    if cfg.store.pg is None:
        raise StoreCmdError("kb.yaml 未配置 store.pg(高斯/PG 向量存储)。参考 references/storage-setup.md")
    p = cfg.store.pg
    try:
        return spg.PgStore.connect(p.host, p.port, p.database, p.user, _password(p.credential),
                                   dims=cfg.embeddings.dims, sslmode=p.sslmode)
    except spg.PgStoreError as exc:
        raise StoreCmdError(str(exc))


def open_graph(cfg: kbconfig.KbConfig, required: bool = False) -> Optional[sg.GraphStore]:
    if cfg.store.graph is None:
        if required:
            raise StoreCmdError("kb.yaml 未配置 store.graph(Neo4j)")
        return None
    g = cfg.store.graph
    store = sg.GraphStore(g.url, g.user, _password(g.credential), database=g.database)
    try:
        store.ping()
    except sg.GraphStoreError as exc:
        if required:
            raise StoreCmdError(str(exc))
        print(f"警告:图库不可用({exc}),本次只写高斯/PG;修好后重跑 index", file=sys.stderr)
        return None
    return store


def open_embedder(cfg: kbconfig.KbConfig, caps: spg.Capabilities) -> Optional[Embedder]:
    if not caps.vector:
        print("提示:存储引擎没有 vector 类型,本库只做词法 + 图(kb_meta.vector_engine=none)", file=sys.stderr)
        return None
    try:
        emb = Embedder.from_config(cfg)
    except (EmbedError, kbconfig.KbConfigError) as exc:
        print(f"警告:embedding 未启用({exc});向量列留空,检索只走词法 + 图", file=sys.stderr)
        return None
    if emb is None:
        print("提示:kb.yaml 未配 embeddings,向量列留空;配好后 `kb.py index --fill-missing` 补齐", file=sys.stderr)
    return emb


def _version(kb: pathlib.Path) -> str:
    try:
        return (kb / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# ---------------------------------------------------------------- setup

def cmd_setup(args: argparse.Namespace) -> int:
    kb = kbconfig.resolve_kb_dir(args.kb)
    cfg = _cfg(kb)
    if cfg.store.pg is None:
        print("高斯/PG   : kb.yaml 未配置 store.pg——文件模式(词法检索 + graph/*.yaml 图文件),无表可建")
        print("Neo4j     : " + ("未配置" if cfg.store.graph is None else "已配置,但没有 store.pg 时不启用(整体走文件模式)"))
        print("要向量库/图库时按 references/storage-setup.md 配好 kb.yaml 与凭据后重跑 setup;现在可直接 kb.py index / health")
        return 0
    pg = open_pg(cfg)
    try:
        caps = pg.setup()
    finally:
        pg.close()
    print(f"高斯/PG   : {caps.engine} · {caps.version.split(',')[0][:60]}")
    if caps.vector:
        engine = "datavec" if caps.engine == "opengauss" else "pgvector"
        index_note = "hnsw" if caps.hnsw else "无 hnsw(顺序扫描)"
        print(f"向量      : 有({engine}) · {index_note} · 维度 {caps.dims}")
    else:
        print("向量      : 无——只做词法 + 图")
    graph = open_graph(cfg)
    if graph is not None:
        graph.setup()
        print(f"Neo4j     : {graph.ping()} · 约束已建")
    else:
        print("Neo4j     : 未配置/不可用(图相关能力关闭)")
    print(f"embedding : {cfg.embeddings.source}" + (f" · {cfg.embeddings.model} · {cfg.embeddings.dims} 维"
                                                     if cfg.embeddings.source != 'none' else "(未启用)"))
    print("Next: kb.py index")
    return 0


# ---------------------------------------------------------------- index

def cmd_index(args: argparse.Namespace) -> int:
    kb = kbconfig.resolve_kb_dir(args.kb)
    if not kb.is_dir():
        raise StoreCmdError(f"KB 目录不存在:{kb}")
    cfg = _cfg(kb)
    pg = open_pg(cfg)
    try:
        caps = pg.setup()
        graph = open_graph(cfg)
        embedder = open_embedder(cfg, caps)
        rep = indexer.run_index(kb, pg, graph, embedder, kb_version=_version(kb),
                                rebuild=args.rebuild, fill_missing=args.fill_missing)
    finally:
        pg.close()
    print(f"文档      : 新写 {rep.docs_indexed} · 未变 {rep.docs_unchanged} · 删除 {rep.docs_removed} · 切块 {rep.chunks_written}")
    print(f"向量      : 引擎 {rep.vector_engine} · 新算 {rep.embedded} · 缓存 {rep.embed_cached} · 失败 {rep.embed_failed} · "
          f"覆盖 {rep.chunk_embedded}/{rep.chunk_total}")
    print(f"图        : {rep.graph} · 节点 {rep.nodes} · 边 {rep.edges}(已确认 {rep.edges_confirmed})")
    for w in rep.warnings:
        print(f"[warn ] {w}")
    if not rep.coverage_ok:
        print(f"[error] 向量覆盖率 {rep.chunk_embedded}/{rep.chunk_total} < 100%——"
              f"embedding 失败或未配置;修好后 `kb.py index --fill-missing`。未补齐前状态行会如实显示。")
        return 2
    if rep.graph == "unavailable":
        return 2
    return 0


# ---------------------------------------------------------------- feedback

def cmd_feedback(args: argparse.Namespace) -> int:
    kb = kbconfig.resolve_kb_dir(args.kb)
    path = kb / "eval" / "feedback.yaml"
    entry = {"id": args.id, "verdict": "useful" if args.useful else "irrelevant",
             "at": datetime.date.today().isoformat(), "note": args.note or ""}
    with kblock.hold(kb):                       # 追加也是写共享文件:持锁,整文件原子替换
        old = path.read_text(encoding="utf-8") if path.is_file() else ""
        write_text_atomic(path, old + "- " + json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"已记录:{entry['id']} → {entry['verdict']}(采纳率加权在下次 index 后生效)")
    return 0


# ---------------------------------------------------------------- eval

def cmd_eval(args: argparse.Namespace) -> int:
    """eval/queries.yaml:[{q, expect: [doc_id…], canary: bool}] → recall@k 与金丝雀命中。"""
    kb = kbconfig.resolve_kb_dir(args.kb)
    path = kb / "eval" / "queries.yaml"
    if not path.is_file():
        raise StoreCmdError(f"没有黄金查询集:{path}")
    try:
        cases = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except yaml.YAMLError as exc:
        raise StoreCmdError(f"queries.yaml 解析失败:{exc}")
    sess = kbquery.KbSession.open(kb)
    if not sess.attached:
        raise StoreCmdError(f"知识库未接入:{sess.reason}")
    k = int(args.k)
    hit = total = 0
    canary_miss: List[str] = []
    try:
        for c in cases:
            q = str(c.get("q") or "")
            expect = [str(e) for e in (c.get("expect") or [])]
            res = kbquery.from_text(q, session=sess)
            got: List[str] = []
            for it in res.items:      # 分类型各取前 k:条款和案例不互相挤占名额
                got += [r.id for r in it.clauses][:k] + [r.id for r in it.cases][:k] + [r.id for r in it.raws][:k]
            ok = any(e in got for e in expect) if expect else not got
            total += 1
            hit += int(ok)
            mark = "✓" if ok else "✗"
            print(f"[{mark}] {q[:50]} → {got[:3]}")
            if c.get("canary") and not ok:
                canary_miss.append(q)
    finally:
        sess.close()
    recall = hit / total if total else 0.0
    print(f"recall@{k}: {hit}/{total} = {recall:.2f}" + (f" · 金丝雀未中 {len(canary_miss)}" if canary_miss else ""))
    if canary_miss:
        for q in canary_miss:
            print(f"[error] 金丝雀未命中:{q}")
        return 2
    return 0 if recall >= float(args.min_recall) else 2


# ---------------------------------------------------------------- parser wiring

def add_admin_subcommands(sub: "argparse._SubParsersAction") -> None:
    """setup / feedback / eval:只在导入 skill 里。query / health 在 gaussdb-kb(查询)。"""
    p = sub.add_parser("setup", help="连接高斯/PG 与 Neo4j,建表建约束,报告引擎能力")
    p.add_argument("--kb")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("feedback", help="DBA 对一次引用打分:--useful / --irrelevant")
    p.add_argument("id")
    p.add_argument("--kb")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--useful", action="store_true")
    g.add_argument("--irrelevant", action="store_true")
    p.add_argument("--note")
    p.set_defaults(func=cmd_feedback)

    p = sub.add_parser("eval", help="跑 eval/queries.yaml,报 recall@k 与金丝雀")
    p.add_argument("--kb")
    p.add_argument("--k", default=3)
    p.add_argument("--min-recall", default=0.8)
    p.set_defaults(func=cmd_eval)
