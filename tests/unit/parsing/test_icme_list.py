from pathlib import Path

import pandas as pd

from pipeline_service.parsing.icme_list import associated_cmes, parse_icme_list

# Two rows in the layout of the list: disturbance, ICME start and end, ten middle columns, Dst, transit speeds and the
# associated LASCO CMEs; the header row and a row without ICME start are skipped
TABLE = (
    """<table>
<tr><th>Disturbance</th><th>ICME start</th><th>end</th></tr>
<tr><td>2024/05/10 1705</td><td>2024/05/10 2000</td><td>2024/05/12 1500</td>
"""
    + "<td>...</td>" * 10
    + """<td>-406 P</td><td>480-1830</td><td>2024/05/08 0536 HD, 1224 HD, 2024/05/09 0924 HD</td></tr>
<tr><td>2024/06/01 0000</td><td>...</td><td>...</td>"""
    + "<td>...</td>" * 10
    + """<td>...</td><td>...</td><td>...</td></tr>
<tr><td>2024/06/02 0100</td><td>2024/06/02 0300</td><td>2024/06/03 0000</td>"""
    + "<td>...</td>" * 10
    + """<td>...</td><td>...</td><td>...</td></tr>
</table>"""
)


def test_associated_cmes_carry_their_date_to_the_times_after_it() -> None:
    """A time without date belongs to the date written before it."""
    assert associated_cmes("2024/05/08 0536 HD, 1224 HD, 2024/05/09 0924 HD") == ["2024-05-08T05:36", "2024-05-08T12:24", "2024-05-09T09:24"]


def test_icme_rows_keep_their_start_dst_and_cmes(tmp_path: Path) -> None:
    """Each ICME is placed at its start with its Dst and its CMEs; a row without start is left out, missing values stay missing."""
    page = tmp_path / "icme.html"
    page.write_text(TABLE, encoding="latin-1")

    frame = parse_icme_list(page)

    assert (list(frame.index), frame["dst"].tolist()[0], frame["cme_times"].tolist()) == (
        list(pd.DatetimeIndex(["2024-05-10T20:00Z", "2024-06-02T03:00Z"])),
        -406.0,
        ["2024-05-08T05:36 2024-05-08T12:24 2024-05-09T09:24", ""],
    )
