#!/usr/bin/env bash
# Z13-Helper — Flatpak build helper.
#
# Run from the project root:
#   flatpak/build.sh
#
# Re-runs are safe (cache is reused). --force-clean wipes the prior build dir.

set -euo pipefail

cd "$(dirname "$0")/.."

MANIFEST="flatpak/io.github.iannitram.Z13Helper.yml"
BUILD_DIR="build-flatpak"
APP_ID="io.github.iannitram.Z13Helper"

if ! command -v flatpak >/dev/null; then
    echo "flatpak not installed" >&2
    exit 1
fi

# Ensure flatpak-builder is available (as a Flatpak)
if ! flatpak --user info org.flatpak.Builder >/dev/null 2>&1 \
   && ! flatpak --system info org.flatpak.Builder >/dev/null 2>&1; then
    echo "Installing org.flatpak.Builder…"
    flatpak install --user --noninteractive flathub org.flatpak.Builder
fi

# Ensure runtime + sdk are present (either scope)
need_install=()
flatpak info org.kde.Platform//6.10 >/dev/null 2>&1 || need_install+=(org.kde.Platform//6.10)
flatpak info org.kde.Sdk//6.10      >/dev/null 2>&1 || need_install+=(org.kde.Sdk//6.10)
if [[ ${#need_install[@]} -gt 0 ]]; then
    echo "Installing missing runtime(s): ${need_install[*]}"
    flatpak install --user --noninteractive flathub "${need_install[@]}"
fi

echo "Building $APP_ID …"
flatpak run org.flatpak.Builder \
    --user --install --force-clean \
    --install-deps-from=flathub \
    "$BUILD_DIR" "$MANIFEST"

echo
echo "Done. Try:  flatpak run $APP_ID --show"
