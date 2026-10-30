import pathlib

from pymongo.database import Database

from pipeline_service.pipelines.base import BasePipeline
from pipeline_service.pipelines.cdaweb import CdawebPipeline
from pipeline_service.pipelines.gfz import GfzPipeline
from pipeline_service.pipelines.hapi import HapiPipeline
from pipeline_service.pipelines.local import LocalPipeline
from space_weather_analysis.catalog.models import SourceType


class PipelineFactory:
    """Creates the pipeline of a source type with the dependencies it needs."""

    def __init__(self, db: Database, data_dir: pathlib.Path) -> None:
        """Initializes the factory with the shared dependencies.

        Args:
            db: The MongoDB database instance.
            data_dir: The directory holding the local dataset files.
        """
        self.db = db
        self.data_dir = data_dir

    def get_pipeline(self, source_type: SourceType) -> BasePipeline:
        """Creates the pipeline of a source type.

        Args:
            source_type: The source type of the master catalog.

        Returns:
            The pipeline.
        """
        match source_type:
            case SourceType.HAPI:
                return HapiPipeline(self.db)
            case SourceType.FIDO_CDAWEB:
                return CdawebPipeline(self.db)
            case SourceType.GFZ_POTSDAM:
                return GfzPipeline(self.db)
            case SourceType.LOCAL:
                return LocalPipeline(self.db, self.data_dir)
