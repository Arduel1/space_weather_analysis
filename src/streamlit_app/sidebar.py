from datetime import UTC, datetime, timedelta

import streamlit as st

# Bounds of the date inputs; without them Streamlit allows only ten years around the default value
MIN_ALLOWED_DATE = datetime(1800, 1, 1, tzinfo=UTC)
MAX_ALLOWED_DATE = datetime(2200, 1, 1, tzinfo=UTC)


def time_range_filter() -> tuple[datetime, datetime]:
    """Shows the start and end date inputs in the sidebar, preset to the last 365 days.

    Returns:
        The aware UTC start at 00:00 of the start date and the end at the last microsecond of the end date.
    """
    st.sidebar.subheader("Time Range Filter")

    default_end = datetime.now(UTC)
    default_start = default_end - timedelta(days=365)

    start_date = st.sidebar.date_input("Start Date", value=default_start, min_value=MIN_ALLOWED_DATE, max_value=MAX_ALLOWED_DATE)
    end_date = st.sidebar.date_input("End Date", value=default_end, min_value=MIN_ALLOWED_DATE, max_value=MAX_ALLOWED_DATE)

    start = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=UTC)
    end = datetime.combine(end_date, datetime.max.time()).replace(tzinfo=UTC)
    return start, end
