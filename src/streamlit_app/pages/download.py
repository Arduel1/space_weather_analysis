import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import streamlit as st
from dateutil import parser

from streamlit_app import data
from streamlit_app.plots.generic import plot_generic
from streamlit_app.sidebar import MAX_ALLOWED_DATE, MIN_ALLOWED_DATE


def parse_catalog_time(time_str: str | None, default: datetime) -> datetime:
    """Parses a time string from the catalog into a timezone-aware datetime object.

    Args:
        time_str: The catalog time string in any format accepted by dateutil; naive times are treated as UTC.
        default: A fallback datetime to return if parsing fails or input is None.

    Returns:
        A datetime object with UTC timezone information.
    """
    if not time_str:
        return default.replace(tzinfo=UTC)
    try:
        dt = parser.parse(time_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (ValueError, TypeError):
        return default.replace(tzinfo=UTC)


def calculate_default_range(start_dt: datetime, end_dt: datetime) -> tuple[datetime, datetime]:
    """Calculates a default 1-day selection window centered in the given range.

    This ensures that when a user selects a dataset, the initial time picker
    values point to valid data rather than an arbitrary 'today' date.

    Args:
        start_dt: The earliest available data timestamp.
        end_dt: The latest available data timestamp.

    Returns:
        A tuple containing the calculated start and end datetimes.
    """
    total_duration = end_dt - start_dt
    midpoint = start_dt + (total_duration / 2)

    default_start = midpoint
    default_end = midpoint + timedelta(days=1)

    if default_end > end_dt:
        default_end = end_dt
        default_start = max(start_dt, default_end - timedelta(days=1))

    return default_start, default_end


def format_timedelta(td: timedelta) -> str:
    """Helper to format timedelta into a readable string (HH:MM:SS)."""
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes > 0:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


st.title("Data Catalog & Download Center")
st.markdown(
    """
    Browse the available datasets from the global catalog and trigger new download jobs.
    """
)

catalog = data.get_master_catalog()
if not catalog:
    st.error("Master catalog is empty.")
else:
    catalog_map = {item["dataset_id"]: item for item in catalog}
    dataset_ids = sorted(catalog_map.keys())

    selected_id = st.selectbox("Select Dataset from Catalog", dataset_ids)

    if selected_id:
        entry = catalog_map[selected_id]
        sources = entry.get("sources", [])
        primary_source: Mapping[str, Any] = sources[0] if sources else {}

        col1, col2 = st.columns([1, 1])

        with col1:
            st.subheader("Dataset Info")
            full_meta = data.get_metadata(selected_id)
            if full_meta:
                st.json(full_meta, expanded=False)
            st.write(f"**Source ID:** {primary_source.get('source_id', 'Unknown')}")
            st.write(f"**Type:** {primary_source.get('type', 'Unknown')}")

        with col2:
            st.subheader("Download Configuration")
            tr = primary_source.get("time_range", {})

            # Parse catalog limits to establish the dataset boundaries
            cat_start_dt = parse_catalog_time(tr.get("start_time"), MIN_ALLOWED_DATE)
            cat_end_dt = parse_catalog_time(tr.get("end_time"), MAX_ALLOWED_DATE)
            st.caption(f"Available Range: {cat_start_dt} to {cat_end_dt}")

            # Determine the initial 1-day selection window centered in the dataset
            init_start, init_end = calculate_default_range(cat_start_dt, cat_end_dt)

            c1, c2 = st.columns(2)
            with c1:
                req_start_date = st.date_input("Start Date", value=init_start.date(), min_value=MIN_ALLOWED_DATE, max_value=MAX_ALLOWED_DATE)
            with c2:
                req_start_time = st.time_input("Start Time", value=init_start.time())

            c3, c4 = st.columns(2)
            with c3:
                req_end_date = st.date_input("End Date", value=init_end.date(), min_value=MIN_ALLOWED_DATE, max_value=MAX_ALLOWED_DATE)
            with c4:
                req_end_time = st.time_input("End Time", value=init_end.time())

            req_start_dt = datetime.combine(req_start_date, req_start_time).replace(tzinfo=UTC)
            req_end_dt = datetime.combine(req_end_date, req_end_time).replace(tzinfo=UTC)

            if st.button("Start Download Job", type="primary"):
                new_job_id = data.create_download_job(dataset_id=selected_id, start_time=req_start_dt, end_time=req_end_dt)
                if new_job_id:
                    st.session_state["current_job_id"] = new_job_id
                    st.success(f"Job started! ID: {new_job_id}")
                else:
                    st.error("Failed to start job.")

        if "current_job_id" in st.session_state:
            st.markdown("---")
            st.subheader("Job Status")

            job_id = st.session_state["current_job_id"]
            status_placeholder = st.empty()
            progress_bar = st.progress(0)
            eta_placeholder = st.empty()

            # Polls the job until it reaches a final status.
            while True:
                job = data.get_job(job_id)
                status = job.status if job else "UNKNOWN"
                message = (job.message if job else None) or ""

                progress_val = min(max(job.progress if job else 0.0, 0.0), 1.0)
                progress_bar.progress(progress_val)

                # The remaining time assumes the rate of progress since the start stays constant
                started_at = job.started_at if job else None
                if status == "RUNNING" and progress_val > 0.01 and started_at:
                    elapsed = (datetime.now(UTC) - started_at).total_seconds()
                    if elapsed > 0:
                        total_time_est = elapsed / progress_val
                        remaining_time = total_time_est - elapsed
                        eta_str = format_timedelta(timedelta(seconds=remaining_time))
                        eta_placeholder.write(f"Estimated time remaining: **{eta_str}**")
                elif status == "PENDING":
                    eta_placeholder.write("Waiting for worker to pick up job...")
                else:
                    eta_placeholder.empty()

                status_placeholder.info(f"Status: **{status}** | {message}")

                if status == "COMPLETED":
                    progress_bar.progress(1.0)
                    st.success("Download Complete!")
                    eta_placeholder.empty()
                elif status == "FAILED":
                    st.error(f"Job Failed: {(job.error if job else None) or 'Unknown error'}")

                if status in ("COMPLETED", "FAILED"):
                    break
                time.sleep(2)

            if job is not None and status == "COMPLETED":
                st.markdown("### Preview Data")
                df_new = data.read_dataset_range(selected_id, job.parameters.start_time, job.parameters.end_time)
                if not df_new.empty:
                    plot_generic(df_new, selected_id)
                    with st.expander("Raw Data"):
                        st.dataframe(df_new)
                else:
                    st.warning("Job completed but no data returned.")
