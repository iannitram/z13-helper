"""ADR 0002 (issue #132): shared touch-first UI decision, measurements, and 16e (#133) scope."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADR = ROOT / "docs" / "adr" / "0002-shared-touch-ui.md"
ROADMAP = ROOT / "docs" / "ROADMAP.md"
CODE_MAP = ROOT / "MAP" / "README.md"
FLATPAK_MANIFEST = ROOT / "flatpak" / "io.github.iannitram.Z13Helper.yml"


def test_adr_0002_exists_and_records_both_options_and_pick():
    assert ADR.is_file(), "docs/adr/0002-shared-touch-ui.md must exist"
    text = ADR.read_text(encoding="utf-8")

    # Both architectural options from #132 must be evaluated.
    assert "Option (a)" in text and "QWebEngineView" in text
    assert "Option (b)" in text and "src/design_tokens.json" in text
    assert "@decky/ui" in text

    # Decision clearly picks Option (b) and rejects embedding a desktop web view.
    assert "We pick **Option (b)**" in text
    assert "We do not embed a desktop web view" in text


def test_adr_0002_records_flatpak_size_and_start_time_measurements_for_option_a():
    text = ADR.read_text(encoding="utf-8")

    # Flatpak size baseline and Option (a) WebEngine delta must be quantified in MB.
    assert re.search(r"253\.7\s*MB", text), "baseline Flatpak size missing"
    assert re.search(r"277\.3\s*MB", text), "PyQt6-WebEngine unpacked size delta missing"
    assert re.search(r"531(\.0)?\s*MB", text), "Option (a) total Flatpak size missing"

    # Cold-start measurements in ms for both Option (a) and Option (b).
    assert re.search(r"266\s*ms", text) and re.search(r"198\s*ms", text), (
        "Option (a) wall-clock and in-process start-time measurements missing"
    )
    assert re.search(r"173\s*ms", text) and re.search(r"143\s*ms", text), (
        "Option (b) wall-clock and in-process start-time measurements missing"
    )


def test_adr_0002_weighs_all_four_criteria():
    text = ADR.read_text(encoding="utf-8")
    for heading in ("Flatpak size", "Touch behaviour", "Shared design system", "Test cost"):
        assert heading in text, f"missing evaluation criterion: {heading}"


def test_phase_16e_where_and_done_when_updated_to_match_pick():
    adr_text = ADR.read_text(encoding="utf-8")
    roadmap_text = ROADMAP.read_text(encoding="utf-8")

    for doc_name, text in (("ADR 0002", adr_text), ("docs/ROADMAP.md", roadmap_text)):
        assert "16e" in text and "#133" in text, f"{doc_name} must reference 16e / #133"
        for expected_path in (
            "src/design_tokens.json",
            "src/theme.py",
            "src/dashboard_ui.py",
            "src/fans_window.py",
            "decky/src/tokens.ts",
        ):
            assert expected_path in text, f"{doc_name} 16e Where missing {expected_path}"
        assert "44 px" in text, f"{doc_name} 16e Done when missing 44 px touch target requirement"
        assert "no `PyQt6-WebEngine`" in text or "No `PyQt6-WebEngine`" in text, (
            f"{doc_name} must rule out PyQt6-WebEngine for 16e"
        )


def test_code_map_and_flatpak_manifest_agree_with_adr_0002():
    map_text = CODE_MAP.read_text(encoding="utf-8")
    assert "docs/adr/0002-shared-touch-ui.md" in map_text
    assert "src/design_tokens.json" in map_text
    assert "docs/adr/" in map_text

    manifest_text = FLATPAK_MANIFEST.read_text(encoding="utf-8")
    assert "WebEngine" not in manifest_text


def test_adr_0002_follows_glossary_and_privacy_rules():
    text = ADR.read_text(encoding="utf-8")

    # GLOSSARY.md preferred terms present, avoided synonyms absent.
    assert "desktop app" in text
    assert "Decky plugin" in text
    for avoided in (
        "controller app",
        "QAM plugin",
        "decky backend",
        "power profile",
        "firmware profile",
        "fan profile",
    ):
        assert not re.search(rf"\b{re.escape(avoided)}\b", text, re.IGNORECASE), (
            f"GLOSSARY.md avoids {avoided!r}"
        )

    # AGENTS.md rule 8: no home paths or email addresses in docs.
    assert "/home/" not in text
    assert not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
