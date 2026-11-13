import numpy as np

from space_weather_analysis.solar.image_analysis import difference_image


def test_a_difference_image_is_grey_where_nothing_changed_and_white_where_it_brightened() -> None:
    """A patch that brightens turns white, the unchanged rest stays mid grey, so that only the change stands out."""
    before = np.full((64, 64), 50, dtype=np.uint8)
    after = before.copy()
    after[24:40, 24:40] = 150

    change = difference_image(before, after)

    assert (change[32, 32], change[2, 2]) == (255, 128)
