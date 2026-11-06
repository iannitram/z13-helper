"""Pure z13ctl named-profile mapping and probe (#23 part B)."""

import pytest

from z13ctl_profiles import (apply_plan, is_valid_backend_name, named_profile_for,
                             profile_target_supported)


@pytest.mark.parametrize("name", ["Silent", "Balanced", "Turbo"])
def test_builtin_has_no_fixed_named_profile(name):
    # z13ctl 1.4.2 has no such profiles and rejects capitalized names.
    assert named_profile_for(name) is None


@pytest.mark.parametrize("name", ["custom", "z13h-balanced-1a2b3c4d", "abc", "a-1"])
def test_lowercase_non_base_names_are_valid_backend_names(name):
    assert is_valid_backend_name(name)


@pytest.mark.parametrize("name", ["Balanced", "Silent", "quiet", "balanced", "performance",
                                  "", None, 23, "has space", "under_score"])
def test_capitalized_base_empty_or_odd_names_are_not_valid_backend_names(name):
    assert not is_valid_backend_name(name)


@pytest.mark.parametrize("name", ["Boondock", "Custom 1", "", "silent", " Unknown "])
def test_unknown_names_have_no_mapping(name):
    assert named_profile_for(name) is None


def test_empty_or_blank_output_is_unsupported():
    assert profile_target_supported("") is False
    assert profile_target_supported("   \n\n") is False


def test_firmware_listing_does_not_prove_target_support():
    assert (
        profile_target_supported("Firmware profiles: quiet, balanced, performance\n")
        is False
    )


def test_one_profile_row_is_supported():
    listing = (
        "Firmware profiles:\nCustom profiles:\n   custom              fan curve, 20W\n"
    )
    assert profile_target_supported(listing) is False


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


@pytest.mark.parametrize("name", ["Silent", "Balanced", "Turbo", "Boondock", "Custom 1"])
@pytest.mark.parametrize("supported", [True, False])
def test_apply_plan_keeps_the_legacy_path_without_a_fixed_named_profile(name, supported):
    # Built-ins get generated backend names, so no fixed named target exists to choose.
    assert apply_plan(name, supported) == "legacy"


@pytest.mark.parametrize("value", ['[]', '[{"name":"custom"}]'])
def test_socket_profile_list_proves_named_api(value):
    assert profile_target_supported(value)


@pytest.mark.parametrize("value", ['{}', 'null', '[{}]', '[{"name":23}]', '[{"name":""}]'])
def test_malformed_socket_profile_list_is_refused(value):
    assert not profile_target_supported(value)
