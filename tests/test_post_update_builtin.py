"""Built-in post-update recipes 1-3 (issue #69): checks on sample host data, applies on fakes, runner use."""

import pytest

from post_update.builtin import (AMDGPU_ARGS, CMDLINE, DMI_PRODUCT, GRUB_FILE, HostIO,
                                 KEEP_DIR, OS_RELEASE, builtin_recipes, supported_device)
from post_update.recipe import order_tasks
from post_update.runner import Env, run_plan

STEAMOS = 'ID=steamos\nVERSION_ID=3.7\nBUILD_ID="20260101"\n'
GZ302 = "ROG Flow Z13 GZ302EA\n"
STOCK_CMDLINE = "BOOT_IMAGE=/vmlinuz root=UUID=1 quiet splash\n"
HEALTHY_CMDLINE = "BOOT_IMAGE=/vmlinuz root=UUID=1 quiet " + " ".join(AMDGPU_ARGS) + " splash\n"
STOCK_GRUB = 'GRUB_TIMEOUT=0\nGRUB_CMDLINE_LINUX_DEFAULT="quiet splash"\n# GRUB_CMDLINE_LINUX_DEFAULT="x"\n'
HEALTHY_GRUB = 'GRUB_TIMEOUT=0\nGRUB_CMDLINE_LINUX_DEFAULT="quiet splash ' + " ".join(AMDGPU_ARGS) + '"\n'
OUR_CONF = "/etc/a\n"


def keys(n):
    return "/etc/pacman.d/gnupg/pubring.gpg\n-------\n" + "".join(
        f"pub   rsa4096 2020-01-01 [SC]\n      ABCD{i:04d}\nuid   [ full ] Dev {i}\n\n" for i in range(n))


class FakeHost:
    """Records every write and command; answers reads from a dict; runs answer from a table."""

    def __init__(self, files=None, listing=None, runs=None):
        self.files = dict(files or {})
        self.listing = dict(listing or {})
        self.runs_table = dict(runs or {})
        self.writes = []
        self.commands = []

    def io(self):
        def read(path):
            return self.files.get(path)

        def write(path, text):
            self.writes.append((path, text))
            self.files[path] = text

        def run(argv):
            self.commands.append(list(argv))
            return self.runs_table.get(tuple(argv), (0, ""))

        return HostIO(read=read, write=write,
                      exists=lambda p: p in self.files,
                      listdir=lambda d: self.listing.get(d, []),
                      run=run)


def host(*, steamos=True, gz302=True, grub=STOCK_GRUB, cmdline=STOCK_CMDLINE, files=None, listing=None, runs=None):
    base = {
        OS_RELEASE: STEAMOS if steamos else "ID=debian\n",
        DMI_PRODUCT: GZ302 if gz302 else "Some Laptop\n",
        CMDLINE: cmdline,
        GRUB_FILE: grub,
    }
    base.update(files or {})
    return FakeHost(files=base, listing=listing, runs=runs)


def recipe(h, id):
    return next(r for r in builtin_recipes(h.io()) if r.id == id)


# --- gate ---------------------------------------------------------------------

def test_supported_only_on_steamos_gz302():
    assert supported_device(host().io())
    assert not supported_device(host(steamos=False).io())
    assert not supported_device(host(gz302=False).io())


def test_recipe_ids_and_flags_match_the_plan():
    h = host()
    rs = {r.id: r for r in builtin_recipes(h.io())}
    assert set(rs) == {"amdgpu", "keyring", "carry-forward"}
    assert rs["amdgpu"].reboot and rs["amdgpu"].sudo and rs["amdgpu"].default_on
    assert rs["keyring"].pacman and rs["keyring"].sudo
    assert not any((rs["carry-forward"].sudo, rs["carry-forward"].reboot, rs["carry-forward"].pacman))
    assert [r.id for r in order_tasks(builtin_recipes(h.io()))] == ["keyring", "carry-forward", "amdgpu"]


# --- 1. amdgpu ------------------------------------------------------------------

def test_amdgpu_check_needs_on_a_stock_cmdline():
    assert recipe(host(), "amdgpu").check() == "needs"


def test_amdgpu_check_ok_when_cmdline_and_grub_carry_all_args():
    assert recipe(host(grub=HEALTHY_GRUB, cmdline=HEALTHY_CMDLINE), "amdgpu").check() == "ok"


def test_amdgpu_check_needs_when_only_grub_has_the_args_reboot_pending():
    assert recipe(host(grub=HEALTHY_GRUB), "amdgpu").check() == "needs"


def test_amdgpu_check_needs_when_grub_has_no_line():
    assert recipe(host(grub="GRUB_TIMEOUT=0\n"), "amdgpu").check() == "needs"


def test_amdgpu_check_does_not_read_a_commented_grub_line():
    # Only a commented line carries the args: the active line (which apply edits) has none.
    commented = '# GRUB_CMDLINE_LINUX_DEFAULT="quiet ' + " ".join(AMDGPU_ARGS) + '"\n'
    grub = "GRUB_TIMEOUT=0\n" + commented + 'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"\n'
    assert recipe(host(grub=grub, cmdline=HEALTHY_CMDLINE), "amdgpu").check() == "needs"


def test_amdgpu_check_reads_the_active_line_after_a_commented_one():
    # A stock commented line comes first; the active line has every arg.
    grub = ('# GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"\n'
            'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash ' + " ".join(AMDGPU_ARGS) + '"\n')
    assert recipe(host(grub=grub, cmdline=HEALTHY_CMDLINE), "amdgpu").check() == "ok"


def test_amdgpu_check_needs_when_the_only_grub_line_is_commented():
    grub = '#GRUB_CMDLINE_LINUX_DEFAULT="quiet ' + " ".join(AMDGPU_ARGS) + '"\n'
    assert recipe(host(grub=grub, cmdline=HEALTHY_CMDLINE), "amdgpu").check() == "needs"


@pytest.mark.parametrize("kw", [dict(steamos=False), dict(gz302=False)])
def test_amdgpu_check_is_na_off_device(kw):
    assert recipe(host(**kw), "amdgpu").check() == "na"


def test_amdgpu_check_never_writes_or_runs():
    h = host()
    recipe(h, "amdgpu").check()
    assert h.writes == [] and h.commands == []


def test_amdgpu_apply_adds_args_keeps_other_lines_and_runs_update_grub():
    h = host()
    recipe(h, "amdgpu").apply()
    path, text = h.writes[0]
    assert path == GRUB_FILE
    assert text.startswith('GRUB_TIMEOUT=0\nGRUB_CMDLINE_LINUX_DEFAULT="quiet splash ')
    assert text.endswith('"\n# GRUB_CMDLINE_LINUX_DEFAULT="x"\n')
    assert all(arg in text for arg in AMDGPU_ARGS)
    assert h.commands == [["update-grub"]]


def test_amdgpu_apply_is_idempotent():
    h = host()
    r = recipe(h, "amdgpu")
    r.apply()
    once = h.files[GRUB_FILE]
    r.apply()
    assert h.files[GRUB_FILE] == once
    assert len(h.writes) == 1  # the second apply finds nothing to change
    assert h.commands == [["update-grub"], ["update-grub"]]


def test_amdgpu_apply_refuses_off_device_without_writing_or_running():
    h = host(gz302=False)
    with pytest.raises(RuntimeError):
        recipe(h, "amdgpu").apply()
    assert h.writes == [] and h.commands == []


def test_amdgpu_apply_fails_when_update_grub_fails():
    h = host(runs={("update-grub",): (1, "boom")})
    with pytest.raises(RuntimeError, match="update-grub failed"):
        recipe(h, "amdgpu").apply()


def test_amdgpu_apply_fails_without_the_grub_line_and_writes_nothing():
    h = host(grub="GRUB_TIMEOUT=0\n")
    with pytest.raises(RuntimeError):
        recipe(h, "amdgpu").apply()
    assert h.writes == []


def test_amdgpu_backs_up_grub_before_apply_through_the_runner():
    h = host()
    log = []
    env = Env(backup=lambda p: log.append(f"backup:{p}"),
              readonly_disable=lambda: log.append("ro-off"),
              readonly_enable=lambda: log.append("ro-on"))
    assert run_plan([recipe(h, "amdgpu")], env) == [("amdgpu", "done")]
    assert log == [f"backup:{GRUB_FILE}"]


# --- 2. pacman keyring ------------------------------------------------------------

def test_keyring_check_ok_with_enough_keys_and_needs_when_empty():
    assert recipe(host(runs={("pacman-key", "--list-keys"): (0, keys(180))}), "keyring").check() == "ok"
    assert recipe(host(), "keyring").check() == "needs"


def test_keyring_check_needs_when_listing_fails():
    h = host(runs={("pacman-key", "--list-keys"): (1, keys(180))})
    assert recipe(h, "keyring").check() == "needs"


def test_keyring_check_is_na_off_device_and_never_writes():
    h = host(gz302=False)
    assert recipe(h, "keyring").check() == "na"
    assert h.commands == []


def test_keyring_apply_runs_init_then_populate_in_order():
    h = host()
    recipe(h, "keyring").apply()
    assert h.commands == [["pacman-key", "--init"], ["pacman-key", "--populate"]]


def test_keyring_apply_stops_after_a_failed_init():
    h = host(runs={("pacman-key", "--init"): (2, "gpg: no")})
    with pytest.raises(RuntimeError, match="--init failed"):
        recipe(h, "keyring").apply()
    assert h.commands == [["pacman-key", "--init"]]


def test_keyring_apply_refuses_off_device():
    h = host(steamos=False)
    with pytest.raises(RuntimeError):
        recipe(h, "keyring").apply()
    assert h.commands == []


def test_keyring_runs_inside_readonly_off_window_through_the_runner():
    h = host()
    log = []
    env = Env(backup=lambda p: log.append(f"backup:{p}"),
              readonly_disable=lambda: log.append("ro-off"),
              readonly_enable=lambda: log.append("ro-on"))
    assert run_plan([recipe(h, "keyring")], env) == [("keyring", "done")]
    assert log == ["ro-off", "backup:/etc/pacman.d/gnupg", "ro-on"]   # keyring folder backed up first
    # The runner calls the read-only check first (--list-keys), then the two applies.
    assert h.commands == [["pacman-key", "--list-keys"], ["pacman-key", "--init"], ["pacman-key", "--populate"]]


# --- 3. carry-forward list health ------------------------------------------------------

def _listing(ours, others=()):
    files = {f"{KEEP_DIR}/z13-bucket1.conf": ours}
    listing = ["z13-bucket1.conf"]
    for name, text in others:
        files[f"{KEEP_DIR}/{name}"] = text
        listing.append(name)
    return dict(files=files, listing={KEEP_DIR: listing})


def test_carry_forward_ok_when_every_path_exists():
    spec = _listing(OUR_CONF, [("50-other.conf", "/etc/b\n")])
    h = host(files=spec["files"], listing=spec["listing"])
    h.files["/etc/a"] = "x"
    h.files["/etc/b"] = "x"
    assert recipe(h, "carry-forward").check() == "ok"


def test_carry_forward_is_info_never_needs_when_our_file_lists_a_missing_path():
    spec = _listing(OUR_CONF, [("50-other.conf", "/etc/b\n")])
    h = host(files=spec["files"], listing=spec["listing"])
    h.files["/etc/b"] = "x"
    assert recipe(h, "carry-forward").check() == "info"


def test_carry_forward_is_info_when_only_another_file_is_missing():
    spec = _listing("/etc/a\n", [("50-other.conf", "/etc/b\n")])
    h = host(files=spec["files"], listing=spec["listing"])
    h.files["/etc/a"] = "x"
    assert recipe(h, "carry-forward").check() == "info"


def test_carry_forward_is_na_off_device_and_never_writes():
    h = host(gz302=False, files=_listing(OUR_CONF)["files"], listing=_listing(OUR_CONF)["listing"])
    assert recipe(h, "carry-forward").check() == "na"
    assert h.writes == [] and h.commands == []


def test_carry_forward_apply_changes_nothing():
    h = host(files=_listing(OUR_CONF)["files"], listing=_listing(OUR_CONF)["listing"])
    recipe(h, "carry-forward").apply()
    assert h.writes == [] and h.commands == []
