import pathlib
from typing import Optional

from pymongo.database import Database

from space_weather_analysis.config import (
    CME_CATALOG_FILE,
    FIDO_CDAWEB_SOURCE_ID,
    HAPI_SOURCE_ID,
    SILSO_FILE,
)
from space_weather_analysis.pipelines.hapi import HapiPipeline
from space_weather_analysis.pipelines.base import BasePipeline
from space_weather_analysis.pipelines.local import LocalCmePipeline, LocalSunspotPipeline
from space_weather_analysis.pipelines.cdaweb import CdawebPipeline


class PipelineFactory:
    """
    Factory class to instantiate and return the appropriate data pipeline
    based on the source type.
    """

    def __init__(self, db: Database, temp_data_path: Optional[pathlib.Path] = None):
        """
        Initializes the factory.

        Args:
            db (Database): The MongoDB database instance.
            temp_data_path (Optional[pathlib.Path]): Path for temporary file storage
                                                     (required for CDAWeb pipeline).
        """
        self.db = db
        self.temp_data_path = temp_data_path or pathlib.Path("/tmp/sw_data")

    def get_pipeline(self, source_type: str) -> BasePipeline:
        """
        Returns a pipeline instance matching the provided source type.

        Args:
            source_type (str): The identifier for the data source type.

        Returns:
            BasePipeline: An instantiated pipeline object conforming to the interface.

        Raises:
            ValueError: If the source_type is unknown.
        """
        if source_type == HAPI_SOURCE_ID:
            return HapiPipeline(self.db)

        elif source_type == FIDO_CDAWEB_SOURCE_ID:
            return CdawebPipeline(self.db, self.base_data_path)

        elif source_type == "LOCAL_SILSO":
            return LocalSunspotPipeline(self.db, SILSO_FILE)

        elif source_type == "LOCAL_CME":
            return LocalCmePipeline(self.db, CME_CATALOG_FILE)

        else:
            raise ValueError(f"Unknown source type: {source_type}")

    @property
    def base_data_path(self) -> pathlib.Path:
        return self.temp_data_path
