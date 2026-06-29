import base64
from io import BytesIO

from PIL import Image

from app.services.profile_photos import (
    PROFILE_PHOTO_MAX_EDGE_PX,
    ProfilePhotoError,
    normalize_profile_photo_data_url,
    photo_data_url_to_media,
)


def test_profile_photo_normalizer_leaves_browser_safe_images_unchanged() -> None:
    source = BytesIO()
    Image.new("RGB", (120, 90), "#38bdf8").save(source, format="PNG")
    data_url = f"data:image/png;base64,{base64.b64encode(source.getvalue()).decode('ascii')}"

    normalized = normalize_profile_photo_data_url(data_url)

    assert normalized is not None
    assert normalized.startswith("data:image/")


def test_profile_photo_normalizer_rejects_svg_active_content() -> None:
    data_url = "data:image/svg+xml;base64,PHN2ZyBvbmxvYWQ9ImFsZXJ0KDEpIj48L3N2Zz4="

    try:
        normalize_profile_photo_data_url(data_url)
    except ProfilePhotoError:
        pass
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("SVG profile photos must be rejected.")


def test_profile_photo_normalizer_converts_heic_to_browser_safe_image() -> None:
    import pillow_heif

    pillow_heif.register_heif_opener()
    source = BytesIO()
    Image.new("RGB", (1200, 900), "#8ab4f8").save(source, format="HEIF")
    data_url = f"data:image/heic;base64,{base64.b64encode(source.getvalue()).decode('ascii')}"

    converted = normalize_profile_photo_data_url(data_url)

    assert converted is not None
    assert converted.startswith("data:image/jpeg;base64,")
    encoded = converted.split(",", 1)[1]
    with Image.open(BytesIO(base64.b64decode(encoded))) as image:
        assert image.format == "JPEG"
        assert max(image.size) == PROFILE_PHOTO_MAX_EDGE_PX


def test_profile_photo_media_response_compacts_large_jpeg() -> None:
    source = BytesIO()
    Image.new("RGB", (1800, 1200), "#f97316").save(source, format="JPEG", quality=95)
    data_url = f"data:image/jpeg;base64,{base64.b64encode(source.getvalue()).decode('ascii')}"

    content_type, media = photo_data_url_to_media(data_url)

    assert content_type == "image/jpeg"
    assert len(media) < len(source.getvalue())
    with Image.open(BytesIO(media)) as image:
        assert max(image.size) == PROFILE_PHOTO_MAX_EDGE_PX


def test_profile_photo_media_response_supports_thumbnail_variant_size() -> None:
    source = BytesIO()
    Image.new("RGB", (1800, 1200), "#22c55e").save(source, format="JPEG", quality=95)
    data_url = f"data:image/jpeg;base64,{base64.b64encode(source.getvalue()).decode('ascii')}"

    content_type, media = photo_data_url_to_media(data_url, max_edge_px=192)

    assert content_type == "image/jpeg"
    with Image.open(BytesIO(media)) as image:
        assert max(image.size) == 192
