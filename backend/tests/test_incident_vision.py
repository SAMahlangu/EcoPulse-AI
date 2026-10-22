import pytest

pytest.importorskip("PIL")

from io import BytesIO

from PIL import Image

from backend.app.ai import incident_vision


def make_image(kind="JPEG"):
    out = BytesIO()
    Image.new("RGB", (40, 20), "blue").save(out, format=kind)
    return out.getvalue()


@pytest.mark.parametrize("kind", ["JPEG", "PNG", "WEBP"])
def test_supported_images_are_decoded_resized_and_metadata_free(kind):
    image = incident_vision.prepare_image(make_image(kind))
    assert image.mode == "RGB"
    assert image.size == (40, 20)


def test_corrupt_image_is_rejected():
    with pytest.raises(incident_vision.InvalidIncidentImage):
        incident_vision.prepare_image(b"MZ executable payload")


def test_oversize_image_is_rejected():
    with pytest.raises(incident_vision.InvalidIncidentImage, match="10 MB"):
        incident_vision.prepare_image(b"x" * (incident_vision.MAX_IMAGE_BYTES + 1))


def test_classifier_absence_is_explicit(monkeypatch):
    monkeypatch.delenv("ECOPULSE_INCIDENT_MODEL", raising=False)
    result = incident_vision.analyze(Image.new("RGB", (10, 10)))
    assert result["available"] is False
    assert "manually" in result["reason"]
