import pathlib
from typing import assert_never

from cdasws import CdasWs
from pymongo.database import Database

from pipeline_service.chunk_status.repository import ChunkStatusRepository, MongoChunkStatusRepository
from pipeline_service.pipelines.base import Pipeline
from pipeline_service.pipelines.cdaweb import CdawebPipeline
from pipeline_service.pipelines.gfz import GfzPipeline
from pipeline_service.pipelines.hapi import HapiPipeline
from pipeline_service.pipelines.local import LocalPipeline
from pipeline_service.pipelines.swpc import SwpcPipeline
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.catalog.repository import CatalogRepository, MongoCatalogRepository
from space_weather_analysis.clients.gfz import GfzClient
from space_weather_analysis.clients.swpc import SwpcClient
from space_weather_analysis.coverage.repository import CoverageRepository, MongoCoverageRepository
from space_weather_analysis.metadata.repository import MetadataRepository, MongoMetadataRepository
from space_weather_analysis.timeseries.repository import MongoTimeSeriesRepository, TimeSeriesRepository


class PipelineFactory:
    """Creates the pipeline of a source type with the repositories and clients it needs, which all pipelines share."""

    def __init__(self, db: Database, data_dir: pathlib.Path) -> None:
        """Builds the repositories on the database and the clients of the data providers; no request is sent yet.

        Args:
            db: The MongoDB database instance.
            data_dir: The directory holding the local dataset files.
        """
        self.data_dir = data_dir
        self.timeseries: TimeSeriesRepository = MongoTimeSeriesRepository(db)
        self.catalog_repository: CatalogRepository = MongoCatalogRepository(db)
        self.metadata_repository: MetadataRepository = MongoMetadataRepository(db)
        self.coverage_repository: CoverageRepository = MongoCoverageRepository(db)
        self.chunk_status: ChunkStatusRepository = MongoChunkStatusRepository(db)
        # Without response cache, so every run reads the current data
        self.cdas = CdasWs(disable_cache=True)
        self.gfz_client = GfzClient()
        self.swpc_client = SwpcClient()

    def get_pipeline(self, source_type: SourceType) -> Pipeline:
        """Creates the pipeline of a source type.

        Args:
            source_type: The source type of the master catalog.

        Returns:
            The pipeline.
        """
        match source_type:
            case SourceType.HAPI:
                return HapiPipeline(
                    timeseries=self.timeseries,
                    catalog_repository=self.catalog_repository,
                    metadata_repository=self.metadata_repository,
                    coverage_repository=self.coverage_repository,
                    chunk_status=self.chunk_status,
                )
            case SourceType.CDAWEB:
                return CdawebPipeline(
                    timeseries=self.timeseries,
                    catalog_repository=self.catalog_repository,
                    metadata_repository=self.metadata_repository,
                    coverage_repository=self.coverage_repository,
                    chunk_status=self.chunk_status,
                    cdas=self.cdas,
                )
            case SourceType.GFZ_POTSDAM:
                return GfzPipeline(
                    timeseries=self.timeseries,
                    metadata_repository=self.metadata_repository,
                    coverage_repository=self.coverage_repository,
                    chunk_status=self.chunk_status,
                    client=self.gfz_client,
                )
            case SourceType.LOCAL:
                return LocalPipeline(timeseries=self.timeseries, coverage_repository=self.coverage_repository, data_dir=self.data_dir)
            case SourceType.SWPC:
                return SwpcPipeline(
                    timeseries=self.timeseries,
                    coverage_repository=self.coverage_repository,
                    metadata_repository=self.metadata_repository,
                    client=self.swpc_client,
                )
            case _:
                assert_never(source_type)
