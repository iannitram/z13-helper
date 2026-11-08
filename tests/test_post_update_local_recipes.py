"""Local recipes and optional packs 8-9 (issue #72)."""

import pytest

from post_update import driver, packs
from post_update.builtin import HostIO
from post_update.local import parse_local
from post_update.local_recipes import load_local_recipes, local_recipe

GOOD = '''id = "hello"
title = "Say hello"
fixes = "nothing"
check = "test -f '/tmp/a b'"
apply = "touch '/tmp/a b'"
sudo = true
'''


def make_io(files=None, run=None, listing=None):
    files = files if files is not None else {}
    return HostIO(read=files.get, write=lambda p, t: files.__setitem__(p, t),
                  exists=lambda p: p in files, listdir=lambda p: (listing or {}).get(p, []),
                  run=run or (lambda argv: (0, "")))


SUPPORTED = {"/etc/os-release": "ID=steamos\nBUILD_ID=20261101.1\n",
             "/sys/class/dmi/id/product_name": "GZ302EA\n",
             "/proc/sys/kernel/osrelease": "7.2.7\n"}


def test_local_recipe_runs_argv_without_a_shell_and_sudo_only_on_apply():
    calls = []
    r = local_recipe(parse_local(GOOD), lambda argv: calls.append(argv) or (0, ""))
    assert r.check() == "ok" and r.sudo and not r.default_on
    r.apply()
    assert calls == [["test", "-f", "/tmp/a b"], ["sudo", "-n", "touch", "/tmp/a b"]]


def test_local_recipe_failures():
    r = local_recipe(parse_local(GOOD), lambda argv: (1, "no"))
    assert r.check() == "needs"
    with pytest.raises(RuntimeError, match="hello failed"):
        r.apply()
    with pytest.raises(ValueError, match="check is not a valid command line"):
        local_recipe(parse_local(GOOD.replace("test -f '/tmp/a b'", "test -f 'x")), lambda a: (0, ""))


def test_load_local_recipes_bad_file_is_an_error_and_others_load(tmp_path):
    d = tmp_path / ".config/z13-helper/post-update.d"
    d.mkdir(parents=True)
    (d / "a.toml").write_text(GOOD)
    (d / "bad.toml").write_text("id = ")
    (d / "clash.toml").write_text(GOOD.replace('"hello"', '"keyring"'))
    (d / "quote.toml").write_text(GOOD.replace('"hello"', '"quote"').replace("test -f '/tmp/a b'", "test -f 'x"))
    recipes, errors = load_local_recipes(str(tmp_path), lambda a: (0, ""), taken=["keyring"])
    assert [r.id for r in recipes] == ["hello"]
    assert len(errors) == 3
    assert errors[0].startswith("bad.toml: invalid TOML")
    assert "keyring: id is already used" in errors[1]
    assert errors[2].startswith("quote: check is not a valid command line")


# --- optional packs ---

def test_packs_are_off_by_default_and_na_off_device():
    rs = packs.pack_recipes(make_io({}))
    assert [r.id for r in rs] == ["dev-headers", "coredump-cap"]
    assert not any(r.default_on for r in rs)
    assert [r.check() for r in rs] == ["na", "na"]


def test_coredump_cap_check_and_apply():
    files = dict(SUPPORTED)
    runs = []
    io = make_io(files, run=lambda argv: runs.append(argv) or (0, ""))
    r = packs.coredump_cap_recipe(io)
    assert r.check() == "needs" and r.replaces == (packs.COREDUMP_CONF,)
    r.apply()
    assert runs == [["mkdir", "-p", "/etc/systemd/coredump.conf.d"]]
    assert files[packs.COREDUMP_CONF] == packs.COREDUMP_TEXT and r.check() == "ok"


def test_coredump_cap_refuses_off_device():
    r = packs.coredump_cap_recipe(make_io({}))
    with pytest.raises(RuntimeError, match="refusing"):
        r.apply()


PQL = ("glib2 /usr/lib/pkgconfig/glib-2.0.pc\nglib2 /usr/lib/libglib.so\n"
       "zlib /usr/lib/pkgconfig/zlib.pc\nfoo /opt/x/foo.pc\n")


def test_dev_headers_extracts_only_missing_pc_from_cache():
    files = dict(SUPPORTED, **{"/usr/lib/pkgconfig/zlib.pc": ""})
    runs = []

    def run(argv):
        runs.append(argv)
        return 0, {"-Ql": PQL, "-Q": "glib2 2.80-1\nzlib 1.3-1\n"}.get(argv[1], "")

    cache = {packs.PACMAN_CACHE: ["glib2-2.80-1-x86_64.pkg.tar.zst.sig", "glib2-2.80-1-x86_64.pkg.tar.zst",
                                  "glib2-2.79-1-x86_64.pkg.tar.zst"]}
    r = packs.dev_headers_recipe(make_io(files, run=run, listing=cache))
    assert r.check() == "needs" and r.pacman and r.network and not r.default_on
    r.apply()
    assert runs[-1] == ["bsdtar", "-xkf", packs.PACMAN_CACHE + "/glib2-2.80-1-x86_64.pkg.tar.zst", "-C", "/",
                        "usr/lib/pkgconfig/glib-2.0.pc"]
    assert not any("--overwrite" in " ".join(a) for a in runs)


def test_dev_headers_ok_when_nothing_missing():
    files = dict(SUPPORTED, **{"/usr/lib/pkgconfig/glib-2.0.pc": "", "/usr/lib/pkgconfig/zlib.pc": ""})
    r = packs.dev_headers_recipe(make_io(files, run=lambda a: (0, PQL)))
    assert r.check() == "ok"


def test_dev_headers_downloads_when_not_cached():
    files = dict(SUPPORTED)
    runs, cache = [], {packs.PACMAN_CACHE: []}

    def run(argv):
        runs.append(argv)
        if argv[:2] == ["pacman", "-Sw"]:
            cache[packs.PACMAN_CACHE].append("glib2-2.80-1-x86_64.pkg.tar.zst")
        return 0, {"-Ql": "glib2 /usr/lib/pkgconfig/g.pc\n", "-Q": "glib2 2.80-1\n"}.get(argv[1], "")

    packs.dev_headers_recipe(make_io(files, run=run, listing=cache)).apply()
    assert ["pacman", "-Sw", "--noconfirm", "glib2"] in runs and runs[-1][0] == "bsdtar"


def test_pacman_query_needs_no_root_but_mkdir_and_bsdtar_do():
    assert not driver._needs_root(["pacman", "-Ql"])
    assert driver._needs_root(["pacman", "-Sw", "x"]) and driver._needs_root(["mkdir", "-p", "/etc/x"])
    assert driver._needs_root(["bsdtar", "-xkf", "a"]) and not driver._needs_root(["tailscale", "status"])


def _local_home(tmp_path, monkeypatch, calls):
    d = tmp_path / ".config/z13-helper/post-update.d"
    d.mkdir(parents=True)
    (d / "a.toml").write_text(GOOD)
    (d / "bad.toml").write_text("id = ")
    monkeypatch.setattr(driver, "make_hostio", lambda escalate=True: make_io({}))
    monkeypatch.setattr(driver, "_run", lambda argv, timeout=600, escalate=True: calls.append(argv) or (0, ""))
    return str(tmp_path)


def test_collect_puts_local_recipes_in_the_same_list_with_error_rows(tmp_path, monkeypatch):
    calls = []
    home = _local_home(tmp_path, monkeypatch, calls)
    recipes, errors = driver.collect(home)
    ids = [r.id for r in recipes]
    assert ids[-1] == "hello" and "dev-headers" in ids and "coredump-cap" in ids
    assert len(errors) == 1 and errors[0].startswith("bad.toml: invalid TOML")


def test_a_local_recipe_runs_through_the_runner_and_a_bad_file_does_not_stop_it(tmp_path, monkeypatch, capsys):
    calls = []
    home = _local_home(tmp_path, monkeypatch, calls)
    monkeypatch.setenv("HOME", home)
    monkeypatch.setattr(driver.os, "geteuid", lambda: 1000)
    # `check` says needs (rc 1), so the apply runs
    monkeypatch.setattr(driver, "_run", lambda argv, timeout=600, escalate=True: calls.append(argv) or (
        (1, "") if argv[0] == "test" else (0, "")))
    checks = []
    monkeypatch.setattr(driver, "_run_plain", lambda argv: checks.append(argv) or (1, ""))
    rc = driver.main(["--post-update", "--only", "hello"], env=driver.Env(
        backup=lambda p: None, readonly_disable=lambda: None, readonly_enable=lambda: None))
    out = capsys.readouterr()
    assert rc == 0 and "hello: done" in out.out
    assert ["sudo", "-n", "touch", "/tmp/a b"] in calls
    assert checks and not any(a[0] == "sudo" for a in checks)   # a check never gets sudo
    assert "local recipe skipped: bad.toml" in out.err
