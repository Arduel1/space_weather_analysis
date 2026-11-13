import io
import pathlib
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image
from pymongo.database import Database
from pytest_httpserver import HTTPServer

from pipeline_service.pipelines.images import ImagePipeline
from space_weather_analysis.clients.image_sequences import ImageProduct
from space_weather_analysis.coverage.repository import MongoCoverageRepository
from space_weather_analysis.metadata.repository import MongoMetadataRepository
from space_weather_analysis.timeseries.repository import MongoTimeSeriesRepository

DATASET = "synthetic_images"
# Now on a ten-minute boundary, so the spacing grid puts the frames into known steps
_NOW_UNALIGNED = datetime.now(UTC).replace(second=0, microsecond=0)
NOW = _NOW_UNALIGNED.replace(minute=_NOW_UNALIGNED.minute // 10 * 10)
# Frames two minutes apart, so a ten-minute spacing keeps one of every five
FRAME_TIMES = [NOW - timedelta(minutes=minutes) for minutes in (40, 38, 30, 28, 20)]


def _png(width: int, height: int) -> bytes:
    """Provides a plain PNG image of the given size."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 120, 40)).save(buffer, "PNG")
    return buffer.getvalue()


def _serve(httpserver: HTTPServer, times: list[datetime]) -> None:
    """Serves a frame list of the given times and one 800 x 600 image per frame."""
    paths = [f"/frames/img_{time:%Y%m%d_%H%M}.png" for time in times]
    httpserver.expect_request("/list.json").respond_with_json([{"url": path} for path in paths])
    for path in paths:
        httpserver.expect_request(path).respond_with_data(_png(800, 600), content_type="image/png")


def _pipeline(db: Database, httpserver: HTTPServer, media_dir: pathlib.Path) -> ImagePipeline:
    """Provides the image pipeline of one synthetic product on the local test server."""
    product = ImageProduct(
        list_url=httpserver.url_for("/list.json"),
        base_url=httpserver.url_for("/"),
        time_pattern=r"img_(\d{8}_\d{4})",
        time_format="%Y%m%d_%H%M",
        spacing=timedelta(minutes=10),
        size=100,
    )
    return ImagePipeline(
        timeseries=MongoTimeSeriesRepository(db),
        coverage_repository=MongoCoverageRepository(db),
        metadata_repository=MongoMetadataRepository(db),
        media_dir=media_dir,
        products={DATASET: product},
    )


@pytest.mark.integration
def test_image_frames_are_thinned_scaled_and_fetched_only_once(db: Database, httpserver: HTTPServer, tmp_path: pathlib.Path) -> None:
    """One frame per spacing is stored scaled down with a document naming its file, and a second run fetches only the new frame."""
    _serve(httpserver, FRAME_TIMES)
    _pipeline(db, httpserver, tmp_path).run([DATASET], NOW - timedelta(hours=1), NOW)
    first_requests = len(httpserver.log)
    httpserver.clear()
    _serve(httpserver, [*FRAME_TIMES, NOW - timedelta(minutes=8)])

    _pipeline(db, httpserver, tmp_path).run([DATASET], NOW - timedelta(hours=1), NOW)

    files = sorted(path.name for path in (tmp_path / DATASET).iterdir())
    with Image.open(tmp_path / DATASET / files[0]) as image:
        size = image.size
    documents = [document["data"]["file"] for document in db[DATASET].find().sort("timestamp", 1)]
    assert (len(files), size, documents == files, first_requests, [request.path for request, _ in httpserver.log][1:]) == (
        4,
        (100, 75),
        True,
        4,
        [f"/frames/img_{NOW - timedelta(minutes=8):%Y%m%d_%H%M}.png"],
    )
