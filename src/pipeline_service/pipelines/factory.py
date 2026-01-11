import logging
import pathlib

from pymongo.database import Database

from pipeline_service.pipelines.local import LocalPipeline
from src.pipeline_service.pipelines.base import BasePipeline
from src.pipeline_service.pipelines.cdaweb import CdawebPipeline
from src.pipeline_service.pipelines.hapi import HapiPipeline

logger = logging.getLogger(__name__)


class PipelineFactory:
    """
    Creates specific pipeline instances based on the provided source identifier.

    This factory abstracts the instantiation logic of data pipelines, allowing
    the orchestrator to remain agnostic of the concrete pipeline classes.
    It injects the necessary dependencies, such as the database connection
    and storage path, into each created pipeline.
    """

    # Source identifiers used to map jobs to pipeline implementations
    SOURCE_HAPI = "HAPI"
    SOURCE_CDAWEB = "FIDO_CDAWEB"
    SOURCE_LOCAL = "LOCAL"

    def __init__(self, db: Database, base_data_path: pathlib.Path) -> None:
        """
        Initializes the factory with shared dependencies.

        Args:
            db (Database): The MongoDB database instance used by pipelines
                for metadata and status updates.
            base_data_path (pathlib.Path): The root directory where downloaded
                data files should be stored.
        """
        self.db = db
        self.base_data_path = base_data_path

    def get_pipeline(self, source_type: str) -> BasePipeline:
        """
        Returns a configured pipeline instance for the given source type.

        This method maps a source string ID to a concrete pipeline class.
        It raises an error if the source type is unknown, ensuring that
        unsupported operations are caught early.

        Args:
            source_type (str): The string identifier for the data source.

        Returns:
            BasePipeline: An instance of a class inheriting from BasePipeline,
            ready to execute data operations.

        Raises:
            ValueError: If the provided source_type does not correspond to
            any registered pipeline implementation.
        """
        if source_type == self.SOURCE_HAPI:
            logger.info(f"Creating HAPI pipeline for storage at {self.base_data_path}")
            return HapiPipeline(self.db, self.base_data_path)

        if source_type == self.SOURCE_CDAWEB:
            logger.info(f"Creating CDAWeb pipeline for storage at {self.base_data_path}")
            return CdawebPipeline(self.db, self.base_data_path)

        if source_type == self.SOURCE_LOCAL:
            return LocalPipeline(self.db, self.base_data_path)

        # Logging the error provides context on which invalid type was requested
        error_msg = f"Unknown pipeline source type: {source_type}"
        logger.error(error_msg)
        raise ValueError(error_msg)
