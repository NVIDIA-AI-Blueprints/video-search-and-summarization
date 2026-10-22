import logging

import pytest
from PIL import Image

from upload_kpi_dataset import (
    _build_golden_metadata,
    _build_test_metadata,
    _crop_item,
    _load_image,
)


def _write_jpeg(path, width, height):
    Image.new("RGB", (width, height), "gray").save(path, "JPEG")
    return path


def _row(label="PASS", mpass_mfail="MPASS"):
    return {
        "input_path": "FXLH01/AOI_B/FXLH_P1_B1_T_B_20260101120000",
        "object_name": "C100",
        "label": label,
        "comp_type_2": "C",
        "mpass_mfail": mpass_mfail,
        "boardname": "B1",
    }


def test_crop_box_covers_the_whole_image():
    crop = _crop_item("C100", "PASS", "C", 128, 96)["crop"]
    assert crop["coordinates"] == {"top": 0, "left": 0, "width": 128, "height": 96}


def test_defect_type_is_the_ground_truth_label():
    assert _crop_item("C100", "Missing", "C", 128, 128)["aoiDefectInfo"]["defectType"] == "Missing"
    assert _crop_item("C100", "PASS", "C", 128, 128)["aoiDefectInfo"]["defectType"] == "PASS"


@pytest.mark.parametrize("mpass_mfail", ["MPASS", "MFAIL", ""])
def test_test_metadata_ignores_the_machine_verdict(mpass_mfail):
    """A passing component must stay a PASS whatever the machine column says."""
    metadata = _build_test_metadata(
        _row("PASS", mpass_mfail), "kpi", "deft-aoi/abc123/C100_SolderLight.jpg", 128, 128
    )
    crop = metadata["imageInfo"]["cropInfo"][0]
    assert crop["aoiDefectInfo"]["defectType"] == "PASS"
    assert crop["crop"]["coordinates"] == {"top": 0, "left": 0, "width": 128, "height": 128}


def test_test_metadata_keeps_a_real_defect_name():
    metadata = _build_test_metadata(_row("Insufficient", "MFAIL"), "kpi", "", 128, 128)
    assert metadata["imageInfo"]["cropInfo"][0]["aoiDefectInfo"]["defectType"] == "Insufficient"


def test_golden_metadata_marks_the_reference_as_passing():
    crop = _build_golden_metadata("C100", "kpi", "C", 64, 32)["imageInfo"]["cropInfo"][0]
    assert crop["aoiDefectInfo"]["defectType"] == "PASS"
    assert crop["crop"]["coordinates"] == {"top": 0, "left": 0, "width": 64, "height": 32}


class TestCropBoxStaysInsideTheGolden:
    """The crop box is applied to the test image AND its golden reference, so it
    must not exceed either one."""

    def _coords(self, width, height, golden_size):
        metadata = _build_test_metadata(
            _row("PASS"), "kpi", "deft-aoi/abc/C100.jpg", width, height, golden_size
        )
        return metadata["imageInfo"]["cropInfo"][0]["crop"]["coordinates"]

    def test_larger_test_image_is_clamped_to_the_golden(self):
        assert self._coords(200, 150, (128, 96)) == {
            "top": 0, "left": 0, "width": 128, "height": 96,
        }

    def test_each_axis_is_clamped_independently(self):
        # Wider than the golden but shorter than it.
        assert self._coords(200, 50, (128, 96)) == {
            "top": 0, "left": 0, "width": 128, "height": 50,
        }

    def test_smaller_test_image_keeps_its_own_size(self):
        assert self._coords(64, 48, (128, 96)) == {
            "top": 0, "left": 0, "width": 64, "height": 48,
        }

    def test_equal_sizes_are_unchanged(self):
        assert self._coords(128, 96, (128, 96)) == {
            "top": 0, "left": 0, "width": 128, "height": 96,
        }

    def test_unknown_golden_size_falls_back_to_the_test_image(self):
        """A golden missing on disk must not collapse the box to zero."""
        assert self._coords(200, 150, None) == {
            "top": 0, "left": 0, "width": 200, "height": 150,
        }

    def test_clamping_is_logged(self, caplog):
        with caplog.at_level(logging.WARNING):
            self._coords(200, 150, (128, 96))
        assert "exceeds golden reference" in caplog.text


def test_load_image_reports_the_real_dimensions(tmp_path):
    loaded = _load_image(_write_jpeg(tmp_path / "C100_SolderLight.jpg", 200, 120))
    assert loaded is not None
    raw, data, content_type, width, height = loaded
    assert (width, height) == (200, 120)
    assert content_type == "image/jpeg"
    assert raw and data


def test_load_image_skips_a_file_it_cannot_decode(tmp_path):
    """Dropping the record beats uploading it with an invented crop box."""
    broken = tmp_path / "C100_SolderLight.jpg"
    broken.write_bytes(b"not a jpeg")
    assert _load_image(broken) is None
