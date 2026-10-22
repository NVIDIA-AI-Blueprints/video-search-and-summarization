import pytest
from pydantic import ValidationError

from backend.api.snapshot_schemas import SnapshotRequest, SnapshotSelection
from backend.api.snapshot_service import SnapshotSelectionError, compile_selection


class TestCreatedAfterValidation:
    @pytest.mark.parametrize(
        "good",
        [
            "2026-06-12T10:00:00Z",           # trailing Z (UTC)
            "2026-06-12T10:00:00+00:00",      # explicit +00:00
            "2026-06-12T10:00:00.123Z",       # fractional seconds
            "2026-06-12T10:00:00.123456+00:00",
        ],
    )
    def test_valid_utc_iso8601_accepted(self, good):
        sel = SnapshotSelection(created_after=good)
        assert sel.created_after == good

    @pytest.mark.parametrize(
        "bad",
        [
            "2026-06-12T10:00:00",       # naive — no timezone
            "2026-06-12",                # date only, naive
            "2026-06-12T10:00:00+07:00",  # non-UTC offset
            "2026-06-12T10:00:00-05:00",  # non-UTC offset
            "2026-13-12T10:00:00Z",      # impossible month
            "not-a-date",
            "",
        ],
    )
    def test_non_utc_or_malformed_rejected(self, bad):
        with pytest.raises(ValidationError, match="UTC"):
            SnapshotSelection(created_after=bad)

    def test_validation_applies_through_request(self):
        with pytest.raises(ValidationError, match="UTC"):
            SnapshotRequest(selection=SnapshotSelection.model_validate({"created_after": "2026-06-12T10:00:00"}))

    def test_missing_created_after_rejected(self):
        with pytest.raises(ValidationError):
            SnapshotSelection()

    def test_passthrough_fields_still_allowed(self):
        sel = SnapshotSelection(created_after="2026-06-12T10:00:00Z", machineId="M-1")
        assert sel.model_dump()["machineId"] == "M-1"

    def test_unknown_field_is_not_rejected_at_the_model(self):
        # The model stays permissive: which keys a catalog actually maps is
        # decided by compile_selection (see TestSelectionFieldValidation in
        # test_snapshot_service.py), not by the schema.
        sel = SnapshotSelection(created_after="2026-06-12T10:00:00Z", cameras=["c1"])
        assert sel.model_dump()["cameras"] == ["c1"]
        with pytest.raises(SnapshotSelectionError, match="cameras"):
            compile_selection(sel.model_dump(), "its")
