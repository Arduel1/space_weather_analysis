from space_weather_analysis.clients.hek import parse_regions


def test_hek_region_entries_need_a_time_and_a_region_number() -> None:
    """Entries become rows at their day with the stored names; one without NOAA number is left out, empty classes are missing."""
    answer = {
        "result": [
            {
                "event_starttime": "2026-11-05T00:00:00",
                "ar_noaanum": 14566,
                "hgs_y": -6,
                "hgs_x": -40,
                "area_atdiskcenter": 365241056,
                "ar_mcintoshcls": "FAI",
                "ar_mtwilsoncls": "BETA-GAMMA-DELTA",
            },
            {
                "event_starttime": "2026-11-05T00:00:00",
                "ar_noaanum": None,
                "hgs_y": 10,
                "hgs_x": 5,
                "area_atdiskcenter": 1,
                "ar_mcintoshcls": "",
                "ar_mtwilsoncls": "",
            },
            {
                "event_starttime": "2026-11-06T00:00:00",
                "ar_noaanum": 14567,
                "hgs_y": 17,
                "hgs_x": 41,
                "area_atdiskcenter": 30436754,
                "ar_mcintoshcls": "",
                "ar_mtwilsoncls": "BETA",
            },
        ]
    }

    regions = parse_regions(answer)

    assert (len(regions), regions.iloc[0]["latitude"], regions.iloc[0]["mag_class"], regions.iloc[1]["spot_class"]) == (
        2,
        -6,
        "BETA-GAMMA-DELTA",
        None,
    )
