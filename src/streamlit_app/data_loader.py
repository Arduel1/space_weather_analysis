import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from space_weather_analysis.database.connection import DatabaseConnection

# Configure logger specific to the frontend
logger = logging.getLogger(__name__)


class DataLoader:
    """
    Handles data retrieval for the Streamlit frontend.

    Currently connects directly to MongoDB, but designed to be replaced
    by an API client in future architectural iterations.
    """

    def __init__(self):
        """
        Initializes the data loader and ensures a database connection.
        """
        self.db = DatabaseConnection.get_database()
        if self.db is None:
            logger.error("Could not establish database connection for Streamlit app.")

    def get_available_collections(self) -> List[str]:
        """
        Retrieves a list of all collections that contain timeseries data.

        Returns:
            List[str]: A list of collection names, excluding system collections.
        """
        if self.db is None:
            return []

        try:
            collections = self.db.list_collection_names()
            # Filter out system collections and the master catalog itself if purely visualizing data
            # Keeping 'master_catalog' might be useful for metadata inspection, but filtering for now.
            filtered = [c for c in collections if c not in ["system.views", "system.profile", "dataset_metadata"]]
            return sorted(filtered)
        except Exception as e:
            logger.error(f"Error fetching collections: {e}")
            return []

    def get_timeseries_data(
        self, collection_name: str, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None, limit: int = 10000
    ) -> pd.DataFrame:
        """
        Fetches timeseries data from a specific collection.

        Args:
            collection_name (str): The MongoDB collection to query.
            start_time (Optional[datetime]): Filter start (inclusive).
            end_time (Optional[datetime]): Filter end (exclusive).
            limit (int): Safety limit for number of documents.

        Returns:
            pd.DataFrame: A DataFrame containing the flattened data.
        """
        if self.db is None:
            return pd.DataFrame()

        query: Dict[str, Any] = {}

        # Construct time query if range provided
        if start_time or end_time:
            time_query = {}
            if start_time:
                time_query["$gte"] = start_time
            if end_time:
                time_query["$lt"] = end_time
            if time_query:
                query["timestamp"] = time_query

        try:
            cursor = self.db[collection_name].find(query).limit(limit)

            # Convert cursor to list of dicts
            docs = list(cursor)
            if not docs:
                return pd.DataFrame()

            # Flatten the 'data' sub-document for easier plotting
            # The structure is assumed to be: {timestamp: ..., metadata: ..., data: {val1: ..., val2: ...}}
            flattened_data = []
            for doc in docs:
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
        Fetches metadata for a given dataset ID (collection name).

        Args:
            dataset_id (str): The dataset identifier.

        Returns:
            Dict[str, Any]: The metadata document or empty dict.
        """
        if self.db is None:
            return {}

        try:
            # Assuming metadata is stored in 'dataset_metadata' collection as per MetadataWriter
            return self.db["dataset_metadata"].find_one({"_id": dataset_id}) or {}
        except Exception:
            return {}
