# Local Data Files

The `data/` directory holds the local data files the project reads at runtime. Apart from the HAPI server list, its contents are not part of the repository: the files are downloaded from their providers, are excluded via `.gitignore`, and remain under the terms of their providers. See [Data Sources and Licenses](../README.md#data-sources-and-licenses) in the main README for all providers and the acknowledgements they request.

## data/SN_d_tot_V2.0.csv

- Title: Daily total sunspot number, version 2.0
- Creator: WDC-SILSO, Royal Observatory of Belgium, Brussels
- Download: <https://www.sidc.be/SILSO/INFO/sndtotcsv.php>
- Reference: Clette, F. and Lefèvre, L. (2015). SILSO Sunspot Number V2.0. WDC-SILSO, Royal Observatory of Belgium. <https://doi.org/10.24414/qnza-ac80>
- License: [Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)](https://creativecommons.org/licenses/by-nc/4.0/). Commercial use is not permitted.
- Notes: values from the most recent months are provisional and are revised by SILSO over time.
- Attribution: Source: WDC-SILSO, Royal Observatory of Belgium, Brussels, <https://doi.org/10.24414/qnza-ac80>

```bash
curl -o data/SN_d_tot_V2.0.csv https://www.sidc.be/SILSO/INFO/sndtotcsv.php
```

## data/SN_m_tot_V2.0.csv

- Title: Monthly mean total sunspot number, version 2.0
- Creator: WDC-SILSO, Royal Observatory of Belgium, Brussels
- Download: <https://www.sidc.be/SILSO/INFO/snmtotcsv.php>
- Reference: Clette, F. and Lefèvre, L. (2015). SILSO Sunspot Number V2.0. WDC-SILSO, Royal Observatory of Belgium. <https://doi.org/10.24414/qnza-ac80>
- License: [Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)](https://creativecommons.org/licenses/by-nc/4.0/). Commercial use is not permitted.
- Notes: values from the most recent months are provisional and are revised by SILSO over time.
- Attribution: Source: WDC-SILSO, Royal Observatory of Belgium, Brussels, <https://doi.org/10.24414/qnza-ac80>

```bash
curl -o data/SN_m_tot_V2.0.csv https://www.sidc.be/SILSO/INFO/snmtotcsv.php
```

## data/SN_ms_tot_V2.0.csv

- Title: 13-month smoothed monthly total sunspot number, version 2.0
- Creator: WDC-SILSO, Royal Observatory of Belgium, Brussels
- Download: <https://www.sidc.be/SILSO/INFO/snmstotcsv.php>
- Reference: Clette, F. and Lefèvre, L. (2015). SILSO Sunspot Number V2.0. WDC-SILSO, Royal Observatory of Belgium. <https://doi.org/10.24414/qnza-ac80>
- License: [Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)](https://creativecommons.org/licenses/by-nc/4.0/). Commercial use is not permitted.
- Notes: the first and last six months of the series have no smoothed value.
- Attribution: Source: WDC-SILSO, Royal Observatory of Belgium, Brussels, <https://doi.org/10.24414/qnza-ac80>

```bash
curl -o data/SN_ms_tot_V2.0.csv https://www.sidc.be/SILSO/INFO/snmstotcsv.php
```

## data/cme_catalog.txt

- Title: SOHO/LASCO CME Catalog, text version (`univ_all.txt`)
- Creator: CDAW Data Center, NASA and The Catholic University of America, in cooperation with the Naval Research Laboratory
- Download: <https://cdaw.gsfc.nasa.gov/CME_list/UNIVERSAL_ver2/text_ver/univ_all.txt>
- License: the catalog publishes no formal license; it requests the acknowledgement below.
- Acknowledgement: "This CME catalog is generated and maintained at the CDAW Data Center by NASA and The Catholic University of America in cooperation with the Naval Research Laboratory. SOHO is a project of international cooperation between ESA and NASA."
- References:
  - Yashiro, S., et al. (2004). A catalog of white light coronal mass ejections observed by the SOHO spacecraft. Journal of Geophysical Research, 109, A07105. <https://doi.org/10.1029/2003JA010282>
  - Gopalswamy, N., et al. (2009). The SOHO/LASCO CME Catalog. Earth, Moon, and Planets, 104, 295-313. <https://doi.org/10.1007/s11038-008-9282-7>

```bash
curl -o data/cme_catalog.txt https://cdaw.gsfc.nasa.gov/CME_list/UNIVERSAL_ver2/text_ver/univ_all.txt
```

## data/hapi_config/all.txt

A list of HAPI server URLs created for this project and read by the metadata worker. It contains no third-party data and is the only file in `data/` that is part of the repository, under its MIT License.
