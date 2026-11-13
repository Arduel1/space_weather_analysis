import logging
import re
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd

from space_weather_analysis.util.error_handling import DataProcessingError

logger = logging.getLogger(__name__)

# Identifies the list of near-Earth interplanetary coronal mass ejections (ICMEs) since 1996 compiled by Richardson and
# Cane, the clouds of CMEs that reached Earth, each with the LASCO CMEs it is associated with
ICME_PROVIDER_ID = "RICHARDSON_CANE_ICME"

# Fewest cells of a data row: disturbance time, ICME start and end, Dst, transit speeds and associated CMEs
_MIN_DATA_CELLS = 6
# A time of the list such as `2024/05/10 2000`: groups 1 to 3 year, month and day separated by slashes, one or more
# spaces or line breaks (\s+), then groups 4 and 5 hour and minute as four digits
_TIME = re.compile(r"(\d{4})/(\d{2})/(\d{2})\s+(\d{2})(\d{2})")
# One associated LASCO CME in the last column, such as `2024/05/08 0536 HD` or `1224 HD`: an optional date as groups
# 1 to 3 with the spaces after it (the non-capturing (?:...)?), then hour and minute as groups 4 and 5, which \b ends at
# a word boundary so that no longer number matches; a time without date belongs to the date before it
_CME = re.compile(r"(?:(\d{4})/(\d{2})/(\d{2})\s+)?(\d{2})(\d{2})\b")
# The Dst minimum during the ICME at the start of its cell, such as `-406 P` (provisional) or `-89`: an optional minus
# and the digits as group
_DST = re.compile(r"(-?\d+)")
# Separates the associated CME times stored as one text
CME_SEPARATOR = " "


class _TableRows(HTMLParser):
    """Collects the plain text of the cells of every table row of a page, one list of cell texts per row."""

    def __init__(self) -> None:
        """Initializes the parser with no rows; character references such as &amp; are turned into their characters."""
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Starts a row at <tr> and a cell at <td> or <th>; any other tag inside a cell separates words like a space."""
        del attrs
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        """Ends the open cell at </td> or </th> and the open row at </tr>; other closing tags inside a cell act as a space."""
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            # Whitespace runs of the source collapse into single spaces
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        """Adds text to the open cell; text outside a cell is not kept."""
        if self._cell is not None:
            self._cell.append(data)


def _table_rows(page: str) -> list[list[str]]:
    """Returns the plain text of each cell of each table row of an HTML page."""
    parser = _TableRows()
    parser.feed(page)
    parser.close()
    return parser.rows


def _time(text: str) -> pd.Timestamp | None:
    """Reads the first time of a cell; None without one."""
    match = _TIME.search(text)
    if match is None:
        return None
    return pd.Timestamp(f"{match[1]}-{match[2]}-{match[3]}T{match[4]}:{match[5]}", tz="UTC")


def associated_cmes(text: str) -> list[str]:
    """Reads the times of the LASCO CMEs associated with an ICME, each as ISO 8601 text to the minute.

    Args:
        text: The text of the last column, such as `2024/05/07 0312 D, 0524 D, 2024/05/08 0536 HD`.

    Returns:
        The times in the order of the list; empty without a dated CME.
    """
    times = []
    date: tuple[str, str, str] | None = None
    for match in _CME.finditer(text):
        if match[1] is not None:
            date = (match[1], match[2], match[3])
        if date is None:
            continue
        times.append(f"{date[0]}-{date[1]}-{date[2]}T{match[4]}:{match[5]}")
    return times


def parse_icme_list(path: Path) -> pd.DataFrame:
    """Reads the saved HTML table of the Richardson and Cane ICME list.

    A row without readable ICME start time is left out.

    Args:
        path: The saved page.

    Returns:
        One row per ICME at its start with a UTC DatetimeIndex and the columns icme_end (ISO 8601 text), dst (the
        lowest Dst in nT, NaN if not given) and cme_times (the associated LASCO CME times, ISO 8601 to the minute,
        separated by CME_SEPARATOR; empty if none is given).

    Raises:
        FileNotFoundError: If the file does not exist.
        DataProcessingError: If the page holds no ICME.
    """
    if not path.exists():
        raise FileNotFoundError(f"ICME list not found at {path}")
    rows = []
    times = []
    for cells in _table_rows(path.read_text(encoding="latin-1")):
        # Data rows start with the disturbance time and end with the Dst, the transit speeds and the associated CMEs
        if len(cells) < _MIN_DATA_CELLS or _time(cells[0]) is None:
            continue
        start = _time(cells[1])
        if start is None:
            continue
        end = _time(cells[2])
        dst = _DST.match(cells[-3])
        times.append(start)
        rows.append(
            {
                "icme_end": end.isoformat() if end is not None else None,
                "dst": float(dst[1]) if dst is not None else float("nan"),
                "cme_times": CME_SEPARATOR.join(associated_cmes(cells[-1])),
            }
        )
    if not rows:
        raise DataProcessingError(f"{path} holds no ICME.")
    frame = pd.DataFrame(rows, index=pd.DatetimeIndex(times))
    logger.info("Extracted %d ICMEs from %s.", len(frame), path)
    return frame[~frame.index.duplicated()].sort_index()
