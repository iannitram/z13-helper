"""Support bundle skeleton shape and default exclusions (issue #27)."""

from support_bundle import EXCLUDED_CLASSES, build_bundle, preview


def test_default_bundle_records_every_exclusion():
    bundle = build_bundle({"kernel": None}, ["a journal line"])
    assert bundle["include_hooks"] is False
    assert bundle["include_credentials"] is False
    assert set(bundle["excluded"]) == {
        "profile hooks",
        "credential values",
        "serial numbers",
        "network identifiers",
        "unrelated journal lines",
    }
    assert set(bundle["excluded"]) == set(EXCLUDED_CLASSES)
    assert all(bundle["excluded"].values())
    assert isinstance(preview(bundle), str)
