import logging
import pathlib
from datetime import datetime, timezone
from typing import Any, Dict, List

from space_weather_analysis.hapi_pipeline.hapi_client import GenericHapiClient
from space_weather_analysis.hapi_pipeline.hapi_converter import convert_hapi_to_timeseries
from space_weather_analysis.util.metadata.metadata_generator import MetadataWriter
from space_weather_analysis.util.metadata.variable_metadata import VariableMetadata

logger = logging.getLogger(__name__)


class GenericHapiPipeline:
    """
    Orchestrates the data streaming and metadata generation
    using the generic HAPI client.
    """

    def __init__(
        self,
        catalog_path: pathlib.Path,
        base_output_dir: pathlib.Path,
    ):
        """
        Initializes the pipeline.

        Args:
            catalog_path (pathlib.Path): Path to 'hapi_master_catalog.json'.
            base_output_dir (pathlib.Path): Root directory to save
                                            metadata documents.
        """
        self.client = GenericHapiClient(catalog_path)
        self.output_dir = base_output_dir
        self.metadata_writer = MetadataWriter()
        if not self.output_dir.exists():
            self.output_dir.mkdir(parents=True)

    def _normalize_hapi_meta_to_variables(self, hapi_meta: Dict[str, Any]) -> List[VariableMetadata]:
        """
        Converts the raw HAPI metadata dictionary into a list of
        standardized VariableMetadata objects.

        Args:
            hapi_meta (Dict[str, Any]): The raw metadata dict from hapiclient.

        Returns:
            List[VariableMetadata]: A list of standardized metadata objects.
        """
        variables = []
        for param in hapi_meta.get("parameters", []):
            variables.append(
                VariableMetadata(
                    name=param.get("name"),
                    description=param.get("description"),
                    units=param.get("units"),
                    fill=param.get("fill"),
                    type=param.get("type"),
                    size=param.get("size"),
                )
            )
        return variables

    def run(
        self,
        datasets_to_process: List[str],
        start_time: datetime,
        stop_time: datetime,
    ):
        """
        Runs the full HAPI streaming pipeline for a list of datasets.

        Args:
            datasets_to_process (List[str]): A list of dataset IDs to fetch.
            start_time (datetime): The start time for the data query.
            stop_time (datetime): The end time for the data query.
        """
        logger.info("--- Starting Generic HAPI Pipeline ---")
        if not self.client.catalog_map:
            logger.error("HAPI client has no catalog. Aborting pipeline.")
            return

        logger.info(f"Processing {len(datasets_to_process)} datasets...")

        # Ensure config times are timezone-aware (UTC)
        config_start = start_time
        if config_start.tzinfo is None:
            config_start = config_start.replace(tzinfo=timezone.utc)

        config_stop = stop_time
        if config_stop.tzinfo is None:
            config_stop = config_stop.replace(tzinfo=timezone.utc)

        for dataset_id in datasets_to_process:
            logger.info(f"--- Processing Dataset: {dataset_id} ---")

            try:
                data, meta = self.client.get_data_and_meta(dataset_id, config_start, config_stop)

                if data is None or meta is None:
                    logger.warning(
                        f"Skipping {dataset_id}: Could not retrieve data. "
                        "All fallbacks failed or no time overlap found."
                    )
                    continue

                ts = convert_hapi_to_timeseries(data, meta)
                if ts:
                    logger.info(
                        f"Successfully converted {dataset_id} data to "
                        f"sunpy.timeseries.TimeSeries with {len(ts.to_table())} rows."
                    )
                else:
                    logger.warning(f"Failed to convert {dataset_id} to TimeSeries.")

                # Normalize the raw HAPI metadata to standard format
                variables = self._normalize_hapi_meta_to_variables(meta)

                # Prepare header info for the markdown file
                source_info = {
                    "Source HAPI Server": meta.get("x_server_url", "N/A"),
                    "Data Start Date": meta.get("startDate", "N/A"),
                    "Data Stop Date": meta.get("stopDate", "N/A"),
                    "Description": meta.get("description", "N/A"),
                    "Citation": meta.get("datasetCitation", "N/A"),
                }

                # Generate metadata document
                dataset_output_dir = self.output_dir / dataset_id
                dataset_output_dir.mkdir(parents=True, exist_ok=True)

                # Call the central MetadataWriter
                self.metadata_writer.write_metadata_file(
                    dataset_id=dataset_id,
                    output_dir=dataset_output_dir,
                    variables=variables,
                    source_info=source_info,
                )

            except Exception as e:
                logger.error(
                    f"Pipeline failed unexpectedly for {dataset_id}: {e}",
                    exc_info=True,
                )

            logger.info(f"--- Finished Dataset: {dataset_id} ---")

        logger.info("--- Generic HAPI Pipeline Finished ---")
