from datetime import UTC, datetime

from pipeline_service.pipelines.image_frames import model_frame_file_name


def test_a_model_frame_is_named_as_jpeg_whatever_the_provider_format() -> None:
    """The stored name of a model frame ends in .jpg, as frames are stored as JPEG and only .jpg files are cleaned up."""
    name = model_frame_file_name(datetime(2026, 11, 10, 12, tzinfo=UTC), "https://example.invalid/run/frame_001.png")

    assert name == "20261110T120000Z_frame_001.jpg"
