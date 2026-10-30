import shutil
import subprocess
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging/host/gamescope/displays/asus.rogflowz13.lcd.lua"
MODEGEN = Path("/usr/share/gamescope/scripts/00-gamescope/common/modegen.lua")


@pytest.fixture(scope="module")
def luajit():
    executable = shutil.which("luajit")
    if not executable:
        pytest.skip("LuaJIT is required to execute Gamescope's Lua display script")
    return executable


def run_lua(luajit, body, modegen=None):
    env = {**os.environ, "Z13_GAMESCOPE_SCRIPT": str(SCRIPT)}
    if modegen:
        env["Z13_GAMESCOPE_MODEGEN"] = str(modegen)
    result = subprocess.run(
        [luajit, "-e", body],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_panel_match_is_exact(luajit, real_subprocess):
    run_lua(
        luajit,
        r'''
gamescope = { config = { known_displays = {} }, eotf = { gamma22 = 22 }, modegen = {} }
function debug(_) end
function warn(_) end
dofile(os.getenv("Z13_GAMESCOPE_SCRIPT"))
local display = { vendor = "TMA", product = 0x0803, model = "TL134ADXP03" }
assert(gamescope.config.known_displays.rogflowz13_lcd.matches(display) == 5000)
display.model = "other TMA panel"
assert(gamescope.config.known_displays.rogflowz13_lcd.matches(display) == -1)
display.model = "TL134ADXP03"
display.product = 0x0804
assert(gamescope.config.known_displays.rogflowz13_lcd.matches(display) == -1)
''',
    )


def test_dynamic_modes_follow_installed_gamescope_helpers_and_preserve_base(luajit, real_subprocess):
    if not MODEGEN.is_file():
        pytest.skip("Installed Gamescope modegen helper is unavailable")
    run_lua(
        luajit,
        r'''
gamescope = { config = { known_displays = {} }, eotf = { gamma22 = 22 }, modegen = {} }
function debug(_) end
function warn(_) end
dofile(os.getenv("Z13_GAMESCOPE_MODEGEN"))
dofile(os.getenv("Z13_GAMESCOPE_SCRIPT"))
local panel = gamescope.config.known_displays.rogflowz13_lcd
assert(#panel.dynamic_refresh_rates == 133)
assert(panel.dynamic_refresh_rates[1] == 48 and panel.dynamic_refresh_rates[133] == 180)
local base = {
    hdisplay = 2560, vdisplay = 1600, hsync_start = 2608, hsync_end = 2640,
    htotal = 2720, vsync_start = 1606, vsync_end = 1612, vtotal = 1775,
    clock = 869040, vrefresh = 180,
}
for _, requested in ipairs({48, 49, 60, 90, 120, 144, 179, 180}) do
    local generated = panel.dynamic_modegen(base, requested)
    assert(generated ~= base)
    assert(generated.hdisplay == 2560 and generated.vdisplay == 1600)
    assert(generated.clock == 869040)
    assert(generated.vrefresh >= requested and generated.vrefresh <= requested + 1)
    assert(generated.vsync_start > generated.vdisplay)
end
assert(base.vtotal == 1775 and base.vsync_start == 1606)
assert(panel.dynamic_modegen(base, 47) == base)
assert(panel.dynamic_modegen(base, 181) == base)
''',
        MODEGEN,
    )


def test_installer_manifest_includes_gamescope_script(real_subprocess):
    result = subprocess.run(
        ["python3", str(ROOT / "tooling/install_check.py"), "manifest"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "packaging/host/gamescope/displays/asus.rogflowz13.lcd.lua" in result.stdout
    assert ".config/gamescope/scripts/00-gamescope/displays/asus.rogflowz13.lcd.lua" in result.stdout
