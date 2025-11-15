import logging
import pathlib
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from astropy.table import Row, Table
from parfive.results import Results as ParfiveResults
from sunpy.net import Fido
from sunpy.net import attrs as a
from sunpy.net.cdaweb import attrs as cdaweb_attrs

from space_weather_analysis.sunpy_pipeline.cdaweb_client import CdawebClient
from space_weather_analysis.util.file_parser import parse_datafiles
from space_weather_analysis.util.metadata.metadata_generator import MetadataWriter
from space_weather_analysis.util.metadata.variable_metadata import VariableMetadata

logger = logging.getLogger(__name__)


class CdawebPipeline:
    """
    Orchestrates the end-to-end process of fetching, downloading, parsing,
    and documenting data from the CDAWeb service using Fido.
    """

    def __init__(self, base_data_path: pathlib.Path):
        """
        Initializes the pipeline.

        Args:
            base_data_path (pathlib.Path): The root directory where all
                                           downloaded data and metadata
                                           files will be stored.
        """
        self.client = CdawebClient()
        self.base_data_dir = base_data_path
        self.metadata_writer = MetadataWriter()
        if not self.base_data_dir.exists():
            self.base_data_dir.mkdir(parents=True)
            logger.info(f"Created base data directory: {self.base_data_dir}")
        logger.info(f"Pipeline initialized. Data root: {self.base_data_dir}")

    def _fetch_data_for_dataset(
        self,
        group_name: str,
        target_dataset: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Tuple[List[str], Optional[pathlib.Path]]:
        """
        Searches Fido for a dataset within a time range and downloads
        the files.

        Args:
            group_name (str): The observatory group name (for folder structure).
            target_dataset (str): The dataset ID to query.
            start_time (datetime): The start of the time range.
            end_time (datetime): The end of the time range.

        Returns:
            Tuple[List[str], Optional[pathlib.Path]]:
                - A list of paths to the downloaded files (empty if none).
                - The specific directory for the download (None if no files).
        """
        logger.info(f"Searching Fido for '{target_dataset}'...")

        try:
            specific_download_dir = self.base_data_dir / group_name / target_dataset
            time_query = a.Time(start_time, end_time)
            dataset_query = cdaweb_attrs.Dataset(target_dataset)
            query_response = Fido.search(time_query, dataset_query)

            if not query_response:
                logger.warning(f"Fido.search returned 0 results for '{target_dataset}'.")
                return [], None

            logger.info(f"Fido found results. Downloading files to: {specific_download_dir}")
            file_results: ParfiveResults = Fido.fetch(query_response, path=str(specific_download_dir / "{file}"))
            downloaded_file_paths = [str(fp) for fp in file_results.data]

            if not downloaded_file_paths:
                logger.warning("Fido reported results, but no files were actually downloaded.")
                return [], None

            logger.info(f"Download complete. {len(downloaded_file_paths)} files fetched.")
            return downloaded_file_paths, specific_download_dir
        except Exception as e:
            logger.error(f"Fido search/fetch failed for {target_dataset}: {e}", exc_info=True)
            return [], None

    def _normalize_cdaweb_meta_to_variables(
        self, api_meta: List[Dict[str, Any]], file_meta: Dict[str, Any]
    ) -> List[VariableMetadata]:
        """
        Converts the API metadata from CdawebClient and file metadata
        from file_parser into a single list of standardized
        VariableMetadata objects.

        Args:
            api_meta (List[Dict[str, Any]]): The metadata from CdawebClient.
            file_meta (Dict[str, Any]): The metadata from file_parser.

        Returns:
            List[VariableMetadata]: A unified list of metadata objects.
        """
        variables = []
        all_var_names = set([var.get("Name") for var in api_meta if var.get("Name")]) | set(file_meta.keys())

        api_meta_dict = {var["Name"]: var for var in api_meta if var.get("Name")}

        for var_name in all_var_names:
            if not var_name:
                continue

            api_attrs = api_meta_dict.get(var_name, {})
            file_attrs = file_meta.get(var_name, {})

            # Create the standardized object
            variables.append(
                VariableMetadata(
                    name=var_name,
                    description=api_attrs.get("LongDescription"),
                    units=api_attrs.get("Units"),
                    fill=api_attrs.get("Fill"),
                    type=api_attrs.get("Type"),
                    # file_attributes are merged directly
                    file_attributes=file_attrs,
                )
            )
        return variables

    def _process_dataset(
        self,
        dataset_id: str,
        group_name: str,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        """
        Runs the full pipeline (API metadata, download, parse, docgen)
        for a single dataset.

        Args:
            dataset_id (str): The dataset ID to process.
            group_name (str): The group name (for the download path).
            start_time (datetime): The start time for the data query.
            end_time (datetime): The end time for the data query.
        """
        logger.info(f"--- Processing Dataset: {dataset_id} ---")
        try:
            # Get API metadata (variables) from CdawebClient
            api_metadata = self.client.get_variables_for_dataset(dataset_id)
            if not api_metadata:
                logger.error(
                    f"Cannot proceed with {dataset_id}: No API metadata found.",
                    exc_info=True,
                )
                return

            # Search and download data
            downloaded_files, specific_download_dir = self._fetch_data_for_dataset(
                group_name, dataset_id, start_time, end_time
            )

            if downloaded_files and specific_download_dir:
                variables_to_parse = [var["Name"] for var in api_metadata if var.get("Name")]

                # Parse data and extract file metadata
                ts_filtered, file_metadata = parse_datafiles(
                    downloaded_files,
                    start_time,
                    end_time,
                    variables_to_parse,
                )

                if ts_filtered:
                    logger.info(f"1D data for {dataset_id} is parsed and ready for loading.")
                else:
                    logger.info(f"N-D data for {dataset_id} was handled by xarray/Dask.")

                # Normalize both API and File metadata into the standard list
                variables = self._normalize_cdaweb_meta_to_variables(api_metadata, file_metadata)

                self.metadata_writer.write_metadata_file(
                    dataset_id=dataset_id,
                    output_dir=specific_download_dir,
                    variables=variables,
                )

            else:
                logger.warning(f"Skipping parse/metadata for {dataset_id}: No files downloaded.")

        except Exception as e:
            logger.error(f"Pipeline failed for dataset {dataset_id}: {e}", exc_info=True)
        finally:
            logger.info(f"--- Finished Dataset: {dataset_id} ---")

    def _process_group(
        self,
        group_row: Row,
        start_time: datetime,
        end_time: datetime,
        dataset_filter: Optional[List[str]] = None,
    ) -> None:
        """
        Fetches and processes all datasets within a single group.

        Args:
            group_row (Row): An astropy.table.Row object representing
                             a single observatory group.
            start_time (datetime): The start time for the data query.
            end_time (datetime): The end time for the data query.
            dataset_filter (Optional[List[str]]): A list of specific
                                                  dataset IDs to process.
        """
        group_name = str(group_row["Group"])
        logger.info(f"===== Processing Group: {group_name} =====")

        datasets_table = self.client.get_datasets_for_group(group_name)
        if datasets_table is None or len(datasets_table) == 0:
            logger.warning(f"No datasets found for group {group_name}. Skipping.")
            return

        logger.info(f"Found {len(datasets_table)} datasets for {group_name}.")

        for dataset_row in datasets_table:
            dataset_id = str(dataset_row["Id"])

            if dataset_filter and dataset_id not in dataset_filter:
                logger.debug(f"Skipping {dataset_id} (not in dataset_filter).")
                continue

            self._process_dataset(dataset_id, group_name, start_time, end_time)

        logger.info(f"===== Finished Group: {group_name} =====")

    def run(
        self,
        start_time: datetime,
        end_time: datetime,
        group_filter: Optional[List[str]] = None,
        dataset_filter: Optional[List[str]] = None,
    ) -> None:
        """
        Runs the complete data pipeline orchestration.

        This method iterates through all observatory groups (or a filtered list)
        and all their datasets (or a filtered list), processing each one.

        Args:
            start_time (datetime): The start time for the data query.
            end_time (datetime): The end time for the data query.
            group_filter (Optional[List[str]], optional):
                A list of group names to process. If None (default),
                all groups are processed.
            dataset_filter (Optional[List[str]], optional):
                A list of specific dataset IDs to process. If None (default),
                all datasets in the processed groups are fetched.
        """
        logger.info("=========================================")
        if group_filter:
            logger.info(f"Starting CDAWeb Pipeline (FILTERED: {group_filter})")
        elif dataset_filter:
            logger.info(f"Starting CDAWeb Pipeline (FILTERED: {dataset_filter})")
        else:
            logger.info("Starting CDAWeb Pipeline (All Groups)")
        logger.info(f"Time Range: {start_time} to {end_time}")
        logger.info(f"Data Root: {self.base_data_dir}")
        logger.info("=========================================")

        all_groups = self.client.get_observatory_groups()
        if all_groups is None or len(all_groups) == 0:
            logger.error("Pipeline failed: Returned no groups. Cannot proceed.", exc_info=True)
            return

        # Ensure we are working with the expected table structure.
        assert isinstance(all_groups, Table)

        # Convert the table into an explicit list of rows.
        # This guarantees that each element is an actual Row object.
        groups_list: List[Row] = list(all_groups.iterrows())

        # Filter groups
        if group_filter:
            logger.info(f"Filtering {len(groups_list)} total groups to {len(group_filter)} requested.")
            groups_to_test: List[Row] = [g for g in groups_list if g["Group"] in group_filter]
        elif dataset_filter:
            logger.info(f"Processing all {len(groups_list)} groups to find specific datasets...")
            groups_to_test = groups_list
        else:
            logger.info(f"Processing all {len(groups_list)} groups.")
            groups_to_test = groups_list

        logger.info(f"--- Will process {len(groups_to_test)} groups ---")

        for group_row in groups_to_test:
            self._process_group(group_row, start_time, end_time, dataset_filter)

        logger.info("=========================================")
        logger.info("CDAWeb Pipeline finished.")
        logger.info("=========================================")
