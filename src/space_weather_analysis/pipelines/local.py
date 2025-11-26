import pathlib
from datetime import datetime
from typing import List, Optional

from pymongo.database import Database

from space_weather_analysis.etl.cme_catalog import CmeETL
from space_weather_analysis.etl.sunspots import SilsoETL
from space_weather_analysis.pipelines.base_pipeline import BasePipeline


class LocalSunspotPipeline(BasePipeline):
    """
    Wrapper for the SILSO Sunspot ETL process to conform to the BasePipeline interface.
    """

    def __init__(self, db: Database, file_path: pathlib.Path):
        self.db = db
        self.file_path = file_path

    def run(
        self,
        datasets: List[str],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> None:
        # Local file ETLs currently process the entire file, ignoring time filters.
        # 'datasets' is also ignored as the file implies the dataset.
        processor = SilsoETL(self.db, self.file_path)
        processor.run()


class LocalCmePipeline(BasePipeline):
    """
    Wrapper for the CME Catalog ETL process to conform to the BasePipeline interface.
    """

    def __init__(self, db: Database, file_path: pathlib.Path):
        self.db = db
        self.file_path = file_path

    def run(
        self,
        datasets: List[str],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> None:
        # Local file ETLs currently process the entire file, ignoring time filters.
        processor = CmeETL(self.db, self.file_path)
        processor.run()