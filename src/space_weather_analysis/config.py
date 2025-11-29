"""
Centralized Configuration for the Space Weather Analysis Pipeline.

This module defines all static paths and configuration constants used throughout
the application. By centralizing path management, we can easily modify
data locations without changing the core logic of the pipelines.
"""

import pathlib

# --- Core Paths ---
PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).parent.parent.parent
"""The root directory of the project."""

DATA_ROOT: pathlib.Path = PROJECT_ROOT / "data"
"""The root directory for all data (inputs and outputs)."""

CATALOG_FILE: pathlib.Path = DATA_ROOT / "config" / "master_catalog.json"
"""Path to the generated master catalog file."""


# --- HAPI Pipeline Configuration ---
HAPI_CONFIG_DIR: pathlib.Path = DATA_ROOT / "hapi_config"
"""Directory for HAPI configuration files."""

HAPI_SERVER_LIST_FILE: pathlib.Path = HAPI_CONFIG_DIR / "all.txt"
"""Path to the text file containing a list of HAPI server URLs."""


# --- Local ETL File Paths ---
CME_CATALOG_FILE: pathlib.Path = DATA_ROOT / "cme_catalog.txt"
"""Path to the SOHO/LASCO CME Catalog text file."""

SILSO_FILE: pathlib.Path = DATA_ROOT / "SN_d_tot_V2.0.csv"
"""Path to the SILSO daily sunspot data CSV file."""


# --- Catalog Field Configuration ---
FIDO_CDAWEB_SOURCE_ID: str = "FIDO_CDAWEB"
"""Identifier used in the catalog to denote datasets sourced via SunPy/Fido (CDAWeb)."""

HAPI_SOURCE_ID: str = "HAPI"
"""Identifier used in the catalog to denote datasets sourced via HAPI servers."""
