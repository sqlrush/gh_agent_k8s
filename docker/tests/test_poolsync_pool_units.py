"""poolsync 为固定池化加的能力(2026-10-23):两侧权限、版本令牌、看不见的子目录不传播删除、锁目录不搬。"""
import os
import pathlib
import stat
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "docker"))

import fdtree  # noqa: E402
import podctl  # noqa: E402
import poolsync as ps  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    users, state = tmp_path / "users", tmp_path / "state"
    users.mkdir()
    state.mkdir()
    ufd, sfd = fdtree.open_root(users), fdtree.open_root(state)
    me = ps.open_user(ufd, "u1234")
    yield users / "u1234", state, me, sfd
    for fd in (ufd, sfd, me):
        os.close(fd)


def _mode(p: pathlib.Path) -> int:
    return stat.S_IMODE(p.lstat().st_mode)


def test_load_into_a_slot_disk_uses_group_shared_modes(env):
    nas, state, me, sfd = env
    (nas / "workspace").mkdir(mode=0o700)
    (nas / "workspace" / "notes.md").write_text("x")
    os.chmod(nas / "workspace" / "notes.md", 0o600)
    ps.load(me, sfd, log=lambda m: None, modes=ps.SLOT_MODES)
    assert _mode(state / "workspace") == 0o2770 and _mode(state / "workspace" / "notes.md") == 0o660
    assert _mode(state / "xdg-data") == 0o2770, "新用户也按槽位权限建空目录"


def test_sync_back_to_nas_is_private_again(env):
    nas, state, me, sfd = env
    (state / "workspace").mkdir(mode=0o2770)
    (state / "workspace" / "a.md").write_text("x")
    os.chmod(state / "workspace" / "a.md", 0o660)
    ps.sync(me, sfd, {}, modes=ps.NAS_MODES)
    assert _mode(nas / "workspace" / "a.md") == 0o600 and _mode(nas / "workspace") == 0o700


@pytest.mark.skipif(os.geteuid() == 0, reason="root 无视权限位")
def test_an_unreadable_slot_directory_never_deletes_its_nas_copy(env):
    """槽位里 0700 的目录管家进不去 —— 看不见不等于没有,NAS 上那份不能被删除传播清掉。"""
    nas, state, me, sfd = env
    (nas / "workspace" / "secret-proj").mkdir(parents=True)
    (nas / "workspace" / "secret-proj" / "plan.md").write_text("keep me")
    (state / "workspace" / "secret-proj").mkdir(parents=True)
    (state / "workspace" / "secret-proj" / "plan.md").write_text("keep me")
    os.chmod(state / "workspace" / "secret-proj", 0o000)
    try:
        moved = ps.sync(me, sfd, {}, may_delete=True, modes=ps.NAS_MODES)
    finally:
        os.chmod(state / "workspace" / "secret-proj", 0o700)
    assert (nas / "workspace" / "secret-proj" / "plan.md").read_text() == "keep me"
    assert any("没有权限进入" in w for w in moved.warnings)


def test_opencode_lock_directories_are_neither_copied_nor_pruned(env):
    nas, state, me, sfd = env
    (nas / "xdg-state" / "opencode" / "locks" / "old.lock").mkdir(parents=True)
    (state / "xdg-state" / "opencode" / "locks" / "x.lock").mkdir(parents=True)
    (state / "xdg-state" / "opencode" / "locks" / "x.lock" / "pid").write_text("1")
    (state / "xdg-state" / "opencode" / "kv.json").write_text("{}")
    ps.sync(me, sfd, {}, modes=ps.NAS_MODES)
    assert (nas / "xdg-state" / "opencode" / "kv.json").exists()
    assert not (nas / "xdg-state" / "opencode" / "locks" / "x.lock").exists()


def test_gen_rotates_to_a_fresh_random_token(env):
    nas, _state, me, _sfd = env
    assert ps.gen_read(me) == ""
    a = ps.gen_rotate(me)
    b = ps.gen_rotate(me)
    assert len(a) == 32 and a != b and ps.gen_read(me) == b
    assert _mode(nas / ps.GEN) == 0o600


def test_a_planted_gen_link_is_not_followed(env):
    nas, _state, me, _sfd = env
    victim = nas.parent / "victim-gen"
    victim.write_text("other")
    os.symlink(victim, nas / ps.GEN)
    assert ps.gen_read(me) == ""
    ps.gen_rotate(me)
    assert victim.read_text() == "other" and not (nas / ps.GEN).is_symlink()


def test_owner_holder_is_compared_as_a_whole(env):
    """固定池化下持有者是「pod/槽位」:同一个 Pod 的另一个槽位不能当成自己。"""
    _nas, _state, me, _sfd = env
    assert ps.owner_acquire(me, "pool-3/1", now=1000)[0]
    ok, holder, _wait = ps.owner_acquire(me, "pool-3/2", now=1001)
    assert not ok and holder == "pool-3/1"


def test_db_fingerprint(env):
    nas, _state, me, _sfd = env
    assert ps.db_fingerprint(me) is None
    (nas / "xdg-data" / "opencode").mkdir(parents=True)
    (nas / "xdg-data" / "opencode" / "opencode.db").write_bytes(b"x" * 10)
    ino, _mt, size = ps.db_fingerprint(me)
    assert size == 10 and ino == (nas / "xdg-data" / "opencode" / "opencode.db").stat().st_ino


def test_on_demand_owner_acquire_rotates_gen_too(tmp_path):
    """按需模式下用过之后,池化里这个人的旧保留副本必须作废。"""
    assert podctl.owner_acquire(tmp_path, "runtime-u1234-abc", wait_s=0)
    first = (tmp_path / podctl.GEN_NAME).read_text()
    assert podctl.owner_acquire(tmp_path, "runtime-u1234-abc", wait_s=0)
    assert (tmp_path / podctl.GEN_NAME).read_text() != first
