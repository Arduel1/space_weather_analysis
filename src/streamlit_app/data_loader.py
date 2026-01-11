import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
from pymongo.database import Database

from common.database.connection import DatabaseConnection

logger = logging.getLogger(__name__)


class DataLoader:
    """
    Manages data retrieval for the Streamlit frontend by connecting directly to MongoDB.

    This class acts as an abstraction layer between the UI and the database,
    formatting raw MongoDB documents into pandas DataFrames suitable for plotting.
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
            filtered = [
                c
                for c in collections
                if c
                not in [
                    "system.views",
                    "system.profile",
                    "system.buckets"
                    "dataset_metadata",
                    "master_catalog",
                ]
            ]
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
            return self.db["dataset_metadata"].find_one({"_id": dataset_id}) or {}
        except Exception:
            return {}
