from enum import StrEnum


class Granularity(StrEnum):
    """The granularity of a MongoDB time series collection, the unit closest to the spacing of its documents.

    MongoDB groups the documents of a time series collection into internal buckets sized by this unit.
    """

    SECONDS = "seconds"
    MINUTES = "minutes"
    HOURS = "hours"


class TimeBucket(StrEnum):
    """The length of the time buckets that aggregated reads group documents into, named by the units of MongoDB $dateTrunc."""

    HOUR = "hour"
    DAY = "day"
    MONTH = "month"
    YEAR = "year"
