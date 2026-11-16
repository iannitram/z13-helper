"""Built-in post-update recipes 4 (etc snapshot) and 6 (ROCm) (issue #71): read-only checks, applies on a fake host."""

import os

import pytest

from post_update.builtin_4_6 import (
    ROCM_COMPAT_LIBS, ROCM_LD_CONF, ROCM_LD_TEXT, ROCM_LIB_DIR, ROCM_PROFILE, ROCM_PROFILE_TEXT, SNAPSHOT_DEST_REL,
    SNAPSHOT_SRC, Sys, builtin_recipes_4_6, etc_snapshot_recipe, rocm_recipe,
)
from post_update.runner import Env, check_all, run_plan

HOME = "/home/deck"
DEST = os.path.join(HOME, SNAPSHOT_DEST_REL)


class FakeHost:
    """A fake tree. Records every write so tests can assert checks never write."""

    def __init__(self, dirs=None, files=None, rc=None, ldd_out=""):
        self.ldd_out = ldd_out
        self.dirs = dict(dirs or {})          # path -> list of names
        self.files = dict(files or {})        # path -> text
        self.rc = dict(rc or {})              # argv[0] -> exit code (default 0)
        self.log = []

    def sys(self):
        return Sys(
            home=HOME,
            listdir=lambda p: None if p not in self.dirs else list(self.dirs[p]),
            exists=lambda p: p in self.files or p in self.dirs,
            read=lambda p: self.files.get(p),
            run=self._run,
            write=self._write,
            copy_tree=self._copy,
            run_out=self._run_out,
        )

    def _run_out(self, argv):
        self.log.append(("run", tuple(argv)))
        if argv[0] == "ldd":
            return 0, self.ldd_out
        return self.rc.get(argv[0], 0), ""

    def _run(self, argv):
        self.log.append(("run", tuple(argv)))
        return self.rc.get(argv[0], 0)

    def _write(self, path, text):
        self.log.append(("write", path))
        self.files[path] = text

    def _copy(self, src, dst):
        self.log.append(("copy", src, dst))
        self.dirs[dst] = [dst]
        self.dirs.setdefault(os.path.dirname(dst), [])

    def writes(self):
        return [x for x in self.log if x[0] in ("write", "copy")]


# ---- task 4: /etc snapshot ----

def test_snapshot_absent_is_not_applicable():
    h = FakeHost()
    assert etc_snapshot_recipe(h.sys()).check() == "na"


def test_snapshot_present_and_not_copied_needs_copy():
    h = FakeHost(dirs={SNAPSHOT_SRC: ["20261101", "20261102"], DEST: ["20261101"]})
    assert etc_snapshot_recipe(h.sys()).check() == "needs"


def test_snapshot_all_copied_is_ok():
    h = FakeHost(dirs={SNAPSHOT_SRC: ["20261101"], DEST: ["20261101"]})
    assert etc_snapshot_recipe(h.sys()).check() == "ok"


def test_snapshot_check_never_writes():
    h = FakeHost(dirs={SNAPSHOT_SRC: ["a", "b"]})
    check_all((etc_snapshot_recipe(h.sys()),))
    assert h.writes() == []


def test_snapshot_apply_copies_only_missing_and_is_idempotent():
    h = FakeHost(dirs={SNAPSHOT_SRC: ["a", "b"], DEST: ["a"]})
    r = etc_snapshot_recipe(h.sys())
    r.apply()
    assert [x for x in h.log if x[0] == "copy"] == [
        ("copy", os.path.join(SNAPSHOT_SRC, "b"), os.path.join(DEST, "b"))]
    h.log.clear()
    h.dirs[DEST] = ["a", "b"]
    r.apply()
    assert h.writes() == []


def test_snapshot_flags_and_id():
    r = etc_snapshot_recipe(FakeHost().sys())
    assert (r.id, r.sudo, r.default_on, r.pacman, r.reboot) == ("etc-snapshot", True, True, False, False)


# ---- task 6: ROCm ----

def test_rocm_hidden_when_opt_rocm_missing():
    h = FakeHost()
    assert rocm_recipe(h.sys()).check() == "na"
    assert h.log == []  # nothing runs when the pack doesn't apply


def test_rocm_ok_when_rocminfo_runs_and_both_files_present():
    h = FakeHost(dirs={"/opt/rocm": []},
                 files={ROCM_LD_CONF: ROCM_LD_TEXT, ROCM_PROFILE: ROCM_PROFILE_TEXT})
    assert rocm_recipe(h.sys()).check() == "ok"


def test_rocm_needs_when_rocminfo_fails():
    h = FakeHost(dirs={"/opt/rocm": []}, rc={"/opt/rocm/bin/rocminfo": 1},
                 files={ROCM_LD_CONF: "", ROCM_PROFILE: ""})
    assert rocm_recipe(h.sys()).check() == "needs"


@pytest.mark.parametrize("present", [ROCM_LD_CONF, ROCM_PROFILE])
def test_rocm_needs_when_one_file_is_missing(present):
    files = {present: "x"}
    h = FakeHost(dirs={"/opt/rocm": []}, files=files)
    assert rocm_recipe(h.sys()).check() == "needs"


def test_rocm_check_never_writes_or_installs():
    h = FakeHost(dirs={"/opt/rocm": []})
    rocm_recipe(h.sys()).check()
    assert all(x[0] == "run" and x[1] == ("/opt/rocm/bin/rocminfo",) for x in h.log)


_LIB = ROCM_COMPAT_LIBS[0]
_PKG = HOME + "/.cache/z13-helper/rocm-compat/" + _LIB["url"].rsplit("/", 1)[1]
_COMPAT_FETCH = [
    ("run", ("curl", "-fL", "--create-dirs", "-o", _PKG, _LIB["url"])),
    ("run", ("sh", "-c", f'echo "{_LIB["sha256"]}  {_PKG}" | sha256sum -c -')),
    ("run", ("bsdtar", "-xf", _PKG, "-C", ROCM_LIB_DIR, "--strip-components=2", "usr/lib/libfmt.so.11*")),
]
_LIBFMT11 = ROCM_LIB_DIR + "/libfmt.so.11"


def test_rocm_apply_installs_then_writes_then_ldconfig_in_order():
    h = FakeHost(dirs={"/opt/rocm": []})
    rocm_recipe(h.sys()).apply()
    assert h.log == [
        ("run", ("pacman", "-S", "--needed", "--noconfirm", "google-glog")),
        *_COMPAT_FETCH,
        ("write", ROCM_LD_CONF),
        ("run", ("ldconfig",)),
        ("write", ROCM_PROFILE),
        ("run", ("ldd", "/opt/rocm/bin/rocminfo")),
        ("run", ("/opt/rocm/bin/rocminfo",)),
    ]
    assert h.files[ROCM_LD_CONF] == ROCM_LD_TEXT
    assert h.files[ROCM_PROFILE] == ROCM_PROFILE_TEXT


def test_rocm_postcheck_fails_naming_the_missing_library(capsys):
    h = FakeHost(dirs={"/opt/rocm": []},
                 ldd_out="\tlibfmt.so.11 => not found\n\tlibc.so.6 => /usr/lib/libc.so.6 (0x1)\n")
    with pytest.raises(RuntimeError, match=r"libfmt\.so\.11"):
        rocm_recipe(h.sys()).apply()
    assert "libfmt.so.11" in capsys.readouterr().out


def test_rocm_postcheck_failure_marks_task_failed_and_rolls_back():
    h = FakeHost(dirs={"/opt/rocm": []}, ldd_out="\tlibfmt.so.11 => not found\n")
    restored = []
    env = Env(backup=lambda p: None, readonly_disable=lambda: None, readonly_enable=lambda: None,
              restore=lambda p, b: restored.append((p, b)))
    assert run_plan((rocm_recipe(h.sys()),), env) == [("rocm", "failed")]
    assert (_LIBFMT11, None) in restored  # the compat library this run added is removed again


def test_rocm_postcheck_fails_when_rocminfo_itself_exits_nonzero():
    h = FakeHost(dirs={"/opt/rocm": []}, rc={"/opt/rocm/bin/rocminfo": 3})
    with pytest.raises(RuntimeError, match="rocminfo exited with code 3"):
        rocm_recipe(h.sys()).apply()


def test_rocm_postcheck_passes_is_done(capsys):
    h = FakeHost(dirs={"/opt/rocm": []}, ldd_out="\tlibfmt.so.11 => /opt/rocm/lib/libfmt.so.11 (0x1)\n")
    env = Env(backup=lambda p: None, readonly_disable=lambda: None, readonly_enable=lambda: None)
    assert run_plan((rocm_recipe(h.sys()),), env) == [("rocm", "done")]
    assert capsys.readouterr().out == ""


def test_rocm_compat_library_already_present_downloads_nothing():
    h = FakeHost(dirs={"/opt/rocm": []}, files={_LIBFMT11: "x"})
    r = rocm_recipe(h.sys())
    assert r.creates() == []
    r.apply()
    argvs = [x[1][0] for x in h.log if x[0] == "run"]
    assert "curl" not in argvs and "bsdtar" not in argvs


def test_rocm_compat_download_failure_stops_before_any_file_is_written():
    h = FakeHost(dirs={"/opt/rocm": []}, rc={"curl": 22})
    with pytest.raises(RuntimeError, match="libfmt.so.11"):
        rocm_recipe(h.sys()).apply()
    assert ROCM_LD_CONF not in h.files


def test_rocm_compat_checksum_failure_stops_before_extracting():
    h = FakeHost(dirs={"/opt/rocm": []}, rc={"sh": 1})
    with pytest.raises(RuntimeError, match="checksum"):
        rocm_recipe(h.sys()).apply()
    assert "bsdtar" not in [x[1][0] for x in h.log if x[0] == "run"]


def test_rocm_creates_lists_both_compat_files_when_absent():
    h = FakeHost(dirs={"/opt/rocm": []})
    assert rocm_recipe(h.sys()).creates() == [_LIBFMT11, ROCM_LIB_DIR + "/libfmt.so.11.2.0"]


def test_rocm_apply_stops_when_pacman_fails():
    h = FakeHost(dirs={"/opt/rocm": []}, rc={"pacman": 1})
    with pytest.raises(RuntimeError, match="pacman"):
        rocm_recipe(h.sys()).apply()
    assert ROCM_LD_CONF not in h.files


def test_rocm_apply_raises_when_ldconfig_fails():
    h = FakeHost(dirs={"/opt/rocm": []}, rc={"ldconfig": 1})
    with pytest.raises(RuntimeError, match="ldconfig"):
        rocm_recipe(h.sys()).apply()
    assert ROCM_PROFILE not in h.files


def test_rocm_flags_and_backups():
    r = rocm_recipe(FakeHost().sys())
    assert (r.id, r.sudo, r.network, r.pacman, r.default_on, r.reboot) == ("rocm", True, True, True, False, False)
    assert r.replaces == (ROCM_LD_CONF, ROCM_PROFILE)


def test_rocm_runs_through_runner_with_backups_and_readonly_toggle():
    h = FakeHost(dirs={"/opt/rocm": []})
    backed = []
    readonly = []
    env = Env(backup=backed.append,
              readonly_disable=lambda: readonly.append("off"),
              readonly_enable=lambda: readonly.append("on"))
    assert run_plan((rocm_recipe(h.sys()),), env) == [("rocm", "done")]
    assert backed == [ROCM_LD_CONF, ROCM_PROFILE]
    assert readonly == ["off", "on"]


def test_builtin_recipes_4_6_in_order():
    assert [r.id for r in builtin_recipes_4_6(FakeHost().sys())] == ["etc-snapshot", "rocm"]
