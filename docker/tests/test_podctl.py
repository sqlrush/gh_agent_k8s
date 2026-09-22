"""podctl:Pod 生命周期里的确定性动作。每条都对着 spec §6.2 的一项措施。"""
import json
import os
import pathlib
import sqlite3
import sys
import time

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "docker"))

import podctl  # noqa: E402


# ---- 渲染配置 ---------------------------------------------------------------

def test_render_opencode_config_uses_env_and_disables_share():
    env = {"MODEL_BASE_URL": "http://llm.internal/v1", "MODEL_API_KEY": "k", "MODEL_ID": "qwen3-32b"}
    cfg = podctl.render_opencode_config(env)
    assert cfg["share"] == "disabled" and cfg["autoupdate"] is False
    assert cfg["permission"]["external_directory"] == "allow"
    prov = cfg["provider"]["model-service"]
    assert prov["npm"] == "@ai-sdk/openai-compatible"
    assert prov["options"] == {"baseURL": "http://llm.internal/v1", "apiKey": "k"}
    assert "qwen3-32b" in prov["models"] and cfg["model"] == "model-service/qwen3-32b"


def test_render_opencode_config_requires_model_vars():
    with pytest.raises(podctl.ConfigError) as exc:
        podctl.render_opencode_config({"MODEL_BASE_URL": "x"})
    assert "MODEL_API_KEY" in str(exc.value) and "MODEL_ID" in str(exc.value)


def test_render_opencode_config_matches_template_keys():
    """docker/opencode.jsonc.tmpl 是给人看的;它的顶层键必须和真正渲染出来的一致,否则文档骗人。"""
    tmpl = json.loads(podctl.strip_jsonc((_ROOT / "docker" / "opencode.jsonc.tmpl").read_text(encoding="utf-8")))
    cfg = podctl.render_opencode_config({"MODEL_BASE_URL": "u", "MODEL_API_KEY": "k", "MODEL_ID": "m"})
    assert set(tmpl) == set(cfg)


def test_render_gdaa_config_api_mode(tmp_path):
    text = podctl.render_gdaa_config({"GRMP_API_HOST": "grmp.internal", "GRMP_API_PORT": "8443"})
    assert "connection_mode: api" in text
    assert "host: grmp.internal" in text and "port: 8443" in text and "token_env: GRMP_AUTH_TOKEN" in text


def test_render_gdaa_config_requires_host():
    with pytest.raises(podctl.ConfigError):
        podctl.render_gdaa_config({})


# ---- .owner 独占 --------------------------------------------------------------

def test_owner_acquire_writes_marker(tmp_path):
    assert podctl.owner_acquire(tmp_path, "runtime-u1-abc", stale_s=90, wait_s=0) is True
    data = json.loads((tmp_path / ".owner").read_text(encoding="utf-8"))
    assert data["pod"] == "runtime-u1-abc" and time.time() - data["ts"] < 5


def test_owner_acquire_refuses_fresh_marker_of_another_pod(tmp_path):
    podctl.owner_acquire(tmp_path, "runtime-u1-old", stale_s=90, wait_s=0)
    assert podctl.owner_acquire(tmp_path, "runtime-u1-new", stale_s=90, wait_s=0) is False
    assert json.loads((tmp_path / ".owner").read_text())["pod"] == "runtime-u1-old"


def test_owner_acquire_takes_over_stale_marker(tmp_path):
    (tmp_path / ".owner").write_text(json.dumps({"pod": "runtime-u1-dead", "ts": time.time() - 3600}), encoding="utf-8")
    assert podctl.owner_acquire(tmp_path, "runtime-u1-new", stale_s=90, wait_s=0) is True
    assert json.loads((tmp_path / ".owner").read_text())["pod"] == "runtime-u1-new"


def test_owner_acquire_waits_until_marker_goes_stale(tmp_path, monkeypatch):
    (tmp_path / ".owner").write_text(json.dumps({"pod": "runtime-u1-old", "ts": time.time() - 88}), encoding="utf-8")
    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)
        (tmp_path / ".owner").write_text(json.dumps({"pod": "runtime-u1-old", "ts": time.time() - 200}), encoding="utf-8")

    monkeypatch.setattr(podctl.time, "sleep", fake_sleep)
    assert podctl.owner_acquire(tmp_path, "runtime-u1-new", stale_s=90, wait_s=55) is True
    assert sleeps == [5]


def test_owner_refresh_only_touches_own_marker(tmp_path):
    podctl.owner_acquire(tmp_path, "a", stale_s=90, wait_s=0)
    old = json.loads((tmp_path / ".owner").read_text())["ts"]
    time.sleep(0.01)
    assert podctl.owner_refresh(tmp_path, "a") is True
    assert json.loads((tmp_path / ".owner").read_text())["ts"] > old
    assert podctl.owner_refresh(tmp_path, "b") is False


def test_owner_release_only_removes_own_marker(tmp_path):
    podctl.owner_acquire(tmp_path, "a", stale_s=90, wait_s=0)
    podctl.owner_release(tmp_path, "b")
    assert (tmp_path / ".owner").exists()
    podctl.owner_release(tmp_path, "a")
    assert not (tmp_path / ".owner").exists()


# ---- SQLite:自检 / checkpoint / 备份 ---------------------------------------

def _make_db(path: pathlib.Path, rows: int = 3) -> None:
    con = sqlite3.connect(path)
    con.execute("pragma journal_mode=wal")
    con.execute("create table t(x)")
    con.executemany("insert into t values (?)", [(i,) for i in range(rows)])
    con.commit()
    con.close()


def test_db_check_passes_on_healthy_db(tmp_path, capsys):
    db = tmp_path / "opencode.db"
    _make_db(db)
    assert podctl.db_check(db, tmp_path / "backup") == "ok"
    assert "完整性检查通过" in capsys.readouterr().out


def test_db_check_missing_db_is_first_start(tmp_path, capsys):
    assert podctl.db_check(tmp_path / "opencode.db", tmp_path / "backup") == "absent"
    assert "首次启动" in capsys.readouterr().out


def test_db_check_restores_from_latest_backup(tmp_path, capsys):
    db = tmp_path / "opencode.db"
    bdir = tmp_path / "backup"
    bdir.mkdir()
    _make_db(bdir / "opencode.db.20260101T000000Z", rows=1)
    _make_db(bdir / "opencode.db.20260102T000000Z", rows=7)
    db.write_bytes(b"this is not a sqlite database at all" * 100)
    assert podctl.db_check(db, bdir) == "restored"
    out = capsys.readouterr().out
    assert "20260102T000000Z" in out and "损坏" in out
    assert list(tmp_path.glob("opencode.db.corrupt-*"))
    con = sqlite3.connect(db)
    assert con.execute("select count(*) from t").fetchone()[0] == 7
    con.close()


def test_db_check_without_backup_starts_empty_and_says_so(tmp_path, capsys):
    db = tmp_path / "opencode.db"
    db.write_bytes(b"garbage" * 100)
    assert podctl.db_check(db, tmp_path / "backup") == "reset"
    assert not db.exists() and "没有可用备份" in capsys.readouterr().out
    assert list(tmp_path.glob("opencode.db.corrupt-*"))


def test_backup_list_ignores_wal_and_shm_sidecars(tmp_path):
    """**备份清单只认真正的备份文件。**

    `opencode.db.*` 这个通配同时命中 `opencode.db.<时间戳>-wal` 与 `-shm` ——
    而 `-wal` 按字典序排在同名备份**之后**,于是「取最新一份」取到的是那个侧车文件。
    侧车常常是 0 字节,而 SQLite 把 0 字节文件当成一个合法的空库:
    自检于是打印「已从备份 …-wal 恢复」并返回 restored,用户的历史却全空了。
    真实形态:恢复成功的日志 + 空的对话列表,没有任何报错。
    """
    bdir = tmp_path / "backup"
    bdir.mkdir()
    _make_db(bdir / "opencode.db.20260102T000000Z", rows=7)
    (bdir / "opencode.db.20260102T000000Z-wal").write_bytes(b"")
    (bdir / "opencode.db.20260102T000000Z-shm").write_bytes(b"")
    (bdir / "opencode.db.corrupt-20260102T000000Z").write_bytes(b"garbage")

    names = [p.name for p in podctl._backups(bdir)]
    assert names == ["opencode.db.20260102T000000Z"], (
        "备份清单里混进了非备份文件:%s" % names)


def test_rotation_counts_only_real_backups(tmp_path):
    """轮转也走同一个清单。侧车被算进「份数」时,keep=3 实际留下的真备份不足 3 份。"""
    db = tmp_path / "opencode.db"
    _make_db(db)
    bdir = tmp_path / "backup"
    for i in range(1, 6):
        podctl.db_backup(db, bdir, keep=3, now="2026010%dT000000Z" % i)
    kept = [p.name for p in podctl._backups(bdir)]
    assert len(kept) == 3, "应留 3 份真备份,实际 %s" % kept
    assert kept == ["opencode.db.20260103T000000Z",
                    "opencode.db.20260104T000000Z",
                    "opencode.db.20260105T000000Z"]


def test_empty_file_is_not_a_usable_backup(tmp_path):
    """0 字节文件能被 SQLite 正常打开,`integrity_check` 也回 ok ——
    拿它当备份恢复,等于用一个空库覆盖并宣告成功。"""
    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    assert not podctl._integrity_ok(empty)


def test_db_checkpoint_truncates_wal(tmp_path):
    db = tmp_path / "opencode.db"
    _make_db(db, rows=50)
    con = sqlite3.connect(db)
    con.execute("insert into t values (99)")
    con.commit()                                  # 留着连接:最后一个连接关闭时 SQLite 会自己删 -wal,那不是我们要测的
    wal = db.with_name("opencode.db-wal")
    assert wal.stat().st_size > 0
    podctl.db_checkpoint(db)                      # 另开连接做 TRUNCATE checkpoint
    assert wal.stat().st_size == 0
    con.close()


def test_db_backup_rotates(tmp_path):
    db = tmp_path / "opencode.db"
    _make_db(db)
    bdir = tmp_path / "backup"
    for i in range(5):
        p = podctl.db_backup(db, bdir, keep=3, now=f"2026010{i+1}T000000Z")
        assert p.name == f"opencode.db.2026010{i+1}T000000Z"
    names = sorted(x.name for x in bdir.iterdir())
    assert names == ["opencode.db.20260103T000000Z", "opencode.db.20260104T000000Z", "opencode.db.20260105T000000Z"]
    con = sqlite3.connect(bdir / names[-1])
    assert con.execute("select count(*) from t").fetchone()[0] == 3
    con.close()


def test_log_prune_removes_old_logs_only(tmp_path):
    old = tmp_path / "a.log"
    old.write_text("x")
    os.utime(old, (time.time() - 10 * 86400,) * 2)
    new = tmp_path / "b.log"
    new.write_text("x")
    other = tmp_path / "keep.txt"
    other.write_text("x")
    os.utime(other, (time.time() - 10 * 86400,) * 2)
    assert podctl.log_prune(tmp_path, days=7) == [old]
    assert not old.exists() and new.exists() and other.exists()


# ---- 命令行 -----------------------------------------------------------------

def test_cli_render_writes_files_and_reports_missing_vars(tmp_path, monkeypatch, capsys):
    for k in ("MODEL_BASE_URL", "MODEL_API_KEY", "MODEL_ID", "GRMP_API_HOST"):
        monkeypatch.delenv(k, raising=False)
    assert podctl.main(["render-opencode-config", "--out", str(tmp_path / "oc.json")]) == 2
    assert "MODEL_ID" in capsys.readouterr().err
    monkeypatch.setenv("MODEL_BASE_URL", "u")
    monkeypatch.setenv("MODEL_API_KEY", "k")
    monkeypatch.setenv("MODEL_ID", "m")
    monkeypatch.setenv("GRMP_API_HOST", "h")
    assert podctl.main(["render-opencode-config", "--out", str(tmp_path / "oc.json")]) == 0
    assert podctl.main(["render-gdaa-config", "--out", str(tmp_path / "config.yaml")]) == 0
    assert json.loads((tmp_path / "oc.json").read_text())["share"] == "disabled"
    assert oct((tmp_path / "oc.json").stat().st_mode & 0o777) == "0o600"       # 里面有 key


def test_render_gdaa_config_appkey_requires_private_key():
    """2026-09-18 中间件加固:平台配了 GRMP_APPKEY 却没注入 SM2 私钥,启动时就拒——
    否则表现成每个请求被中间件拒,排查方向被带到中间件那边。"""
    env = {"GRMP_API_HOST": "grmp.internal", "GRMP_APPKEY": "gaussdb-agent"}
    with pytest.raises(podctl.ConfigError) as info:
        podctl.render_gdaa_config(env)
    assert "GRMP_SM2_PRIVATE_KEY" in str(info.value) and "grmp-sm2" in str(info.value)
    text = podctl.render_gdaa_config({**env, "GRMP_SM2_PRIVATE_KEY": "ab" * 32})
    assert "appkey_env: GRMP_APPKEY" in text
    assert "appkey_env" not in podctl.render_gdaa_config({"GRMP_API_HOST": "grmp.internal"})   # 没开签名不写这行
