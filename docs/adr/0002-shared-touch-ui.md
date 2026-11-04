# Shared design tokens and a large-tile Qt layout for the touch-first interface

**Status:** Accepted (Phase 16d, #132)

The desktop app and the Decky plugin share one touch-first interface through **Option (b): a single JSON design-token file (`src/design_tokens.json`) consumed by `src/theme.py` and `decky/src/`, paired with a large-tile PyQt6 layout (`>= 44 px` touch targets) in `src/dashboard_ui.py` and `src/fans_window.py`**. We do not embed a desktop web view hosting the Decky React components (**Option (a)**).

## Context

On the ASUS ROG Flow Z13 (GZ302EA, 13.4" 2560×1600 touchscreen, SteamOS), the machine is frequently used as a tablet with the folio keyboard detached or folded back. Today's two frontends share profile and Max Fans state (`~/.config/z13-helper/`), but not visual or touch geometry:

- The **desktop app** (`src/dashboard_ui.py`, `src/fans_window.py`, `src/theme.py`) ports G-Helper's mouse-oriented layout: `20 px` pills, buttons and combos, `14 px` checkboxes, `16 px` slider handles, and `29–36 px` secondary tiles.
- The **Decky plugin** (`decky/src/`) renders inside Steam's Quick Access Menu (QAM) using `@decky/ui` controls with inline hex colours (`#1a9fff`, `#ffb02e`) that differ from `src/theme.py` (`#3AAEEF`, `#FF8000`).

Phase 16d (#132) chooses how the two frontends share one set of parts when Phase 16e (#133) lands the shared design system and touch-first tablet layout.

## Considered options

### Option (a): Desktop web view hosting the Decky React components against a local bridge

Embed `PyQt6.QtWebEngineWidgets.QWebEngineView` inside the desktop app's main window, serve a bundled React tree over a localhost HTTP or `QWebChannel` bridge backed by `src/profile_control.py`, `src/hardware_controller.py` and `src/sensor_reader.py`, and render the TSX components from `decky/src/`.

Inspecting the built Decky bundle and `@decky/ui` (`^4.11.0`) shows two immediate structural obstacles before runtime cost is even counted:

1. **`@decky/ui` is not a standalone DOM/CSS component library.** Its exports (`PanelSection`, `PanelSectionRow`, `SliderField`, `ToggleField`, `DropdownItem`, `DialogButton`, `Focusable`, `Field`, `staticClasses`) resolve at runtime against Valve's internal webpack modules (`CommonUIModule`, `SP_REACT`, `SP_JSX`) inside Steam's `SharedJSContext` CEF browser (`window.__DECKY_SECRET_INTERNALS_...`). Outside Steam's CEF, importing the bundle fails immediately unless we build and maintain our own standalone React + CSS re-implementation of `@decky/ui` and `@decky/api` (`callable`, `toaster`, `useQuickAccessVisible`).
2. **Desktop-only surfaces are absent from `decky/src/`.** The desktop app owns the interactive 8-point `QPainter` fan curve editor (`src/fan_curve_widget.py` in `src/fans_window.py`), the Updates and post-update repair runner (`src/updates_dialog.py`), the Error log and fan-trace viewer (`src/log_dialog.py`), `QSystemTrayIcon`, `QMenuBar`, and KDE `org.kde.ScreenBrightness` D-Bus integration. Hosting `decky/src/` in a web view either leaves the desktop app split across two widget toolkits or forces rewriting those desktop windows in React as well.

### Option (b): Shared design tokens and a matching large-tile Qt layout (chosen)

Keep native toolkits in each frontend—PyQt6 widgets in the desktop app and `@decky/ui` focusable QAM controls in the Decky plugin—unified by one canonical, dependency-free token file (`src/design_tokens.json`) from the shared design pass:

- **Shared tokens (`src/design_tokens.json`):** surface, text and border palette; semantic accents per base platform profile and goal (`quiet` `#06B48A`, `balanced` `#3AAEEF`, `performance` `#FF2020`, custom/warning `#FF8000`); typography sizes; corner radii; spacing scale; and touch-target metrics (`touch_min_px`: `44`, primary/secondary tile heights, slider groove and handle hit boxes).
- **Desktop app (`src/theme.py`, `src/dashboard_ui.py`, `src/fans_window.py`):** `src/theme.py` reads `src/design_tokens.json` via stdlib `json` and drives touch-sized `ModeTile` cards, `>= 44 px` combos/buttons/checkbox rows, and wide-handle touch sliders across both desktop windows. `flatpak/io.github.iannitram.Z13Helper.yml` already copies `src/` into `/app/share/z13-helper/src/`.
- **Decky plugin (`decky/src/tokens.ts` and `decky/src/components/`):** Rollup bundles `src/design_tokens.json` into `decky/dist/index.js` at build time, so no extra runtime file copy is needed in `py_modules/` (see [ADR 0001](0001-decky-imports-root-owned-copies.md)). Components keep `@decky/ui` `Focusable`, `PanelSection` and `SliderField` so Game Mode controller and touch focus keep working with a game running (`SPEC.md` M9), while replacing inline colour and spacing literals with the shared tokens.

## Measurements for Option (a) vs Option (b)

Measured on the target GZ302EA hardware under SteamOS 3.9.5 against the installed `io.github.iannitram.Z13Helper` Flatpak (`org.kde.Platform//6.10`, `PyQt6==6.11.0`, `QT_QPA_PLATFORM=offscreen`, 5 cold runs per case):

| Metric | Option (a): `QWebEngineView` + local JSON bridge | Option (b): Shared tokens + PyQt6 widgets | Delta for (a) |
|---|---|---|---|
| Flatpak app installed size | **~531.0 MB** (`253.7 MB` base + `277.3 MB` unpacked `PyQt6-WebEngine` wheels) | **253.7 MB** (`files/` = `252.5 MB`; tokens `< 4 KB`) | **+277.3 MB (+109 %)** |
| Extra wheel download at Flatpak build | `116.59 MB` (`PyQt6-WebEngine-Qt6==6.11.0` `116.27 MB` + `PyQt6-WebEngine==6.11.0` `0.32 MB`) | `0 MB` | **+116.6 MB** |
| Cold start (wall-clock, process spawn → first rendered frame) | **266 ms** median (runs: `250, 264, 266, 267, 300 ms`) | **173 ms** median (runs: `155, 169, 173, 178, 186 ms`) | **+93 ms (+54 %)** |
| Cold start (in-process, `QApplication` → first rendered frame) | **198 ms** median (runs: `185, 197, 198, 200, 231 ms`) | **143 ms** median (runs: `129, 141, 143, 147, 156 ms`) | **+55 ms (+38 %)** |
| Resident memory (`VmRSS`, process tree) | **516 MB** across **4 processes** (`python3` + `QtWebEngineProcess` helpers) | **65 MB** in **1 process** | **+451 MB (~7.9×)** |

Notes on the measurements:

- `org.kde.Platform//6.10` ships `libQt6WebChannel` and `libQt6WebSockets`, **not** `libQt6WebEngineCore`, and Flathub's `io.qt.qtwebengine.BaseApp` ends at Qt `6.4`. Option (a) must therefore bundle `PyQt6-WebEngine-Qt6==6.11.0` (`276.03 MB` unpacked) + `PyQt6-WebEngine==6.11.0` (`1.29 MB` unpacked) inside the app Flatpak itself.
- The desktop app runs permanently resident in the tray via `z13-helper.service`. Keeping four Chromium helper processes at ~516 MB `VmRSS` idle in Desktop Mode is disproportionate for a hardware tray utility (and moves in the wrong direction relative to `#30`), plus Chromium's user-namespace sandbox collides with Flatpak's `bwrap` without `zypak` or `--no-sandbox`.

## Trade-off summary

| Criterion | Option (a): Web view + Decky React + bridge | Option (b): Shared JSON tokens + large-tile Qt |
|---|---|---|
| **Flatpak size** | More than doubles the installed Flatpak (`253.7 MB` → `~531 MB`, `+277.3 MB`), plus `react`/`react-dom` and a `@decky/ui` shim. | Zero new runtime packages; `< 4 KB` JSON file inside `src/`. |
| **Touch behaviour** | Chromium handles touch in a separate renderer compositor: kinetic scroll, text selection, long-press callouts and fractional HiDPI scaling under KWin Wayland must be tamed in CSS/JS, and slider drags add bridge IPC latency. | Native KWin Wayland touch events go directly to in-process Qt widgets with native HiDPI scaling, `QScroller` where needed, and `>= 44 px` hit boxes on tiles, sliders, combos and checkboxes. |
| **Shared design system** | Still maintains two widget implementations (`@decky/ui` in Steam CEF vs custom DOM/CSS shim in QtWebEngine) and leaves `FansWindow`, `UpdatesDialog` and `LogDialog` in Qt unless rewritten. | One `src/design_tokens.json` governs palette, semantic accents, typography, spacing, radii and `>= 44 px` touch geometry across every desktop window and the QAM panel. |
| **Test cost** | Requires adding Node + DOM/browser test tooling (`jsdom`/Playwright), a `@decky/ui` mock layer, and bridge lifecycle tests to CI (`.github/workflows/tests.yml` currently runs stdlib + `pytest` and `unittest` only). | Pure-Python `pytest` tests validate token schema, desktop/Decky colour parity, and `>= 44 px` touch-target constants without importing `PyQt6` (`SPEC.md` Testing decisions); `tooling/screenshots.py` renders offscreen inside the existing Flatpak. |

## Decision and Phase 16e (#133) scope

We pick **Option (b)**. Phase 16e (#133) is scoped accordingly:

- **16e Where:** `src/design_tokens.json`, `src/theme.py`, `src/dashboard_ui.py`, `src/fans_window.py`, `decky/src/tokens.ts` (and token consumers in `decky/src/`), `MAP/README.md`, `MAP/decky.md`, `docs/ROADMAP.md` §10b Phase 16. No `PyQt6-WebEngine` dependency, local HTTP/`QWebChannel` bridge, or `@decky/ui` DOM shim.
- **16e Done when:**
  - `src/design_tokens.json` defines the shared design tokens (surfaces, text, borders, semantic profile/goal accents, radii, spacing, and `>= 44 px` minimum touch target metrics), loaded by `src/theme.py` in the desktop app and imported at build time by `decky/src/tokens.ts` in the Decky plugin, with `pytest` checks (no `PyQt6` import) verifying token parity and `>= 44 px` touch minimums.
  - `src/dashboard_ui.py` and `src/fans_window.py` rebuild the desktop app as large touch-first tiles and controls where every interactive target is at least `44 px` tall/wide and every control from the current windows still exists.
  - `decky/src/` consumes the shared accent and spacing tokens while keeping `@decky/ui` `Focusable` QAM navigation.
  - Screenshots in tablet (keyboard off) and laptop use are in the PR, and Flatpak dependencies remain unchanged (no `QtWebEngine`).
