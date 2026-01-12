# Space Weather Analysis Project

## Project Overview

This repository contains a Data Science project developed as part of a the DSCB-230-WS25/26 Hausarbeit at [HKA](https://www.h-ka.de/). The project's goal is to analyze space weather phenomena and their impact on Earth.

The analysis follows the physical causal chain of events, starting from the Sun, propagating through the heliosphere, and finally impacting Earth's magnetosphere.

The core components of this project are:

1. A robust ETL pipeline to fetch, clean, and store multi-instrument space physics data in a MongoDB database.
2. Exploratory Data Analysis (EDA) to investigate known physical relationships, such as the 11-year solar cycle and the in-situ signatures of Coronal Mass Ejections (CMEs).
3. A predictive model to forecast geomagnetic activity (using the Kp-Index) based on real-time solar wind data.


TODO:
1. Fester Datensatz
2. CDAWeb Schnittstelle
3. Nutzung von Sunpy
4. Sonnenzyklen zu Sonnenflecken
5. Zyklus zu CMEs
6. Als .exe bereitstellen

---

### Project Structure

The repository is organized as a modern Python package:

```bash
.
├── docker-compose.yml         # Defines the MongoDB service and starts the Python Script
├── poetry.lock                # Dependency lock file
├── pyproject.toml             # Project definition and dependencies (for Poetry)
├── data/                      # Contains all local data source files
│   ├── SN_d_tot_V2.0.csv
│   └── cme_catalog.txt
├── notebooks/                 # Jupyter notebooks for EDA and modeling
├── src/
│   └── space_weather_analysis/
│       ├── main.py            # Main entry point to run the ETL pipeline
│       ├── database/          # (Module) Database connection and setup
│       │   └── connection.py
│       ├── etl/               # (Module) ETL logic
│       │   ├── base_processor.py  # Abstract base class for ETL processes
│       │   ├── cme_catalog.py   # ETL for SOHO/LASCO CME Catalog
│       │   └── sunspots.py      # ETL for SILSO Sunspot Data
│       └── util/              # (Module) Utility code
│           ├── data_preprocessing.py
│           └── exceptions.py
└── tests/                     # Unit and integration tests
```

---

### Authors

`Group Papa`:

* Patrick Heß
* Sebastian Straßner

---

### License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

---

## Getting Started

This project uses **Docker** to run the database and the main ETL application, and **Poetry** for Python dependency management.

### 1. Running the ETL Pipeline (Quick Start)

This is the simplest way to run the entire application. It will start the MongoDB database and run the Python ETL script defined in `src/space_weather_analysis/main.py`.

1. **Configure Environment:**
    Copy the `example.env` file to `.env` and fill in your desired credentials.

    ```bash
    cp example.env .env
    ```

2. **Build and Run:**
    Use `docker-compose` to build the Python image and start both the application and database containers.
    Start the Database Service: Navigate to the project's root directory (where [docker-compose.yml](docker-compose.yml) is located) and run:

    ```bash
    docker-compose up --build -d
    ```

3. **Check Logs (Optional):**
    To see the live output of the ETL process, you can follow the logs of the `app` service:

    ```bash
    docker-compose logs -f app
    ```

4. **Stop the Services:**
    When you are finished, stop the containers.

    ```bash
    docker-compose down
    ```

    *To delete all data in the database volume as well, use `docker-compose down -v`.*

---

### 2. Setting up for Development (Analysis & Notebooks)

If you want to contribute code, run tests, or work in the Jupyter notebooks, you need to set up the Python environment locally using Poetry. This assumes you already have Docker running the database (see step 1).

1. **Install Poetry:**
    Follow the [official Poetry documentation](https://python-poetry.org/docs/) to install it on your system.

2. **Configure Poetry (Optional):**
    To store the virtual environment inside the project folder (as `.venv`), run:

    ```bash
    poetry config virtualenvs.in-project true
    ```

3. **Install Dependencies:**
    Poetry will read the `pyproject.toml` file and install all required packages.

    ```bash
    poetry install
    ```

4. **Activate the Environment:**
    You can now access the environment in two ways:

      * **Spawn a shell:** `poetry shell` (Recommended)
      * **Run a single command:** `poetry run jupyter lab`

With the environment active, you can now open the `notebooks/` directory and connect to the running MongoDB database to perform your analysis.

---

## Data Sources

The ETL pipeline currently processes the following data sources from the `data/` directory:

* **SILSO (Royal Observatory of Belgium):** Provides historical daily sunspot number data (`SN_d_tot_V2.0.csv`), used as a proxy for the 11-year solar cycle.
* **SOHO/LASCO CME CATALOG:** Provides an extensive catalog of all measured Coronal Mass Ejection (CME) events (`cme_catalog.txt`) dating back to 1996.
