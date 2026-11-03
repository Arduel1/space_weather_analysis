import streamlit as st

from streamlit_app import data

st.set_page_config(page_title="Space Weather Analytics", layout="wide", initial_sidebar_state="expanded")

if data.get_repositories() is None:
    st.error(
        "The database is unavailable: the MongoDB settings are invalid or incomplete, or the server is unreachable. "
        "Check the MONGO_* environment variables and the logs."
    )
    st.stop()

# Page paths are relative to this script
navigation = st.navigation(
    [
        st.Page("pages/dashboard.py", title="Dashboard", default=True),
        st.Page("pages/explorer.py", title="Dataset Explorer"),
        st.Page("pages/download.py", title="Data Downloader"),
    ]
)
navigation.run()

st.markdown("---")
st.caption("Space Weather Analysis Platform")
