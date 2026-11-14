import json
import pathlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pymongo.database import Database

from pipeline_service.pipelines import pfss
from pipeline_service.pipelines.pfss import PFSS_DATASET, PfssPipeline
from space_weather_analysis.coverage.repository import MongoCoverageRepository
from space_weather_analysis.metadata.repository import MongoMetadataRepository
from space_weather_analysis.solar.magnetic_field import FieldModel, SurfaceMap
from space_weather_analysis.solar.synoptic_maps import Magnetogram
from space_weather_analysis.timeseries.repository import MongoTimeSeriesRepository
from space_weather_analysis.util.error_handling import SourceUnreachableError

# The HMI map was last updated more than a day ago, older than the models are usually kept
HMI_UPDATED = datetime.now(UTC).replace(microsecond=0) - timedelta(days=2)


def _model(magnetogram: Magnetogram) -> FieldModel:
    """Provides a model without field lines, standing in for the slow PFSS computation."""
    empty = SurfaceMap(longitudes=[], latitudes=[], values=[])
    return FieldModel(
        map_time=HMI_UPDATED,
        source_surface_radius=2.5,
        earth_longitude=0.0,
        lines=[],
        surface_field=empty,
        source_surface_field=empty,
        open_field=empty,
        reference="",
        magnetogram=magnetogram,
    )


def test_without_an_answer_from_gong_the_model_comes_from_the_hmi_map_and_is_kept(
    db: Database, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreachable GONG server leads to a model of the HMI map, which stays stored although older than a day."""

    def unreachable(*_: Any) -> list[tuple[str, datetime]]:
        raise SourceUnreachableError("GONG directory is unreachable")

    monkeypatch.setattr(pfss, "recent_maps", unreachable)
    monkeypatch.setattr(pfss, "latest_maps", lambda *_: (tmp_path / "current.fits", tmp_path / "final.fits", HMI_UPDATED))
    monkeypatch.setattr(pfss, "hmi_synoptic_map", lambda *_: object())
    monkeypatch.setattr(pfss, "compute_field", lambda _, magnetogram: _model(magnetogram))
    media_dir = tmp_path / "media"
    pipeline = PfssPipeline(
        timeseries=MongoTimeSeriesRepository(db),
        coverage_repository=MongoCoverageRepository(db),
        metadata_repository=MongoMetadataRepository(db),
        media_dir=media_dir,
        map_dir=tmp_path / "maps",
    )

    report = pipeline.run([PFSS_DATASET], None, None)

    stored = [json.loads(path.read_text(encoding="utf-8"))["magnetogram"] for path in (media_dir / PFSS_DATASET).glob("*.json")]
    assert (report.gaps, stored, db[PFSS_DATASET].count_documents({})) == ([], ["SDO/HMI"], 1)
