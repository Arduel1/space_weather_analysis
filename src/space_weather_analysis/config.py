"""
Centralized Configuration for the Space Weather Analysis Pipeline.

This module defines all static paths and configuration constants used throughout
the application. By centralizing path management, we can easily modify
data locations without changing the core logic of the pipelines.
"""

import pathlib

# --- Core Paths ---
PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent
"""The root directory of the project."""

DATA_ROOT = PROJECT_ROOT / "data"
"""The root directory for all data (inputs and outputs)."""

CATALOG_FILE = DATA_ROOT / "config" / "master_catalog.json"
"""Path to the generated master catalog file."""


# --- HAPI Pipeline Configuration ---
HAPI_CONFIG_DIR = DATA_ROOT / "hapi_config"
"""Directory for HAPI configuration files."""

HAPI_SERVER_LIST_FILE = HAPI_CONFIG_DIR / "all.txt"
"""Path to the text file containing a list of HAPI server URLs."""

HAPI_OUTPUT_DIR = DATA_ROOT / "hapi_streaming_output"
"""Root output directory for data streamed from the HAPI pipeline."""


# --- Fido/CDAWeb Pipeline Configuration ---
FIDO_DOWNLOAD_DIR = DATA_ROOT / "cdaweb_fido_downloads"
"""Root output directory for files downloaded via the Fido/CDAWeb pipeline."""


# --- Local ETL File Paths ---
CME_CATALOG_FILE = DATA_ROOT / "cme_catalog.txt"
"""Path to the SOHO/LASCO CME Catalog text file."""

SILSO_FILE = DATA_ROOT / "SN_d_tot_V2.0.csv"
"""Path to the SILSO daily sunspot data CSV file."""
