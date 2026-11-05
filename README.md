# Space Weather Analysis

A self-hostable data platform that ingests, stores, and visualizes space-weather time-series data from multiple providers through a MongoDB-backed Streamlit dashboard.

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Data Sources and Licenses](#data-sources-and-licenses)
- [Tech Stack](#tech-stack)
- [Getting Started](#getting-started)
- [Project Structure](#project-structure)
- [Background](#background)
- [Authors](#authors)
- [License](#license)

## Overview

Space-weather data is distributed across several providers, formats, and APIs: NASA's CDAWeb, GFZ Potsdam, NOAA SWPC, HAPI servers, and plain CSV/text catalogs. This project collects these heterogeneous time-series sources into a single MongoDB store and serves them through an interactive Streamlit dashboard.

The data model follows the physical chain of space weather: phenomena originate at the Sun, propagate through the heliosphere, and drive activity in Earth's magnetosphere. The system is designed to run as a containerized, always-on service.

## Features

- Ingestion of time-series data from NASA CDAWeb, GFZ, NOAA SWPC, HAPI servers, and local files through one pipeline with a separate adapter per source type.
- Feeds, configured in `config/feeds.toml`, keep selected datasets current: each feed is loaded on start and fetched again at its interval, down to once a minute for live data.
- Incremental loading: data is fetched in calendar chunks sized by the cadence of the dataset, chunks whose data is stable are skipped, recent and provisional data is fetched again, and only changed documents are written.
- One processing lane per source type, so a slow or unreachable provider does not hold up the others; an unreachable provider is detected and its remaining requests are skipped.
- A master catalog of the available datasets and descriptive metadata for each dataset, maintained in the background from the metadata services of the providers and complemented by curated metadata that names the page every value was taken from.
- A live dashboard built from configurable panels (values with NOAA scale levels, time series, tables, feed status), which redraws when new data arrives and shows units, sources, licenses, data freshness, gaps, and the valid range of each field.
- A dataset explorer and a downloader that queues a fetch job for a dataset not yet stored.
- A Docker Compose stack that runs the metadata worker, pipeline service, frontend, database, and database admin UI together.

## Architecture

The system is a modular monolith: a shared core library (`space_weather_analysis`) plus three separately-runnable components. All components share one codebase and one Docker image and communicate through MongoDB rather than over the network.

- Metadata Worker: a long-running background process that builds and maintains the master catalog and the dataset metadata. It repeats all metadata requests (HAPI metadata mirror, CDAWeb dataset list, per-dataset master files and SPASE records, dataset availability) on a schedule, one request at a time with a random pause per provider host, keeping the last good state when a provider is unreachable. It never deletes catalog entries, it only counts sources a provider no longer lists.
- Pipeline Service: keeps the feeds of `config/feeds.toml` current and processes the fetch jobs of the frontend. A scheduler queues one job per feed on start and whenever its interval has passed; a job runner works off the jobs of each source type in its own lane. The pipelines fetch in calendar chunks, record which chunks are stable, write only changed documents, and save the dataset metadata with the curated metadata of `config/metadata/` laid over it.
- Frontend: the Streamlit app with the live dashboard, whose panels are configured in `config/feeds.toml`, a dataset explorer, and a downloader that queues fetch jobs.
- MongoDB: the shared data store, holding the master catalog, the metadata of each dataset, one time series collection per dataset, the stored coverage and chunk status of each dataset, and the job queue. `mongo-express` provides a web admin UI.

![Architecture diagram: external data sources feed the ingestion components, which write to MongoDB collections read by the consumers](docs/architecture.drawio.svg)

Solid arrows show data flow; dashed arrows show the job queue between the frontend and the pipeline service. The files in `config/` give the feeds and the curated metadata; the frontend also reads the dashboard panels from `config/feeds.toml`, which the diagram leaves out. The diagram is an editable draw.io file and can be opened directly in draw.io.

The shared `space_weather_analysis` library (clients, configuration, database connection, logging, utilities) is used in-process by all three components, keeping domain logic independent of MongoDB, HTTP, and Streamlit.

## Data Sources and Licenses

The MIT License of this repository covers the source code only. This repository contains no third-party data: all data the project uses is obtained directly from its providers and remains under their terms. Several sources permit non-commercial use only. Check the linked terms before reusing any data, including data displayed by a deployed instance of the dashboard. The terms below were reviewed in October 2026.

### Local data files

The following files are not part of this repository. They are downloaded from their providers and stored locally in `data/`.

| File | Provider | How to obtain | License / terms |
| --- | --- | --- | --- |
| `data/SN_d_tot_V2.0.csv` | WDC-SILSO, Royal Observatory of Belgium | [Download](https://www.sidc.be/SILSO/INFO/sndtotcsv.php) | [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) |
| `data/SN_m_tot_V2.0.csv` | WDC-SILSO, Royal Observatory of Belgium | [Download](https://www.sidc.be/SILSO/INFO/snmtotcsv.php) | [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) |
| `data/SN_ms_tot_V2.0.csv` | WDC-SILSO, Royal Observatory of Belgium | [Download](https://www.sidc.be/SILSO/INFO/snmstotcsv.php) | [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) |
| `data/cme_catalog.txt` | CDAW Data Center (NASA, The Catholic University of America, Naval Research Laboratory) | [Download](https://cdaw.gsfc.nasa.gov/CME_list/UNIVERSAL_ver2/text_ver/univ_all.txt) | No formal license; [acknowledgement requested](https://cdaw.gsfc.nasa.gov/CME_list/) |

Details on each file: [docs/local-data.md](docs/local-data.md).

### Data retrieved at runtime

| Provider | Data | Access | License / terms |
| --- | --- | --- | --- |
| NASA SPDF (CDAWeb, SSCWeb) | Heliophysics mission data and spacecraft orbits | CDAS web services (cdasws), HAPI | Public domain (CC0), citation requested ([data use policy](https://spdf.gsfc.nasa.gov/data_use_policy.html)) |
| NASA CCMC (iSWA) | Space weather products | HAPI | NASA open data policy, acknowledgement requested ([publication policy](https://ccmc.gsfc.nasa.gov/publication-policy)) |
| GFZ Helmholtz Centre for Geosciences | Kp index | GFZ web service | [CC BY 4.0](https://kp.gfz.de/en/) |
| NOAA Space Weather Prediction Center (SWPC) | Real-time GOES X-ray and proton flux, estimated Kp | [SWPC JSON services](https://services.swpc.noaa.gov/json/) | Public domain (U.S. government work) |
| WDC for Geomagnetism, Edinburgh (BGS) | Ground magnetometer data | HAPI | Free for scientific use; commercial use requires contacting the data supplier ([usage](https://wdc.bgs.ac.uk/usage)) |
| INTERMAGNET (via BGS GIN) | Observatory magnetometer data | HAPI | CC BY-NC 4.0 unless otherwise noted; commercial use and bulk redistribution require written permission ([conditions](https://intermagnet.org/data_conditions.html)) |
| SuperMAG (JHU/APL) | Ground magnetometer data and indices | HAPI | Fair use under the SuperMAG rules of the road; data, plots, and derived data products must not be redistributed; acknowledgement required ([rules of the road](https://supermag.jhuapl.edu/info/?page=rulesoftheroad)) |
| AMDA (CDPP) | Space physics datasets from several missions | HAPI | [AMDA use policy](https://amda.irap.omp.eu/help/policy.html); the original provider's rules also apply |
| ESA (VirES for Swarm) | Swarm satellite data | HAPI | [ESA terms and conditions](https://earth.esa.int/eogateway/documents/20142/1564626/Terms-and-Conditions-for-the-use-of-ESA-Data.pdf) |
| ESA Cluster Science Archive | Cluster mission data (configured, no datasets catalogued) | HAPI | CC BY-NC 3.0 IGO ([terms](https://cosmos.esa.int/web/esdc/terms-and-conditions)) |
| KNMI | Space weather data | HAPI | No published terms found; includes third-party data |
| Helioviewer Project | Solar data products | HAPI | No published terms found |
| LASP LISIRD | Solar irradiance data | HAPI | No published terms found |
| University of Iowa (das2) | Space physics datasets | HAPI | No published terms found |
| hapi-server.org test servers | Synthetic test data | HAPI | Test data for HAPI clients |

### Acknowledgements

The providers request the following acknowledgements and citations. Providers and the entries of each list are in alphabetical order.

<details>
<summary>AMDA</summary>

Data analysis was performed with the AMDA science analysis system provided by the Centre de Données de la Physique des Plasmas (CDPP) supported by CNRS, CNES, Observatoire de Paris and Université Paul Sabatier, Toulouse.

</details>

<details>
<summary>ESA</summary>

Data provided by the European Space Agency (ESA). For Cluster data, we acknowledge the instrument teams and the Cluster Science Archive.

</details>

<details>
<summary>GFZ Kp index</summary>

Matzka, J., Stolle, C., Yamazaki, Y., Bronkalla, O. and Morschhauser, A. (2021). The geomagnetic Kp index and derived indices of geomagnetic activity. Space Weather, <https://doi.org/10.1029/2020SW002641>. Data: Matzka, J., Bronkalla, O., Tornow, K., Elger, K. and Stolle, C. (2021). Geomagnetic Kp index. V. 1.0. GFZ Data Services, <https://doi.org/10.5880/Kp.0001>

</details>

<details>
<summary>INTERMAGNET</summary>

This project relies on data collected at magnetic observatories. We thank the national institutes that support them and INTERMAGNET for promoting high standards of magnetic observatory practice (<www.intermagnet.org>).

</details>

<details>
<summary>NASA CCMC</summary>

We acknowledge the Community Coordinated Modeling Center (CCMC) at Goddard Space Flight Center for the use of iSWA, <https://ccmc.gsfc.nasa.gov/tools/iSWA/>

</details>

<details>
<summary>NASA SPDF</summary>

NASA Space Physics Data Facility (SPDF), <https://doi.org/10.17616/R3P301>. Individual CDAWeb datasets name their instrument teams and specific requirements in their `Acknowledgement` and `Rules_of_use` metadata attributes.

</details>

<details>
<summary>NOAA SWPC</summary>

Data provided by the NOAA Space Weather Prediction Center, <https://www.spaceweather.gov/>.

</details>

<details>
<summary>SILSO</summary>

Source: WDC-SILSO, Royal Observatory of Belgium, Brussels, <https://doi.org/10.24414/qnza-ac80>

</details>

<details>
<summary>SOHO/LASCO CME catalog</summary>

"This CME catalog is generated and maintained at the CDAW Data Center by NASA and The Catholic University of America in cooperation with the Naval Research Laboratory. SOHO is a project of international cooperation between ESA and NASA." References: Yashiro et al. (2004), <https://doi.org/10.1029/2003JA010282>; Gopalswamy et al. (2009), <https://doi.org/10.1007/s11038-008-9282-7>

</details>

<details>
<summary>SuperMAG (JHU/APL)</summary>

SuperMAG data and indices, including those retrieved through AMDA or KNMI, are acknowledged as required by the [SuperMAG rules of the road](https://supermag.jhuapl.edu/info/?page=rulesoftheroad):

- All SuperMAG data: Gjerloev, J. W. (2012), The SuperMAG data processing technique, J. Geophys. Res., 117, A09213, <https://doi.org/10.1029/2012JA017683>
- Indices: "We gratefully acknowledge the SuperMAG collaborators (<https://supermag.jhuapl.edu/info/?page=acknowledgement>)". References for the indices:
  - SML, SMU, SME: Newell, P. T., and J. W. Gjerloev (2011), Evaluation of SuperMAG auroral electrojet indices as indicators of substorms and auroral power, J. Geophys. Res., 116, A12211, <https://doi.org/10.1029/2011JA016779>
  - SMLs, SMLd, SMUs, SMUd: Gjerloev, J. W., R. A. Hoffman, S. Ohtani, J. Weygand, and R. Barnes (2010), Response of the Auroral Electrojet Indices to Abrupt Southward IMF Turnings, Annales Geophysicae, 28, 1167-1182.
  - SME-LT, SMU-LT, SML-LT: Newell, P. T., and J. W. Gjerloev (2014), Local geomagnetic indices and the prediction of auroral power, J. Geophys. Res. Space Physics, 119, <https://doi.org/10.1002/2014JA020524>
  - SMR, SMR-LT: Newell, P. T. and J. W. Gjerloev (2012), SuperMAG-Based Partial Ring Current Indices, J. Geophys. Res., 117, <https://doi.org/10.1029/2012JA017586>
- Ground magnetometer data: the acknowledgement and the station network references below.

For the ground magnetometer data we gratefully acknowledge:

- 210 Chain, PI K. Yumoto
- AARI, PI Oleg Troshichev
- AUTUMN, PI Martin Connors
- BCMT, V. Lesur and A. Chambodut
- BGS, PI Dr. Susan Macmillan
- British Artarctic Survey
- CANMOS, Geomagnetism Unit of the Geological Survey of Canada
- CARISMA, PI Ian Mann
- Data obtained in cooperation with Geoscience Australia, PI Andrew Lewis
- Data obtained in cooperation with the Australian Bureau of Meteorology, PI Richard Marshall
- DTU Space, Thom Edwards and PI Anna Willer
- Finnish Meteorological Institute, PI Liisa Juusola
- Geological Survey of Sweden, PI Gerhard Schwarz
- Geophysical and Astronomical Observatory of the University of Coimbra, PI Paulo Ribeiro
- GFZ German Research Centre For Geosciences, PI Jürgen Matzka
- GIMA
- ICESTAR
- IMAGE, PI Liisa Juusola
- Institute of Geophysics, Polish Academy of Sciences, PI Anne Neska and Jan Reda
- INTERMAGNET
- Leibniz Institute of Atmospheric Physics, PI Jorge Chau
- LISN PI Cesar Valladares
- MagStar, PI Jennifer Gannon
- McMac, PI Dr. Peter Chi
- MEASURE, UCLA IGPP and Florida Institute of Technology
- MFGI, PI B. Heilig
- PENGUIn, co-PIs Bob Clauer, Michael Hartinger, and Zhonghua Xu
- Polar Geophysical Institute, PI Alexander Yahnin and Yarolav Sakharov
- Pushkov Institute of Terrestrial Magnetism, Ionosphere and Radio Wave Propagation (IZMIRAN)
- RAPIDMAG
- SAMBA, PI Eftyhia Zesta
- SAMNET, PI Farideh Honary
- Sodankylä Geophysical Observatory, PI Tero Raita
- South Pole and McMurdo Magnetometer, PI's Louis J. Lanzarotti and Alan T. Weatherwax
- SuperMAG, PI Rafael Mesquita
- Swedish Institute of Space Physics, PI Masatoshi Yamauchi
- The MACCS program, PI M. Engebretson
- The S-RAMP Database, PI K. Yumoto and Dr. K. Shiokawa
- The SPIDR database
- UiT the Arctic University of Norway, Tromsø Geophysical Observatory, PI Magnar G. Johnsen
- University of L'Aquila, PI M. Vellante
- USGS Geomagnetism Program

Station network references:

- AALPIP: Clauer, C. R., et al. (2014), An autonomous adaptive low-power instrument platform (AAL-PIP) for remote high-latitude geospace data collection, Geosci. Instrum. Methods Data Syst., 3, 211-227, <https://doi.org/10.5194/gi-3-211-2014>
- CARISMA: Mann, I. R., et al. (2008), The upgraded CARISMA magnetometer array in the THEMIS era, Space Sci. Rev., 141, 413-451, <https://doi.org/10.1007/s11214-008-9457-6>
- EMMA: Lichtenberger J., M. Clilverd, B. Heilig, M. Vellante, J. Manninen, C. Rodger, A. Collier, A. Jørgensen, J. Reda, R. Holzworth, and R. Friedel (2013), The plasmasphere during a space weather event: first results from the PLASMON project, J. Space Weather Space Clim., 3, A23, <https://www.swsc-journal.org/articles/swsc/pdf/2013/01/swsc120062.pdf>
- IMAGE: Tanskanen, E.I. (2009), A comprehensive high-throughput analysis of substorms observed by IMAGE magnetometer network: Years 1993-2003 examined, 114, A05204, <https://doi.org/10.1029/2008JA013682>
- INTERMAGNET: Love, J. J., Chulliat, A. (2013), An international network of magnetic observatories, Eos, 94(42), 373-374, <https://doi.org/10.1002/2013EO420001>
- MACCS: Engebretson, M. J., W. J. Hughes, J. L. Alford, E. Zesta, L. J. Cahill, Jr., R. L. Arnoldy, and G. D. Reeves (1995), Magnetometer array for cusp and cleft studies observations of the spatial extent of broadband ULF magnetic pulsations at cusp/cleft latitudes, J. Geophys. Res., 100, 19371-19386, <https://doi.org/10.1029/95JA00768>
- MAGDAS / 210 Chain: Yumoto, K., and the CPMN Group (2001), Characteristics of Pi 2 magnetic pulsations observed at the CPMN stations: A review of the STEP results, Earth Planets Space, 53, 981-992.
- McMAC: Chi, P. J., M. J. Engebretson, M. B. Moldwin, C. T. Russell, I. R. Mann, M. R. Hairston, M. Reno, J. Goldstein, L. I. Winkler, J. L. Cruz-Abeyro, D.-H. Lee, K. Yumoto, R. Dalrymple, B. Chen, and J. P. Gibson (2013), Sounding of the plasmasphere by Mid-continent MAgnetoseismic Chain magnetometers, J. Geophys. Res. Space Physics, 118, <https://doi.org/10.1002/jgra.50274>

</details>

<details>
<summary>WDC for Geomagnetism, Edinburgh</summary>

We acknowledge the WDC for Geomagnetism (Edinburgh) and the institutes that supply its data.

</details>

## Tech Stack

- Language: Python 3.12
- Dependency management: uv (with lockfile)
- Configuration: pydantic-settings (environment variables) and TOML files (feeds, dashboard panels, curated metadata)
- Storage: MongoDB time series collections, browsed via mongo-express
- Frontend: Streamlit and Plotly
- Data handling: pandas, NumPy, xarray; cdasws, cdflib and netCDF4 for CDAWeb; hapiclient for HAPI servers
- Containerization: Docker and Docker Compose
- Quality tooling: pytest with testcontainers, Ruff (lint and format), mypy

## Getting Started

The full stack runs in Docker. uv is only required for local development, tests, and notebooks.

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with the Docker Compose plugin
- [uv](https://docs.astral.sh/uv/) (for development only)

### Run the stack

1. Copy the example environment file and set credentials:

   ```bash
   cp example.env .env
   ```

2. Review `config/feeds.toml`: its feeds decide which datasets the pipeline service keeps current, and its `dashboard` table which panels the dashboard shows.

3. The pipeline service downloads the local data files into `data/` itself when they are missing or older than a day (see [Data Sources and Licenses](#data-sources-and-licenses) for their terms). To fetch them by hand instead:

   ```bash
   curl -o data/SN_d_tot_V2.0.csv https://www.sidc.be/SILSO/INFO/sndtotcsv.php
   curl -o data/SN_m_tot_V2.0.csv https://www.sidc.be/SILSO/INFO/snmtotcsv.php
   curl -o data/SN_ms_tot_V2.0.csv https://www.sidc.be/SILSO/INFO/snmstotcsv.php
   curl -o data/cme_catalog.txt https://cdaw.gsfc.nasa.gov/CME_list/UNIVERSAL_ver2/text_ver/univ_all.txt
   ```

4. Build and start all services:

   ```bash
   docker compose up --build -d
   ```

   This starts five services:

   | Service | Purpose | Exposed at |
   | --- | --- | --- |
   | `frontend` | Streamlit dashboard | <http://localhost:8501> |
   | `mongo-express` | MongoDB web admin UI | <http://localhost:8081> |
   | `mongo` | MongoDB database | `localhost:27017` |
   | `pipeline-service` | ETL / data pipeline | (no public port) |
   | `metadata-worker` | Catalog and metadata maintenance | (no public port) |

   The ports shown are the defaults; the published ports can be changed in `.env` (see `example.env`).

5. Open the dashboard at [http://localhost:8501](http://localhost:8501). On a fresh database the metadata worker needs a few minutes to build the catalog; feeds whose datasets are not catalogued yet wait for it.

6. Stop the stack:

   ```bash
   docker compose down
   ```

   Add `-v` to also remove the database volume.

### Local development

```bash
# Install all dependencies, including dev tools
uv sync

# Run the test suite
uv run pytest

# Lint and format
uv run ruff check src tests
uv run ruff format src tests

# Type-check (src and tests, as configured in pyproject.toml)
uv run mypy
```

`pytest` runs the unit tests; tests marked `integration` need Docker, start a throwaway MongoDB themselves (no running stack or credentials needed) and are run with `uv run pytest -m integration`.

Test coverage can be inspected with `uv run pytest --cov=src --cov-report=term-missing`.

Jupyter notebooks for exploratory analysis live in `notebooks/` and can be run with `uv run jupyter lab`.

Tools started outside Docker, such as the notebooks, can load the variables from `.env` with `uv run --env-file .env <command>`. Variables already set in the environment take precedence over the file, so `MONGO_HOST=localhost uv run --env-file .env jupyter lab` reaches the MongoDB published on the host (add `MONGO_PORT=<published port>` if the port was changed).

## Project Structure

```text
.
├── .github/workflows/demo.yml  # Hourly build of the static dashboard demo on GitHub Pages
├── docker-compose.yml          # Five-service stack definition
├── Dockerfile                  # Shared image for all Python services
├── pyproject.toml              # Project metadata, dependencies, tool config
├── uv.lock                     # Locked dependency versions
├── example.env                 # Template for required environment variables
├── config/                     # feeds.toml: feeds that keep datasets current, and the dashboard panels
│   └── metadata/               # Curated metadata per provider, with the pages each value was taken from
├── data/                       # Local data files (not in the repository) and HAPI server list
├── docs/                       # Architecture diagram (editable draw.io SVG) and local data notes
├── notebooks/                  # Jupyter notebooks for exploratory analysis
├── web/                        # Static web frontend of the dashboard (HTML, JavaScript, Plotly.js), drawing the exported JSON documents
├── src/
│   ├── space_weather_analysis/ # Shared core library used by all components
│   │   ├── catalog/            # Master catalog of the datasets and their sources
│   │   ├── clients/            # Provider clients: HAPI, CDAWeb, GFZ, SWPC
│   │   ├── metadata/           # Dataset metadata: builders per source, overlays of further sources, curated metadata
│   │   ├── timeseries/         # Time series documents and their storage in MongoDB time series collections
│   │   ├── coverage/           # Stored time ranges of each dataset
│   │   ├── jobs/               # Job queue shared by frontend and pipeline service
│   │   ├── scales/             # NOAA space weather scales and flare classes
│   │   ├── dashboard/          # Dashboard configuration, panel types, labels, freshness, valid ranges and panel JSON documents
│   │   └── config/, database/, logging/, util/
│   ├── catalog_collector/      # Metadata worker (metadata_worker.py)
│   │   ├── data_catalog/       # Master catalog: GFZ entries and availability refresh
│   │   ├── metadata_catalog/   # Dataset metadata: HAPI, CDAS, master file and SPASE metadata refresh
│   │   ├── cdaweb/             # CDAWeb access: dataset list (CDAS web services) and master files
│   │   ├── spase/              # SPASE records of the datasets (spase-metadata.org)
│   │   ├── hapi_mirror/        # HAPI metadata mirror files and the server lookup shared by its refreshes
│   │   ├── scheduling/         # Task queue and worker for recurring metadata requests
│   │   └── worker_tasks/       # Task kinds the metadata worker runs, with their intervals
│   ├── pipeline_service/       # Ingestion service (main.py) and job runner (jobs/runner.py)
│   │   ├── feeds/              # Feed file model and the scheduler queuing feed jobs
│   │   ├── chunk_status/       # Status of each fetched chunk, which decides when it is fetched again
│   │   ├── pipelines/          # One pipeline per source type, the shared chunk loop and the pipeline factory
│   │   └── parsing/            # Conversion of delivered data and local files into frames
│   ├── site_export/            # Export of the dashboard as JSON documents for a static web frontend (main.py)
│   └── streamlit_app/          # Streamlit app (main.py)
│       ├── pages/              # Dashboard, dataset explorer and downloader pages
│       ├── dashboard/          # Drawing of the dashboard panels in Streamlit
│       │   └── panels/         # One drawing function per panel type: value, time series, table, feed status
│       └── figures/, plots/    # Plot builders of the explorer
└── tests/                      # Unit tests (tests/unit) and integration tests (tests/integration)
```

## Background

This project began as a Data Science coursework assignment (*Hausarbeit*, module DSCB-230-WS25/26) at [Hochschule Karlsruhe (HKA)](https://www.h-ka.de/) and is being developed further into a maintained, self-hosted service.

## Authors

- Patrick Heß
- Sebastian Straßner

## License

The source code of this project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details. Third-party data is not covered by this license; see [Data Sources and Licenses](#data-sources-and-licenses).
