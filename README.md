# Space Weather Analysis Project

## Project Overview

This repository contains a Data Science project developed as part of a the DSCB-230-WS25/26 Hausarbeit at [HKA](https://www.h-ka.de/). The project's goal is to analyze space weather phenomena and their impact on Earth.

The analysis follows the physical causal chain of events, starting from the Sun, propagating through the heliosphere, and finally impacting Earth's magnetosphere.

The core components of this project are:

1. A robust ETL pipeline to fetch, clean, and store multi-instrument space physics data in a MongoDB database.

2. Exploratory Data Analysis (EDA) to investigate known physical relationships, such as the 11-year solar cycle and the in-situ signatures of Coronal Mass Ejections (CMEs).

3. A predictive model to forecast geomagnetic activity (using the Kp-Index) based on real-time solar wind data.

### Project Structure

The repository is organized as a modern Python package:

```bash
.
├── docker-compose.yml         # Defines the MongoDB service and starts the Python Script
├── poetry.lock                # Dependency lock file
├── pyproject.toml             # Project definition and dependencies (for Poetry)
├── data                       # Contains all data source files
├── notebooks/                 # Jupyter notebooks for EDA and modeling
├── src/
│   └── space_weather_analysis/
│       ├── main.py            # Main entry point to run the ETL pipeline
│       ├── database/          # (Module) Database connection and setup
│       ├── etl/               # (Module) ETL logic (Extract, Transform, Load)
│       └── util/              # (Module) Utility code (logging, exceptions, preprocessing)
└── tests/                     # Unit and integration tests
```

### Authors

`Group Papa`:

* Patrick Heß
* Sebastian Straßner

### License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Preconditions & Installation

This project relies on Docker for the database and Poetry for Python dependency management.

1. Database Setup (Docker)

    The project requires a running MongoDB instance. We use Docker and Docker Compose to manage this service.

    Install Docker: Ensure you have Docker and Docker Compose installed. We recommend using Docker Desktop for your operating system.

    Start the Database Service: Navigate to the project's root directory (where [docker-compose.yml](docker-compose.yml) is located) and run:

    ```Bash
    # This starts the MongoDB container in detached mode (background)

    docker-compose up -d
    ```

    Stop the Database Service: When you are finished, you can stop the container:

    ```Bash
    # This ends the MongoDB container. The -v flag also removes the volume and deletes all data inside of it.

    docker-compose down -v
    ```

    For futher explanations please consult the official [documentation](https://docs.docker.com/reference/cli/docker/compose/).

2. Python Environment (Poetry)

    We use Poetry to manage all Python dependencies and virtual environments.
    For installation and [configuration](https://python-poetry.org/docs/configuration/) (e.g. create virtual environments localy) consult the [documentation](https://python-poetry.org/docs/)

    ```Bash
    poetry config virtualenvs.in-project true
    ```

    Install Dependencies: With Poetry installed, navigate to the project root and install all required packages:

    ```Bash
    # This reads the pyproject.toml and installs dependencies

    poetry install
    ```

    Activate the Virtual Environment: To work within the project's environment, you can either:

    Run commands with poetry run `command` (e.g., poetry run python src/space_weather_analysis/main.py)

    Or, spawn a new shell inside the environment:

    ```Bash
        poetry shell
    ```

3. Environment Variables

    The Database uses environment variables to establish a conncetion. These variables can be injected via a `.env` file which can be loaded by Python with the `dotenv` Module.

    The [example.env](example.env) file gives an example how the variable should look like and can be used for local deployment.

    For that you need to change the filename to only `.env` or copy the content in a new file with that name.

## Data Sources

The ETL pipeline is designed to acquire data from several key heliophysics data sources, primarily using the sunpy Python library.

* GOES (Geostationary Operational Environmental Satellite): Provides X-ray flux data from the XRS instrument, used to identify solar flares.

* OMNIWeb Database (via CDAWeb): Aggregates 1-minute resolution solar wind data from satellites at the L1 point (e.g., ACE, DSCOVR). Key parameters include IMF Bz​, solar wind speed (V), and density (N).

* SILSO (Royal Observatory of Belgium): Provides historical sunspot number data, used as a proxy for the 11-year solar cycle.

* GFZ Potsdam: Provides the 3-hour Kp-Index, a measure of global geomagnetic activity used as the target variable for our predictive model.

* SOHO/LASCO CME CATALOG: Provides a extensive catalog of all measured Coronal Mass Ejection (CME's) events dating back to 1996.
