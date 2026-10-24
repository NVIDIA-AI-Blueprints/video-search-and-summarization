import pytest
from pydantic import ValidationError

from backend.api.kpi_schemas import KpiRequest, KpiSelection


class TestKpiSelection:
    def test_tag_required(self):
        with pytest.raises(ValidationError):
            KpiSelection()

    def test_valid_minimal(self):
        assert KpiSelection(tag="kpi").tag == "kpi"

    def test_created_after_optional(self):
        assert KpiSelection(tag="kpi").created_after is None

    def test_passthrough_fields_allowed(self):
        sel = KpiSelection(tag="kpi", machineId="M-1")
        assert sel.model_dump()["machineId"] == "M-1"

    @pytest.mark.parametrize("good", ["2026-06-12T10:00:00Z", "2026-06-12T10:00:00+00:00"])
    def test_created_after_valid_utc(self, good):
        assert KpiSelection(tag="kpi", created_after=good).created_after == good

    @pytest.mark.parametrize(
        "bad",
        ["2026-06-12T10:00:00", "2026-06-12", "2026-06-12T10:00:00+07:00", "nope"],
    )
    def test_created_after_invalid_rejected(self, bad):
        with pytest.raises(ValidationError, match="UTC"):
            KpiSelection(tag="kpi", created_after=bad)

    def test_missing_tag_through_request(self):
        with pytest.raises(ValidationError):
            KpiRequest(selection={"created_after": "2026-06-12T10:00:00Z"})
