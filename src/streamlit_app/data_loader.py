import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from bson.objectid import ObjectId
from pymongo.database import Database

from space_weather_analysis.database.connection import DatabaseConnection

logger = logging.getLogger(__name__)


class DataLoader:
    """
    Manages data retrieval for the Streamlit frontend by connecting directly to MongoDB.

    This class acts as an abstraction layer between the UI and the database,
    formatting raw MongoDB documents into pandas DataFrames suitable for plotting.
    It now also handles catalog retrieval and job management for data downloads.
    """

    def __init__(self) -> None:
        """
        Initializes the data loader.

        The database connection is retrieved via the common singleton pattern
        to ensure connection pooling and consistency across services.
        """
        self.db: Optional[Database] = DatabaseConnection.get_database()
        if self.db is None:
            logger.error("Could not establish database connection for Streamlit app.")

    def get_available_collections(self) -> List[str]:
        """
        Retrieves a list of all collections containing analyzable time-series data.

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
            filtered = [c for c in collections if not c.startswith("system.") and c not in ["dataset_metadata", "master_catalog", "jobs"]]
            return sorted(filtered)
        except Exception as e:
            logger.error(f"Error fetching collections: {e}")
            return []

    def get_timeseries_data(
        self,
        collection_name: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 0,
    ) -> pd.DataFrame:
        """
        Fetches time-series data from a specific collection and flattens it.

        The method handles the flattening of the nested 'data' structure used in
        the MongoDB documents into a flat DataFrame structure required by Streamlit
        charts.

        Args:
            collection_name: The MongoDB collection to query.
            start_time: Filter start date (inclusive).
            end_time: Filter end date (exclusive).
            limit: Maximum number of documents to return. 0 implies no limit.

        Returns:
            A DataFrame with a DatetimeIndex and columns for each data variable.
            Returns an empty DataFrame on error or no data.
        """
        if self.db is None:
            return pd.DataFrame()

        query: Dict[str, Any] = {}

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
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df.set_index("timestamp", inplace=True)
                df.sort_index(inplace=True)

            return df

        except Exception as e:
            logger.error(f"Error fetching data from {collection_name}: {e}")
            return pd.DataFrame()

    def get_metadata(self, dataset_id: str) -> Dict[str, Any]:
        """
        Fetches the metadata document for a given dataset.

        Args:
            dataset_id: The unique dataset identifier (collection name).

        Returns:
            The metadata dictionary or an empty dict if not found.
        """
        if self.db is None:
            return {}

        try:
            return self.db["dataset_metadata"].find_one({"dataset_id": dataset_id}) or {}
        except Exception:
            return {}

    def get_master_catalog(self) -> List[Dict[str, Any]]:
        """
        Retrieves the entire master catalog to list available datasets.

        Returns:
            List[Dict[str, Any]]: A list of catalog entries containing dataset IDs and source info.
        """
        if self.db is None:
            return []
        try:
            # Return all entries, excluding the internal _id field
            return list(self.db["master_catalog"].find({}, {"_id": 0}))
        except Exception as e:
            logger.error(f"Error fetching master catalog: {e}")
            return []

    def create_download_job(
        self,
        dataset_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Optional[str]:
        """
        Creates a new download job in the database without specifying the source explicitly.

        The Orchestrator will resolve the correct source based on the dataset_id.

        Args:
            dataset_id: The specific dataset identifier to download.
            start_time: The start datetime for the data range.
            end_time: The end datetime for the data range.

        Returns:
            Optional[str]: The string representation of the new job ID, or None if failed.
        """
        if self.db is None:
            return None

        job_doc = {
            # Source ID is deliberately omitted here; determined by pipeline logic.
            "parameters": {
                "datasets": [dataset_id],
                "start_time": start_time,
                "end_time": end_time,
            },
            "status": "PENDING",
            "created_at": datetime.now(timezone.utc),
            "progress": 0.0,
            "message": "Job created",
        }

        try:
            result = self.db["jobs"].insert_one(job_doc)
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"Error creating job: {e}")
            return None

    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """
        Retrieves the current status of a specific job.

        Args:
            job_id: The unique identifier of the job (as a string).

        Returns:
            Dict[str, Any]: The job document including status and any error messages.
        """
        if self.db is None:
            return {}

        try:
            return self.db["jobs"].find_one({"_id": ObjectId(job_id)}) or {}
        except Exception as e:
            logger.error(f"Error fetching job status: {e}")
            return {}
