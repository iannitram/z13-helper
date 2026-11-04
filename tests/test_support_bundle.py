"""Support bundles fail closed and preview the exact shareable payload."""

import copy
import json

import pytest

from support_bundle import EXCLUDED_CLASSES, build_bundle, preview


def test_clean_bundle_does_not_claim_exclusions():
    bundle = build_bundle({"kernel": "6.12.0-valve", "bios_ec": None}, [])
    assert bundle["inventory"] == {"kernel": "6.12.0-valve", "bios_ec": None}
    assert bundle["include_hooks"] is False
    assert bundle["include_credentials"] is False
    assert set(bundle["excluded"]) == set(EXCLUDED_CLASSES)
    assert not any(bundle["excluded"].values())


@pytest.mark.parametrize(("field", "category"), [
    ("profile_hooks", "profile hooks"),
    ("api_key", "credential values"),
    ("serial_number", "serial numbers"),
    ("ssid", "network identifiers"),
])
def test_sensitive_fields_are_excluded_and_reported(field, category):
    bundle = build_bundle({field: "private-value"}, [])
    assert bundle["inventory"] == {}
    assert "private-value" not in preview(bundle)
    assert {key for key, removed in bundle["excluded"].items() if removed} == {category}


def test_unknown_nested_and_free_form_data_fail_closed_without_mutating_input():
    inventory = {
        "kernel": "6.12.0",
        "profiles": [{"after_hook": "run-private-script", "password": "secret-value"}],
        "device": {"serial": "device-123", "mac": "aa:bb:cc:dd:ee:ff"},
        "kernel_args": "wifi.password=unlabelled-value",
        "backend_version": "1.4.2 token=hidden-value",
        "other": "unlabelled-private-value",
    }
    lines = ["z13ctl: password=journal-secret", "unrelated private text"]
    original = copy.deepcopy(inventory)
    bundle = build_bundle(inventory, lines)
    assert bundle["inventory"] == {"kernel": "6.12.0"}
    assert bundle["journal_lines"] == []
    assert all(bundle["excluded"][name] for name in (
        "profile hooks", "credential values", "serial numbers", "network identifiers",
        "unreviewed inventory fields", "unreviewed journal lines",
    ))
    assert bundle["excluded"]["unrelated journal lines"] is False
    assert inventory == original
    assert lines == ["z13ctl: password=journal-secret", "unrelated private text"]


def test_explicit_opt_ins_do_not_disable_serial_or_network_exclusions():
    bundle = build_bundle({
        "hooks": ["echo opted-in"], "credentials": {"token": "opted-in"},
        "serial": "private-serial", "network": "private-network",
    }, [], include_hooks=True, include_credentials=True)
    assert bundle["inventory"] == {
        "hooks": ["echo opted-in"], "credentials": {"token": "opted-in"},
    }
    assert bundle["excluded"]["profile hooks"] is False
    assert bundle["excluded"]["credential values"] is False
    assert bundle["excluded"]["serial numbers"] is True
    assert bundle["excluded"]["network identifiers"] is True


def test_preview_shows_exact_redacted_payload():
    bundle = build_bundle({"kernel": "6.12.0", "password": "do-not-share"}, ["private"])
    rendered = preview(bundle)
    assert json.loads(rendered) == bundle
    assert "6.12.0" in rendered
    assert "do-not-share" not in rendered
    assert '"journal_lines": []' in rendered
    assert '"credential values": true' in rendered


def test_empty_inputs_have_no_exclusions():
    bundle = build_bundle(None, None)
    assert bundle["inventory"] == {}
    assert bundle["journal_lines"] == []
    assert not any(bundle["excluded"].values())


def test_exclusions_report_every_class_removed_with_a_parent_field():
    bundle = build_bundle({"hooks": {"serial": "private-serial"}}, [], include_hooks=True)
    assert bundle["inventory"] == {}
    assert bundle["excluded"]["profile hooks"] is True
    assert bundle["excluded"]["serial numbers"] is True


def test_machine_id_is_not_reported_as_a_network_identifier():
    bundle = build_bundle({"machine_id": "private-id"}, [])
    assert bundle["excluded"]["serial numbers"] is True
    assert bundle["excluded"]["network identifiers"] is False
