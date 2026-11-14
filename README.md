# Space Weather Analysis

A self-hostable data platform that ingests, stores, and visualizes space-weather time-series data from multiple providers through a MongoDB-backed web dashboard.

Static demo of the dashboard, rebuilt every hour from the providers' data: <https://arduel1.github.io/space_weather_analysis/>

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

Space-weather data is distributed across many providers, formats, and APIs. This project collects these heterogeneous time-series sources into a single MongoDB store and shows them in an interactive web dashboard.

The data model follows the physical chain of space weather: phenomena originate at the Sun, propagate through the heliosphere, and drive activity in Earth's magnetosphere. The system is designed to run as a containerized, always-on service.

## Features

- Ingestion from NASA CDAWeb, CCMC DONKI, GFZ, NOAA SWPC (products, image sequences and OVATION aurora maps), NSO GONG, the Stanford JSOC (SDO/HMI synoptic maps), the LMSAL Heliophysics Event Knowledgebase (HEK), JPL Horizons, Helioviewer, HAPI (Heliophysics Application Programming Interface) servers, and local files through one pipeline with a separate adapter per source type.
- Feeds, configured in `config/feeds.toml`, keep selected datasets current: each feed is loaded on start and fetched again at its interval, down to once a minute for live data.
- Incremental loading: data is fetched in calendar chunks sized by the cadence of the dataset, stable chunks are skipped, recent and provisional data is fetched again, and only changed documents are written.
- One processing lane per source type, so a slow or unreachable provider does not hold up the others.
- A master catalog of the available datasets and descriptive metadata for each dataset, maintained in the background from the metadata services of the providers and complemented by curated metadata that names the page every value was taken from.
- A web dashboard of configurable pages and panels, the same site locally on the live data and publicly as the hourly demo:
  - Overview: status tiles with NOAA scale levels and the rank of the newest value in the past week or year (the solar wind speed also as travel time to Earth, Kp also as the latitude the aurora reaches), solar, coronagraph and aurora images of the last day, the last day from the Sun to the ground on one time axis (flares, solar wind, southward field shaded, Dst, Kp), linked DONKI events of the last 30 days, SWPC alerts and the status of every feed
  - Sun: six extreme ultraviolet wavelengths and both coronagraphs, the NOAA sunspot regions on SDO HMI and AIA images with the coronal holes found in each AIA image, a close look at the newest flare, the coronal magnetic field of the PFSS (potential field source surface) model from the hourly GONG magnetograms (the daily SDO/HMI map where GONG does not answer, as in the demo) as a turnable 3D Sun playing the last day and as a map, and a butterfly diagram since 1996 with the sunspot numbers of both hemispheres, the polar fields (WSO), F10.7 and CMEs
  - Sun to Earth: the inner solar system from above with planets and spacecraft, field lines bent by the measured solar wind and the CMEs of the last weeks flying out (drag-based model) with the arrivals WSA-Enlil expects and the shocks measured; WSA-Enlil at Earth five days ahead against the measured solar wind; the solar wind at STEREO A and Earth; the magnetic connection of flares to Earth and STEREO A with the protons measured there
  - Earth: the aurora of the last day on a turnable Earth from the OVATION maps with the night-side edge of the oval against the rule of thumb from Kp, two weeks of Kp, Hp30, Dst and SYM-H, radiation belt electrons beside the solar wind speed, and the ionosphere (D-RAP radio absorption, GloTEC electron content)
  - Connections, from the data since 1996: every CME of the CDAW catalog and every intense storm below the sunspot cycle, storm hours per month, average Kp by the direction of the solar wind field for slow, medium and fast wind, and all intense storms laid over each other at their lowest Dst (superposed epoch)
  - one replay bar per page moving the image sequences and the charts of the last days together, and an info view per panel built from the metadata of its datasets
- A dataset explorer and a downloader that queues a fetch job for a dataset not yet stored (Streamlit).
- A Docker Compose stack that runs all components together.

## Architecture

The system is a modular monolith: a shared core library (`space_weather_analysis`) plus four separately-runnable components. All components share one codebase and one Docker image and communicate through MongoDB rather than over the network.

- Metadata Worker: a long-running background process that builds and maintains the master catalog and the dataset metadata. It repeats all metadata requests (HAPI metadata mirror, CDAWeb dataset list, per-dataset master files and SPASE (Space Physics Archive Search and Extract) records, dataset availability) on a schedule, one request at a time per provider host, keeping the last good state when a provider is unreachable.
- Pipeline Service: keeps the feeds of `config/feeds.toml` current and processes the fetch jobs of the Streamlit app. A scheduler queues one job per feed on start and whenever its interval has passed; a job runner works off the jobs of each source type in its own lane. The dataset metadata is saved with the curated metadata of `config/metadata/` laid over it.
- Web dashboard: `site_export` writes the pages of `config/dashboard/` as JSON documents and serves them next to the static web frontend in `web/`; locally it writes them again every minute, the demo workflow every hour.
- Streamlit app: a dataset explorer and a downloader that queues fetch jobs.
- MongoDB: the shared data store, holding the master catalog, the metadata of each dataset and the raw provider metadata it is built from, one time series collection per dataset, the stored coverage and the chunk status of each dataset, the job queue and the task queue of the metadata worker. `mongo-express` provides a web admin UI.

![Architecture diagram: external data sources feed the ingestion components, which write to MongoDB collections read by the consumers](docs/architecture.drawio.svg)

Arrows point in the direction the data flows; double arrows mark collections a component reads and writes, with each label next to the arrowhead it describes. Dashed arrows show the job queue between the Streamlit app and the pipeline service. The diagram is an editable draw.io file.

## Data Sources and Licenses

The MIT License of this repository covers the source code only. The repository contains no third-party data and no copied third-party code: all data is obtained directly from its providers and remains under their terms, and all libraries are installed from their package registries. Several sources permit non-commercial use only. Check the linked terms before reusing any data, including data displayed by a deployed instance of the dashboard. The terms below were reviewed in October and November 2026.

### Local data files

The following files are not part of this repository. The pipeline service downloads them into `data/`.

| File | Provider | How to obtain | License / terms |
| --- | --- | --- | --- |
| `data/SN_d_tot_V2.0.csv` | WDC-SILSO, Royal Observatory of Belgium | [Download](https://www.sidc.be/SILSO/INFO/sndtotcsv.php) | [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) |
| `data/SN_m_tot_V2.0.csv` | WDC-SILSO, Royal Observatory of Belgium | [Download](https://www.sidc.be/SILSO/INFO/snmtotcsv.php) | [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) |
| `data/SN_ms_tot_V2.0.csv` | WDC-SILSO, Royal Observatory of Belgium | [Download](https://www.sidc.be/SILSO/INFO/snmstotcsv.php) | [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) |
| `data/SN_m_hem_V2.0.csv` | WDC-SILSO, Royal Observatory of Belgium | [Download](https://www.sidc.be/SILSO/INFO/snmhemcsv.php) | [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) |
| `data/wso_polar.html` | Wilcox Solar Observatory, Stanford University | [Download](http://wso.stanford.edu/Polar.html) | No terms stated; acknowledgement as customary |
| `data/icme_list.html` | Ian Richardson and Hilary Cane (NASA GSFC, University of Tasmania) | [Download](https://izw1.caltech.edu/ACE/ASC/DATA/level3/icmetable2.htm) | No terms stated; the list asks to include its DOI ([10.7910/DVN/C2MHTH](https://doi.org/10.7910/DVN/C2MHTH)) |
| `data/cme_catalog.txt` | CDAW Data Center (NASA, The Catholic University of America, Naval Research Laboratory) | [Download](https://cdaw.gsfc.nasa.gov/CME_list/UNIVERSAL_ver2/text_ver/univ_all.txt) | No formal license; [acknowledgement requested](https://cdaw.gsfc.nasa.gov/CME_list/) |

Details on each file: [docs/local-data.md](docs/local-data.md).

### Web frontend libraries

The web frontend loads these files from its own host. They are installed when the Docker image is built (see [Local development](#local-development) for a build outside Docker).

| Library or data | Version | Source | License / terms |
| --- | --- | --- | --- |
| three.js | 0.186.1 | npm package `three` | [MIT](https://github.com/mrdoob/three.js/blob/dev/LICENSE) |
| Plotly.js | 4.1.2 | npm package `plotly.js-dist-min` | [MIT](https://github.com/plotly/plotly.js/blob/master/LICENSE) |
| Natural Earth coastlines (`ne_110m_coastline.geojson`) | 5.1.2 | [natural-earth-vector](https://github.com/nvkelso/natural-earth-vector) | Public domain ([terms of use](https://www.naturalearthdata.com/about/terms-of-use/)) |

### Data retrieved at runtime

| Provider | Data | Access | License / terms |
| --- | --- | --- | --- |
| NASA SPDF (CDAWeb, SSCWeb) | Heliophysics mission data and spacecraft orbits | CDAS web services (cdasws), HAPI | Public domain (CC0), citation requested ([data use policy](https://spdf.gsfc.nasa.gov/data_use_policy.html)) |
| NASA CCMC (iSWA) | Space weather products | HAPI | NASA open data policy, acknowledgement requested ([publication policy](https://ccmc.gsfc.nasa.gov/publication-policy)) |
| NASA CCMC (DONKI) | Space weather events and the links between them | [DONKI API](https://ccmc.gsfc.nasa.gov/tools/DONKI/) | Provided as is; event studies with the knowledge of the forecasters and CCMC ([rules of the road](https://ccmc.gsfc.nasa.gov/tools/DONKI/)), acknowledgement requested ([publication policy](https://ccmc.gsfc.nasa.gov/publication-policy)) |
| NSF National Solar Observatory (GONG) | Synoptic magnetograms, from which the coronal magnetic field is computed | [GONG synoptic maps](https://gong2.nso.edu/oQR/zqs/) | Acknowledgement required ([NSO data use policy](https://nso.edu/data/)) |
| Stanford JSOC (NASA SDO/HMI) | Synoptic magnetograms, used for the coronal magnetic field where the GONG server does not answer | [HMI synoptic maps](https://jsoc1.stanford.edu/data/hmi/synoptic/) | No copyright; acknowledgement requested ([SDO data rules](https://sdo.gsfc.nasa.gov/data/rules.php)) |
| Helioviewer Project (ESA/NASA) | SDO AIA images rendered at a time, scale and region | [Helioviewer API](https://api.helioviewer.org/docs/v2/) | No terms stated; the SDO images have no copyright, credit requested ([SDO data rules](https://sdo.gsfc.nasa.gov/data/rules.php)) |
| NASA/JPL Solar System Dynamics (Horizons) | Daily positions of spacecraft; planets come from the astropy built-in ephemeris | [Horizons API](https://ssd-api.jpl.nasa.gov/doc/horizons.html) via sunpy | Public domain, citation recommended ([SSD FAQ](https://ssd.jpl.nasa.gov/faq.html)) |
| LMSAL Heliophysics Event Knowledgebase | Daily NOAA sunspot regions since 1996 | [HEK search](https://www.lmsal.com/hek/) | No terms stated; the region reports are NOAA SWPC data |
| GFZ Helmholtz Centre for Geosciences | Kp and Hp30 index | GFZ web service | [CC BY 4.0](https://kp.gfz.de/en/) |
| NOAA Space Weather Prediction Center (SWPC) | Real-time GOES X-ray, proton and electron flux, GOES X-ray flares, alerts, watches and warnings, sunspot regions, estimated Kp, real-time solar wind at L1 and at STEREO A, the WSA-Enlil solar wind at Earth; the products are described in `config/sources/swpc.toml` | [SWPC data services](https://services.swpc.noaa.gov/) | Public domain (U.S. government work) |
| NOAA SWPC image sequences | GOES SUVI solar images, SDO HMI continuum images, SOHO LASCO C2/C3 coronagraph images, OVATION aurora images and maps, WSA-Enlil model images, D-RAP and GloTEC ionosphere maps; described in `config/sources/images.toml` | [SWPC frame lists](https://services.swpc.noaa.gov/products/animations/) | SUVI and OVATION: public domain (U.S. government work); SDO: no copyright, credit requested ([SDO data rules](https://sdo.gsfc.nasa.gov/data/rules.php)); LASCO: free for non-commercial use with acknowledgement ([SOHO copyright notice](https://soho.nascom.nasa.gov/data/summary/copyright.html)) |
| National Research Council Canada (via SWPC) | F10.7 solar radio flux | [SWPC data services](https://services.swpc.noaa.gov/) | No terms stated; acknowledgement requested ([SWPC F10.7 page](https://www.swpc.noaa.gov/phenomena/f107-cm-radio-emissions)) |
| WDC for Geomagnetism, Kyoto | Dst, SYM-H and ASY-H indices | [Kyoto HAPI server](https://wdc.kugi.kyoto-u.ac.jp/hapi/) | Acknowledgement and data DOI required, no commercial use of the indices ([rules](https://wdc.kugi.kyoto-u.ac.jp/wdc/Sec3.html)) |
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
<summary>GFZ Kp and Hp30 index</summary>

Matzka, J., Stolle, C., Yamazaki, Y., Bronkalla, O. and Morschhauser, A. (2021). The geomagnetic Kp index and derived indices of geomagnetic activity. Space Weather, <https://doi.org/10.1029/2020SW002641>. Data: Matzka, J., Bronkalla, O., Tornow, K., Elger, K. and Stolle, C. (2021). Geomagnetic Kp index. V. 1.0. GFZ Data Services, <https://doi.org/10.5880/Kp.0001>

Hp30: Matzka, J., Bronkalla, O., Kervalishvili, G., Rauberg, J. and Yamazaki, Y. (2022). Geomagnetic Hpo index. V. 2.0. GFZ Data Services, <https://doi.org/10.5880/Hpo.0002>; Yamazaki, Y., Matzka, J., Stolle, C., Kervalishvili, G., Rauberg, J., Bronkalla, O., Morschhauser, A., Bruinsma, S., Shprits, Y. Y. and Jackson, D. R. (2022). Geomagnetic Activity Index Hpo. Geophysical Research Letters, 49(10), <https://doi.org/10.1029/2022GL098860>

</details>

<details>
<summary>INTERMAGNET</summary>

This project relies on data collected at magnetic observatories. We thank the national institutes that support them and INTERMAGNET for promoting high standards of magnetic observatory practice (<www.intermagnet.org>).

</details>

<details>
<summary>LMSAL Heliophysics Event Knowledgebase</summary>

Hurlburt, N. et al. (2012), Heliophysics Event Knowledgebase for the Solar Dynamics Observatory (SDO) and Beyond, Solar Physics 275, 67, <https://doi.org/10.1007/s11207-010-9624-2>

</details>

<details>
<summary>NASA CCMC</summary>

We acknowledge the Community Coordinated Modeling Center (CCMC) at Goddard Space Flight Center for the use of iSWA, <https://ccmc.gsfc.nasa.gov/tools/iSWA/>

We acknowledge the Community Coordinated Modeling Center (CCMC) at Goddard Space Flight Center for the use of the DONKI, <https://ccmc.gsfc.nasa.gov/tools/DONKI/>.

</details>

<details>
<summary>NASA/JPL Solar System Dynamics (Horizons)</summary>

NASA/JPL Solar System Dynamics, Horizons On-Line Ephemeris System, <https://ssd.jpl.nasa.gov> (citation recommended in the [SSD FAQ](https://ssd.jpl.nasa.gov/faq.html))

</details>

<details>
<summary>NASA SDO</summary>

Courtesy of NASA/SDO and the AIA, EVE, and HMI science teams. (requested in the [SDO data rules](https://sdo.gsfc.nasa.gov/data/rules.php))

Coronal holes are found in each AIA 193 A image as the areas darker than a share of the median disk brightness, the threshold method of Rotter et al. (2012), <https://doi.org/10.1007/s11207-012-0101-y>.

</details>

<details>
<summary>NASA SPDF</summary>

NASA Space Physics Data Facility (SPDF), <https://doi.org/10.17616/R3P301>. Individual CDAWeb datasets name their instrument teams and specific requirements in their `Acknowledgement` and `Rules_of_use` metadata attributes.

</details>

<details>
<summary>National Research Council Canada (F10.7)</summary>

"These F10.7 measurements are provided courtesy of the National Research Council Canada in partnership with the Natural Resources Canada." (requested on the [SWPC F10.7 page](https://www.swpc.noaa.gov/phenomena/f107-cm-radio-emissions))

</details>

<details>
<summary>NOAA SWPC</summary>

Data provided by the NOAA Space Weather Prediction Center, <https://www.spaceweather.gov/>.

</details>

<details>
<summary>NSF National Solar Observatory (GONG)</summary>

This work utilizes data from the U.S. National Science Foundation (NSF) Global Oscillation Network Group (GONG), built and operated by the NSF National Solar Observatory (NSO) Integrated Synoptic Program (NISP). NSO is managed by the Association of Universities for Research in Astronomy, Inc. (AURA), and funded under a cooperative agreement with the NSF. GONG is partially supported by the National Oceanic and Atmospheric Administration (NOAA). The GONG network of instruments is hosted by the Big Bear Solar Observatory, High Altitude Observatory, Learmonth Solar Observatory, Udaipur Solar Observatory, Instituto de Astrofísica de Canarias, and Cerro Tololo Inter-American Observatory. (required by the [NSO data use policy](https://nso.edu/data/))

The coronal magnetic field is computed with the PFSS model (Altschuler and Newkirk 1969, <https://doi.org/10.1007/BF00145734>; Schatten, Wilcox and Ness 1969, <https://doi.org/10.1007/BF00146478>) using sunkit-magex, the successor of pfsspy (Stansby, Yeates and Badman 2020, <https://doi.org/10.21105/joss.02732>).

</details>

<details>
<summary>Richardson and Cane ICME list</summary>

Richardson and Cane list of near-Earth ICMEs, doi:10.7910/DVN/C2MHTH; Cane and Richardson (2003), doi:10.1029/2002JA009817; Richardson and Cane (2010), doi:10.1007/s11207-010-9568-6.

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
<summary>SOHO/LASCO images</summary>

"Courtesy of SOHO/LASCO consortium. SOHO is a project of international cooperation between ESA and NASA." (requested in the [SOHO copyright notice](https://soho.nascom.nasa.gov/data/summary/copyright.html))

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

<details>
<summary>WDC for Geomagnetism, Kyoto</summary>

- "The Dst index used in this paper/presentation was provided by the WDC for Geomagnetism, Kyoto (<https://wdc.kugi.kyoto-u.ac.jp/wdc/Sec3.html>)." World Data Center for Geomagnetism, Kyoto, M. Nose, T. Iyemori, M. Sugiura, T. Kamei, A. Matsuoka, S. Imajo, and T. Kotani (2015), Geomagnetic Dst index, doi: 10.17593/14515-74000
- "The ASY/SYM indices used in this paper/presentation were provided by the WDC for Geomagnetism, Kyoto (<https://wdc.kugi.kyoto-u.ac.jp/wdc/Sec3.html>)." World Data Center for Geomagnetism, Kyoto, S. Imajo, A. Matsuoka, H. Toh, and T. Iyemori (2022), Mid-latitude Geomagnetic Indices ASY and SYM (ASY/SYM Indices), doi: 10.14989/267216

</details>

<details>
<summary>Wilcox Solar Observatory</summary>

Wilcox Solar Observatory data used in this study were obtained via the web site <http://wso.stanford.edu>, courtesy of J.T. Hoeksema.

</details>

## Tech Stack

- Language: Python 3.12
- Dependency management: uv (with lockfile)
- Configuration: pydantic-settings (environment variables) and TOML files in `config/`
- Storage: MongoDB time series collections, browsed via mongo-express
- Web dashboard: static HTML and JavaScript modules with Plotly.js and three.js
- Data handling: pandas, NumPy, xarray, astropy, sunpy; cdasws, cdflib and netCDF4 for CDAWeb; hapiclient for HAPI servers; sunkit-magex for the PFSS model
- Explorer: Streamlit and Plotly
- Containerization: Docker and Docker Compose
- Quality tooling: pytest with testcontainers, Ruff (lint and format), mypy

## Getting Started

The full stack runs in Docker. uv and Node.js are only required for local development.

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with the Docker Compose plugin
- [uv](https://docs.astral.sh/uv/) (development only)
- [Node.js](https://nodejs.org/) with npm (development only, to serve `web/` outside Docker)

### Run the stack

1. Copy the example environment file and set credentials:

   ```bash
   cp example.env .env
   ```

2. Review the configuration: `config/feeds.toml` decides which datasets the pipeline service keeps current, `config/dashboard/` which pages and panels the dashboard shows.

3. Build and start all services:

   ```bash
   docker compose up --build -d
   ```

   | Service | Purpose | Exposed at |
   | --- | --- | --- |
   | `dashboard` | Web dashboard | <http://localhost:8502> |
   | `frontend` | Streamlit dataset explorer and downloader | <http://localhost:8501> |
   | `mongo-express` | MongoDB web admin UI | <http://localhost:8081> |
   | `mongo` | MongoDB database | `localhost:27017` |
   | `pipeline-service` | Ingestion of feeds and fetch jobs | (no public port) |
   | `metadata-worker` | Catalog and metadata maintenance | (no public port) |

   The published ports can be changed in `.env` (see `example.env`).

4. Open the dashboard at <http://localhost:8502>. On a fresh database the metadata worker needs a few minutes to build the catalog; feeds whose datasets are not catalogued yet wait for it. The pipeline service downloads the local data files into `data/` itself.

5. Stop the stack with `docker compose down`; add `-v` to also remove the database volume.

### Local development

```bash
uv sync                          # install all dependencies, including dev tools
uv run pytest                    # unit tests
uv run pytest -m integration     # integration tests, start a throwaway MongoDB via Docker
uv run ruff check src tests
uv run ruff format src tests
uv run mypy                      # src and tests, as configured in pyproject.toml
```

To serve `web/` outside Docker, install its libraries into `web/vendor/` first:

```bash
cd web
npm ci --ignore-scripts
sh build-vendor.sh
```

Jupyter notebooks for exploratory analysis live in `notebooks/` (`uv run jupyter lab`). Tools started outside Docker can load `.env` with `uv run --env-file .env {command}`; variables already set take precedence, so `MONGO_HOST=localhost uv run --env-file .env jupyter lab` reaches the MongoDB published on the host.

## Project Structure

```text
.
├── .github/workflows/demo.yml  # Hourly build of the static dashboard demo on GitHub Pages
├── docker-compose.yml
├── Dockerfile                  # Shared image for all components
├── pyproject.toml, uv.lock     # Python dependencies and tool configuration
├── example.env                 # Template for the environment variables
├── config/
│   ├── feeds.toml              # Feeds that keep datasets current
│   ├── dashboard/              # One file per dashboard page, page order in pages.toml
│   ├── metadata/               # Curated metadata per provider
│   └── sources/                # SWPC products, image sequences, planets and spacecraft, static catalog items
├── data/                       # Local data files (not in the repository) and HAPI server list
├── docs/                       # Architecture diagram and notes on the local data files
├── notebooks/                  # Jupyter notebooks
├── web/                        # Static web frontend of the dashboard
│   ├── js/                     # ES modules, entry point main.js
│   └── build-vendor.sh         # Writes the frontend libraries into vendor/ (not in the repository)
├── src/
│   ├── space_weather_analysis/ # Shared core library
│   │   ├── catalog/, metadata/ # Master catalog and dataset metadata (metadata/spase/: SPASE records and dictionary)
│   │   ├── clients/            # Provider clients
│   │   ├── timeseries/, coverage/, jobs/
│   │   ├── solar/, heliosphere/, geospace/  # Physics: solar images and magnetic field, CME propagation, aurora
│   │   ├── dashboard/          # Dashboard configuration and panel documents (panels/: one module per panel type)
│   │   └── config/, database/, logging/, scales/, util/
│   ├── catalog_collector/      # Metadata worker (metadata_worker.py)
│   ├── pipeline_service/       # Pipeline service (main.py): feeds, jobs, pipelines per source type, parsing
│   ├── site_export/            # Writes and serves the dashboard documents (main.py)
│   └── streamlit_app/          # Streamlit explorer (main.py)
└── tests/                      # tests/unit and tests/integration
```

## Background

This project began as a Data Science coursework assignment (*Hausarbeit*, module DSCB-230-WS25/26) at [Hochschule Karlsruhe (HKA)](https://www.h-ka.de/) and is being developed further into a maintained, self-hosted service.

## Authors

- Patrick Heß
- Sebastian Straßner

## License

The source code of this project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details. Third-party data is not covered by this license; see [Data Sources and Licenses](#data-sources-and-licenses).
