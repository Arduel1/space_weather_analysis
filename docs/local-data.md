# Local Data Files

The `data/` directory holds the local data files the project reads at runtime. Apart from the HAPI server list, its contents are not part of the repository: the pipeline service downloads each file from its provider when it is missing or older than a day, the files are excluded via `.gitignore`, and they remain under the terms of their providers. The `curl` command under each file fetches it by hand. See [Data Sources and Licenses](../README.md#data-sources-and-licenses) in the main README for all providers and the acknowledgements they request.

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

## data/SN_m_hem_V2.0.csv

- Title: Monthly mean sunspot number of the northern and southern hemisphere, version 2.0
- Creator: WDC-SILSO, Royal Observatory of Belgium, Brussels
- Download: <https://www.sidc.be/SILSO/INFO/snmhemcsv.php>
- Reference: Clette, F. and Lefèvre, L. (2015). SILSO Sunspot Number V2.0. WDC-SILSO, Royal Observatory of Belgium. <https://doi.org/10.24414/qnza-ac80>
- License: [Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)](https://creativecommons.org/licenses/by-nc/4.0/). Commercial use is not permitted.
- Notes: monthly values since 1992; each sunspot group counts in the hemisphere of its latitude, and the hemispheric numbers are normalized to the total monthly mean.
- Attribution: Source: WDC-SILSO, Royal Observatory of Belgium, Brussels, <https://doi.org/10.24414/qnza-ac80>

```bash
curl -o data/SN_m_hem_V2.0.csv https://www.sidc.be/SILSO/INFO/snmhemcsv.php
```

## data/wso_polar.html

- Title: Wilcox Solar Observatory polar field observations, 1976 to present
- Creator: Wilcox Solar Observatory, Stanford University
- Download: <http://wso.stanford.edu/Polar.html>
- License: no terms stated.
- Notes: the page lists the north, south and average polar field every 10 days, each the mean of a centred 30-day window, and the same values low pass filtered at 20 nHz to remove the yearly projection effect.
- Attribution: Wilcox Solar Observatory data used in this study were obtained via the web site <http://wso.stanford.edu>, courtesy of J.T. Hoeksema.

```bash
curl -o data/wso_polar.html http://wso.stanford.edu/Polar.html
```

## data/icme_list.html

- Title: Near-Earth interplanetary coronal mass ejections since 1996 (Richardson and Cane)
- Creator: Ian Richardson and Hilary Cane (NASA GSFC, University of Tasmania)
- Download: <https://izw1.caltech.edu/ACE/ASC/DATA/level3/icmetable2.htm>
- License: no terms stated; the list asks to include its DOI 10.7910/DVN/C2MHTH.
- Notes: one row per ICME with its start and end, the Dst and the associated LASCO CMEs.
- Acknowledgement: Richardson and Cane list of near-Earth ICMEs, doi:10.7910/DVN/C2MHTH; Cane and Richardson (2003), doi:10.1029/2002JA009817; Richardson and Cane (2010), doi:10.1007/s11207-010-9568-6.
- References:
  - Cane and Richardson (2003), method of the list. <https://doi.org/10.1029/2002JA009817>
  - Richardson and Cane (2010), catalog for solar cycle 23. <https://doi.org/10.1007/s11207-010-9568-6>

```bash
curl -o data/icme_list.html https://izw1.caltech.edu/ACE/ASC/DATA/level3/icmetable2.htm
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
