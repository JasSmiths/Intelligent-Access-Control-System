import base64
from io import BytesIO

from PIL import Image

from app.services.profile_photos import (
    PROFILE_PHOTO_MAX_EDGE_PX,
    normalize_profile_photo_data_url,
)


def test_profile_photo_normalizer_leaves_browser_safe_images_unchanged() -> None:
    data_url = "data:image/png;base64,aGVsbG8="

    assert normalize_profile_photo_data_url(data_url) == data_url


def test_profile_photo_normalizer_converts_heic_to_png() -> None:
    import pillow_heif

    pillow_heif.register_heif_opener()
    source = BytesIO()
    Image.new("RGB", (1200, 900), "#8ab4f8").save(source, format="HEIF")
    data_url = f"data:image/heic;base64,{base64.b64encode(source.getvalue()).decode('ascii')}"

    converted = normalize_profile_photo_data_url(data_url)

    assert converted is not None
    assert converted.startswith("data:image/png;base64,")
    encoded = converted.split(",", 1)[1]
    with Image.open(BytesIO(base64.b64decode(encoded))) as image:
        assert image.format == "PNG"
        assert max(image.size) == PROFILE_PHOTO_MAX_EDGE_PX
