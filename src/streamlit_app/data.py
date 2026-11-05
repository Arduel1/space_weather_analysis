import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import streamlit as st
from pydantic import ValidationError
from pymongo.errors import PyMongoError

from space_weather_analysis.catalog.models import MasterCatalogItem
from space_weather_analysis.catalog.repository import CatalogRepository, MongoCatalogRepository
from space_weather_analysis.config.errors import format_settings_error
from space_weather_analysis.config.mongo import MongoSettings
from space_weather_analysis.coverage.repository import CoverageRepository, MongoCoverageRepository
from space_weather_analysis.database.connection import MongoConnection
from space_weather_analysis.jobs.models import Job
from space_weather_analysis.jobs.repository import JobRepository, MongoJobRepository
from space_weather_analysis.metadata.repository import MetadataRepository, MongoMetadataRepository
from space_weather_analysis.timeseries.repository import MongoTimeSeriesRepository, TimeSeriesRepository

logger = logging.getLogger(__name__)

# How long cached reads are reused before the database is queried again
READ_TTL = timedelta(minutes=1)

# Failures of a database operation or of the validation of a stored document
DATABASE_ERRORS = (PyMongoError, ValidationError)


@dataclass(frozen=True)
class Repositories:
    """The core repositories the app reads from and creates download jobs with."""

    timeseries: TimeSeriesRepository
    coverage: CoverageRepository
    metadata: MetadataRepository
    catalog: CatalogRepository
    jobs: JobRepository


class DatabaseUnavailableError(Exception):
    """Raised when the MongoDB settings are invalid or the database cannot be reached."""


@st.cache_resource(show_spinner=False)
def _connect() -> Repositories:
    """Builds the repositories on one MongoDB connection, once per Streamlit server process.

    A failure raises instead of returning, so Streamlit does not cache it and the next run tries again.

    Returns:
        The repositories.

    Raises:
        DatabaseUnavailableError: If the MongoDB settings are invalid or the database is unreachable.
    """
    try:
        mongo_settings = MongoSettings()
    except ValidationError as error:
        # The traceback is left out, as it would show the given values
        raise DatabaseUnavailableError(f"Invalid MongoDB settings, check the MONGO_* environment variables: {format_settings_error(error)}") from None
    db = MongoConnection(mongo_settings).get_database()
    if db is None:
        raise DatabaseUnavailableError("The MongoDB server is unreachable.")
    return Repositories(
        timeseries=MongoTimeSeriesRepository(db),
        coverage=MongoCoverageRepository(db),
        metadata=MongoMetadataRepository(db),
        catalog=MongoCatalogRepository(db),
        jobs=MongoJobRepository(db),
    )


def get_repositories() -> Repositories | None:
    """Returns the shared repositories of the app.

    Returns:
        The repositories, or None if the MongoDB settings are invalid or the database is unreachable; the reason is logged.
    """
    try:
        return _connect()
    except DatabaseUnavailableError as error:
        logger.critical("No database for the Streamlit app: %s", error)
        return None


@st.cache_data(ttl=READ_TTL, show_spinner=False)
def list_stored_collections() -> list[str]:
    """Lists the collections of the datasets that have stored documents, according to their coverage.

    Returns:
        The collection names, sorted and without duplicates; empty if the coverage cannot be read.
    """
    repositories = get_repositories()
    if repositories is None:
        return []
    try:
        coverages = repositories.coverage.list_all()
    except DATABASE_ERRORS:
        logger.exception("Error reading the dataset coverages")
        return []
    return sorted({coverage.collection for coverage in coverages if coverage.document_count > 0})


@st.cache_data(ttl=READ_TTL, show_spinner=False)
def read_collection_range(collection: str, start: datetime, stop: datetime) -> pd.DataFrame:
    """Reads the data fields of the documents of a collection in a time range.

    Args:
        collection: The collection holding the data documents.
        start: The start of the time range (inclusive).
        stop: The end of the time range (exclusive).

    Returns:
        One row per document with an aware UTC DatetimeIndex and one column per data field; empty on error or without documents.
    """
    return _read_range(collection, start, stop)


def read_dataset_range(dataset_id: str, start: datetime | None, stop: datetime | None) -> pd.DataFrame:
    """Reads the freshly stored documents of a dataset, without the cache.

    The collection and the missing range limits come from the coverage of the dataset.

    Args:
        dataset_id: The dataset ID as named in the master catalog.
        start: The start of the time range (inclusive); None starts at the earliest stored document.
        stop: The end of the time range (exclusive); None ends after the latest stored document.

    Returns:
        One row per document with an aware UTC DatetimeIndex and one column per data field; empty on error, without
        coverage or without documents.
    """
    repositories = get_repositories()
    if repositories is None:
        return pd.DataFrame()
    try:
        coverage = repositories.coverage.get(dataset_id)
    except DATABASE_ERRORS:
        logger.exception("Error reading the coverage of %s", dataset_id)
        return pd.DataFrame()
    if coverage is None or coverage.first_timestamp is None or coverage.last_timestamp is None:
        return pd.DataFrame()
    # The stop is exclusive, so the latest document needs a stop just after it
    range_start = start or coverage.first_timestamp
    range_stop = stop or coverage.last_timestamp + timedelta(microseconds=1)
    return _read_range(coverage.collection, range_start, range_stop)


def _read_range(collection: str, start: datetime, stop: datetime, fields: Sequence[str] | None = None) -> pd.DataFrame:
    """Reads a time range of a collection through the time series repository.

    Args:
        collection: The collection holding the data documents.
        start: The start of the time range (inclusive).
        stop: The end of the time range (exclusive).
        fields: The data fields to read; None reads every data field.

    Returns:
        The read frame; empty on error or without documents.
    """
    repositories = get_repositories()
    if repositories is None:
        return pd.DataFrame()
    try:
        return repositories.timeseries.read_range(collection, start, stop, fields)
    except PyMongoError:
        logger.exception("Error reading %s between %s and %s", collection, start, stop)
        return pd.DataFrame()


@st.cache_data(ttl=READ_TTL, show_spinner=False)
def get_metadata(dataset_id: str) -> dict[str, Any]:
    """Reads the metadata record of a dataset.

    Args:
        dataset_id: The dataset ID as named in the master catalog.

    Returns:
        The record as JSON-compatible dictionary; empty on error or if none is stored.
    """
    repositories = get_repositories()
    if repositories is None:
        return {}
    try:
        record = repositories.metadata.get(dataset_id)
    except DATABASE_ERRORS:
        logger.exception("Error reading the metadata of %s", dataset_id)
        return {}
    return record.model_dump(mode="json") if record else {}


@st.cache_data(ttl=READ_TTL, show_spinner=False)
def get_metadata_for_collection(collection: str) -> dict[str, Any]:
    """Reads the metadata record of the dataset stored in a collection.

    Args:
        collection: The collection holding the data documents.

    Returns:
        The record as JSON-compatible dictionary, the one with the first dataset ID if several datasets share
        the collection; empty on error or if none is stored.
    """
    repositories = get_repositories()
    if repositories is None:
        return {}
    try:
        records = repositories.metadata.find_by_collection(collection)
    except DATABASE_ERRORS:
        logger.exception("Error reading the metadata of collection %s", collection)
        return {}
    return records[0].model_dump(mode="json") if records else {}


@st.cache_data(ttl=READ_TTL, show_spinner=False)
def get_master_catalog() -> list[MasterCatalogItem]:
    """Reads the master catalog.

    Returns:
        The catalog items with dataset ID and sources; empty on error.
    """
    repositories = get_repositories()
    if repositories is None:
        return []
    try:
        return repositories.catalog.get_all()
    except PyMongoError:
        logger.exception("Error reading the master catalog")
        return []


def create_download_job(dataset_id: str, start_time: datetime, end_time: datetime) -> str | None:
    """Creates a download job for one dataset; the job runner resolves the source from the master catalog.

    Args:
        dataset_id: The dataset ID as named in the master catalog.
        start_time: The start of the requested time range.
        end_time: The end of the requested time range.

    Returns:
        The ID of the new job, or None if it could not be created.
    """
    repositories = get_repositories()
    if repositories is None:
        return None
    try:
        return repositories.jobs.create([dataset_id], start_time, end_time)
    except DATABASE_ERRORS:
        logger.exception("Error creating a download job for %s", dataset_id)
        return None


def get_job(job_id: str) -> Job | None:
    """Reads the current state of a job, without the cache, as the job page polls it.

    Args:
        job_id: The job ID.

    Returns:
        The job, or None on error or if it does not exist.
    """
    repositories = get_repositories()
    if repositories is None:
        return None
    try:
        return repositories.jobs.get(job_id)
    except DATABASE_ERRORS:
        logger.exception("Error reading job %s", job_id)
        return None
