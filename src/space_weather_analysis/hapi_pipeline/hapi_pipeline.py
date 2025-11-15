import logging
import pathlib
from datetime import datetime, timezone
from typing import Any, Dict, List

from space_weather_analysis.hapi_pipeline.hapi_client import GenericHapiClient
from space_weather_analysis.hapi_pipeline.hapi_converter import (
    convert_hapi_to_timeseries,
)

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
        if not self.output_dir.exists():
            self.output_dir.mkdir(parents=True)

    def _generate_hapi_metadata_file(
        self,
        meta: Dict[str, Any],
        start_time: datetime,
        stop_time: datetime,
        dataset_id: str,
        output_path: pathlib.Path,
    ):
        """
        Generates a markdown metadata file directly from HAPI metadata.

        Args:
            meta (Dict[str, Any]): The metadata from the HAPI client.
            start_time (datetime): The requested pipeline start time.
            stop_time (datetime): The requested pipeline stop time.
            dataset_id (str): The target dataset ID.
            output_path (pathlib.Path): The path to write the .md file to.
        """
        if output_path.exists():
            logger.info(f"Metadata document {output_path} already exists. Skipping.")
            return

        logger.info(f"Generating metadata document: {output_path}")
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(f"# Metadata for Dataset: {dataset_id}\n\n")
                f.write(f"**Source HAPI Server:** {meta.get('x_server_url', 'N/A')}\n")
                f.write(f"**Start Date:** {start_time}\n")
                f.write(f"**Stop Date:** {stop_time}\n")
                f.write(f"**Description:** {meta.get('description', 'N/A')}\n")
                f.write(f"**Citation:** {meta.get('datasetCitation', 'N/A')}\n\n")
                f.write("---\n\n## Variables\n\n")

                for param in meta.get("parameters", []):
                    param_name = param.get("name", "N/A")
                    f.write(f"### Variable: `{param_name}`\n\n")
                    f.write(f"- **Description:** {param.get('description', 'N/A')}\n")
                    f.write(f"- **Type:** {param.get('type')}\n")
                    f.write(f"- **Units:** {param.get('units', 'N/A')}\n")
                    f.write(f"- **Fill Value:** `{param.get('fill', 'N/A')}`\n")
                    if param.get("size"):
                        f.write(f"- **Size:** {param.get('size')}\n")
                    f.write("\n")
            logger.info(f"Successfully generated metadata: {output_path}")
        except Exception as e:
            logger.error(f"Failed to write metadata file: {e}", exc_info=True)

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

                # Convert to TimeSeries (using the imported converter)
                ts = convert_hapi_to_timeseries(data, meta)
                if ts:
                    logger.info(
                        f"Successfully converted {dataset_id} data to "
                        f"sunpy.timeseries.TimeSeries with {len(ts.to_table())} rows."
                    )
                else:
                    logger.warning(f"Failed to convert {dataset_id} to TimeSeries.")

                # Generate metadata document
                dataset_output_dir = self.output_dir / dataset_id
                dataset_output_dir.mkdir(parents=True, exist_ok=True)
                output_path = dataset_output_dir / f"{dataset_id}_metadata.md"

                self._generate_hapi_metadata_file(meta, start_time, stop_time, dataset_id, output_path)

            except Exception as e:
                logger.error(
                    f"Pipeline failed unexpectedly for {dataset_id}: {e}",
                    exc_info=True,
                )

            logger.info(f"--- Finished Dataset: {dataset_id} ---")

        logger.info("--- Generic HAPI Pipeline Finished ---")
