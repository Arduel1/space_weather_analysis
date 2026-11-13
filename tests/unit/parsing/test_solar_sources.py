import pathlib

import pandas as pd

from pipeline_service.parsing.silso import parse_silso_monthly_hemispheric
from pipeline_service.parsing.wso_polar import parse_wso_polar_fields


def test_hemispheric_sunspot_numbers_keep_both_hemispheres_with_placeholders_missing(tmp_path: pathlib.Path) -> None:
    """Each month holds the total and the number of each hemisphere; a -1 placeholder is missing in its own field only."""
    path = tmp_path / "SN_m_hem_V2.0.csv"
    path.write_text(
        "1992; 1;1992.042; 198.3; 67.0;131.3;  12.9;  7.7; 10.8;  566;  -1;  -1;1\n"
        "2026;10;2026.790;  45.7; 29.1; 16.6;  10.2;  6.3;  4.9; 1126; 949; 949;0\n",
        encoding="utf-8",
    )

    months = parse_silso_monthly_hemispheric(path)

    assert (
        months.index[1],
        months.iloc[1][["sunspot_number", "sunspot_number_north", "sunspot_number_south"]].tolist(),
        bool(months.iloc[1]["is_provisional"]),
        pd.isna(months.iloc[0]["num_observations_north"]),
    ) == (pd.Timestamp("2026-10-01", tz="UTC"), [45.7, 29.1, 16.6], True, True)


def test_wso_polar_field_page_yields_one_row_per_observation_line(tmp_path: pathlib.Path) -> None:
    """Observation lines become rows with the raw and filtered fields; other lines of the page are left out."""
    path = tmp_path / "Polar.html"
    path.write_text(
        "<pre>Last updated Mon Aug 10 17:27:49 UTC 2026\n"
        "1976:05:31_21h:07m:13s    89N -126S  108Avg   20nhz filt:  111Nf  -92Sf  101Avgf\n"
        "2026:07:20_21h:07m:13s   -45N   52S  -48Avg   20nhz filt:  -40Nf   47Sf  -44Avgf\n</pre>",
        encoding="latin-1",
    )

    fields = parse_wso_polar_fields(path)

    assert (list(fields.index), fields.iloc[1].tolist()) == (
        [pd.Timestamp("1976-05-31T21:07:13Z"), pd.Timestamp("2026-07-20T21:07:13Z")],
        [-45, 52, -48, -40, 47, -44],
    )
