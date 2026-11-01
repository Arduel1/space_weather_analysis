import logging
from datetime import datetime
from typing import Any

import pandas as pd
from pydantic import ValidationError
from pymongo.database import Database
from pymongo.errors import PyMongoError

from space_weather_analysis.catalog.repository import CatalogRepository, MongoCatalogRepository
from space_weather_analysis.coverage.repository import MongoCoverageRepository
from space_weather_analysis.jobs.repository import JobRepository, MongoJobRepository
from space_weather_analysis.metadata.repository import MetadataRepository, MongoMetadataRepository

logger = logging.getLogger(__name__)

# Collections holding catalog, metadata, jobs and coverage rather than time series
EXCLUDED_COLLECTIONS = (
    MongoMetadataRepository.RECORD_COLLECTION,
    MongoMetadataRepository.RAW_COLLECTION,
    MongoCatalogRepository.COLLECTION,
    MongoJobRepository.COLLECTION,
    MongoCoverageRepository.COLLECTION,
)


class DataLoader:
    """Manages data retrieval for the Streamlit frontend from MongoDB.

    This class acts as an abstraction layer between the UI and the database,
    formatting raw MongoDB documents into pandas DataFrames suitable for plotting.
    It also handles catalog retrieval and job management for data downloads.
    """

    def __init__(self, db: Database | None) -> None:
        """Initializes the data loader with the database provided by the caller.

        Args:
            db (Database | None): The MongoDB database to read from, or None if
                                     no connection could be established. Without a
                                     database, all queries return empty results.
        """
        self.db: Database | None = db
        self.metadata_repository: MetadataRepository | None = MongoMetadataRepository(db) if db is not None else None
        self.catalog_repository: CatalogRepository | None = MongoCatalogRepository(db) if db is not None else None
        self.job_repository: JobRepository | None = MongoJobRepository(db) if db is not None else None
        if self.db is None:
            logger.error("Could not establish database connection for Streamlit app.")

    def get_available_collections(self) -> list[str]:
        """Retrieves a list of all collections containing analyzable time-series data.

        This method filters out internal system collections and metadata catalogs
        to present only relevant datasets to the user.

        Returns:
            A sorted list of string collection names.
        """
        if self.db is None:
            return []

        try:
            collections = self.db.list_collection_names()
            # Exclude system and metadata collections to keep the UI clean
            filtered = [name for name in collections if not name.startswith("system.") and name not in EXCLUDED_COLLECTIONS]
            return sorted(filtered)
        except PyMongoError:
            logger.exception("Error fetching collections")
            return []

    def get_timeseries_data(
        self,
        collection_name: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 0,
    ) -> pd.DataFrame:
        """Fetches time-series data from a specific collection and flattens it.

        The method handles the flattening of the nested 'data' structure used in
        the MongoDB documents into a flat DataFrame structure required by Streamlit
        charts.

        Args:
            collection_name: The MongoDB collection to query.
            start_time: Filter start date (inclusive).
            end_time: Filter end date (exclusive).
            limit: Maximum number of documents to return. 0 implies no limit.

        Returns:
            A DataFrame with a timezone-aware UTC DatetimeIndex and columns for each data variable.
            Returns an empty DataFrame on error or no data.
        """
        if self.db is None:
            return pd.DataFrame()

        query: dict[str, Any] = {}

        # Construct time range query only if parameters are provided
        if start_time or end_time:
            time_query = {}
            if start_time:
                time_query["$gte"] = start_time
            if end_time:
                time_query["$lt"] = end_time
            if time_query:
                query["timestamp"] = time_query

        try:
            # MongoDB treats a limit of 0 as unlimited
            cursor = self.db[collection_name].find(query).limit(limit)

            docs = list(cursor)
            if not docs:
                return pd.DataFrame()

            flattened_data = []
            for doc in docs:
                # Merge the top-level timestamp with the nested data dictionary
                flat_entry = {"timestamp": doc.get("timestamp"), **doc.get("data", {})}
                flattened_data.append(flat_entry)

            df = pd.DataFrame(flattened_data)

            if "timestamp" in df.columns:
                # utc=True keeps the index comparable with the aware UTC times of the plots
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                df.set_index("timestamp", inplace=True)
                df.sort_index(inplace=True)

            return df

        except PyMongoError:
            logger.exception("Error fetching data from %s", collection_name)
            return pd.DataFrame()

    def get_metadata(self, dataset_id: str) -> dict[str, Any]:
        """Fetches the metadata record of a dataset.

        Args:
            dataset_id: The dataset ID as named in the master catalog.

        Returns:
            The record as JSON-compatible dictionary, or an empty dict if not found.
        """
        if self.metadata_repository is None:
            return {}

        try:
            record = self.metadata_repository.get(dataset_id)
        except (PyMongoError, ValidationError):
            logger.exception("Error fetching metadata of %s", dataset_id)
            return {}
        return record.model_dump(mode="json") if record else {}

    def get_metadata_for_collection(self, collection_name: str) -> dict[str, Any]:
        """Fetches the metadata record of the dataset stored in a collection.

        Args:
            collection_name: The MongoDB collection holding the data documents.

        Returns:
            The record as JSON-compatible dictionary, the one with the first dataset ID if several
            datasets share the collection, or an empty dict if none is found.
        """
        if self.metadata_repository is None:
            return {}

        try:
            records = self.metadata_repository.find_by_collection(collection_name)
        except (PyMongoError, ValidationError):
            logger.exception("Error fetching metadata of collection %s", collection_name)
            return {}
        return records[0].model_dump(mode="json") if records else {}

    def get_master_catalog(self) -> list[dict[str, Any]]:
        """Retrieves the entire master catalog to list available datasets.

        Returns:
            list[dict[str, Any]]: The catalog items with dataset ID and sources, sorted by dataset ID.
        """
        if self.catalog_repository is None:
            return []
        try:
            catalog: list[dict[str, Any]] = [
                {"dataset_id": item["dataset_id"], "sources": item["sources"]} for item in self.catalog_repository.get_all()
            ]
            return catalog
        except PyMongoError:
            logger.exception("Error fetching master catalog")
            return []

    def create_download_job(
        self,
        dataset_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> str | None:
        """Creates a new download job in the database without specifying the source explicitly.

        The Orchestrator will resolve the correct source based on the dataset_id.

        Args:
            dataset_id: The specific dataset identifier to download.
            start_time: The start datetime for the data range.
            end_time: The end datetime for the data range.

        Returns:
            str | None: The string representation of the new job ID, or None if failed.
        """
        if self.job_repository is None:
            return None

        try:
            # Source ID is deliberately omitted here; determined by pipeline logic.
            return self.job_repository.create([dataset_id], start_time, end_time)
        except (PyMongoError, ValidationError):
            logger.exception("Error creating job")
            return None

    def get_job_status(self, job_id: str) -> dict[str, Any]:
        """Retrieves the current status of a specific job.

        Args:
            job_id: The unique identifier of the job (as a string).

        Returns:
            dict[str, Any]: The job fields including status and any error messages, or an empty
            dict if the job is not found.
        """
        if self.job_repository is None:
            return {}

        try:
            job = self.job_repository.get(job_id)
            return job.model_dump() if job else {}
        except (PyMongoError, ValidationError):
            logger.exception("Error fetching job status")
            return {}
