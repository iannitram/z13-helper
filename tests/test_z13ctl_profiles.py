"""Pure z13ctl named-profile mapping and probe (#23 part B)."""

import pytest

from z13ctl_profiles import apply_plan, named_profile_for, profile_target_supported


@pytest.mark.parametrize("name", ["Silent", "Balanced", "Turbo"])
def test_builtin_maps_to_its_own_name(name):
    assert named_profile_for(name) == name


@pytest.mark.parametrize("name", ["Boondock", "Custom 1", "", "silent", " Unknown "])
def test_unknown_names_have_no_mapping(name):
    assert named_profile_for(name) is None


def test_empty_or_blank_output_is_unsupported():
    assert profile_target_supported("") is False
    assert profile_target_supported("   \n\n") is False


def test_firmware_listing_with_names_is_supported():
    assert (
        profile_target_supported("Firmware profiles: quiet, balanced, performance\n")
        is True
    )


def test_one_profile_row_is_supported():
    listing = (
        "Firmware profiles:\nCustom profiles:\n   custom              fan curve, 20W\n"
    )
    assert profile_target_supported(listing) is True


@pytest.mark.parametrize(
    "text",
    [
        "garbage",
        "unexpected output",
        "Firmware profiles:\nCustom profiles:\n",
    ],
)
def test_unparseable_or_heading_only_output_is_unsupported(text):
    assert profile_target_supported(text) is False


def test_apply_plan_uses_the_named_target_when_supported():
    assert apply_plan("Balanced", True) == "named"


def test_apply_plan_refuses_when_the_target_is_unsupported():
    assert apply_plan("Balanced", False) == "refuse"


@pytest.mark.parametrize("name", ["Boondock", "Custom 1"])
@pytest.mark.parametrize("supported", [True, False])
def test_apply_plan_keeps_the_legacy_path_for_unmapped_names(name, supported):
    assert apply_plan(name, supported) == "legacy"
