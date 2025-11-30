import pytest

from space_weather_analysis.util.error_handling import (
    CatalogError,
    DataProcessingError,
    DataRetrievalError,
    SpaceWeatherError,
)


class TestErrorClasses:
    def test_inheritance_hierarchy(self) -> None:
        """
        Ensure all custom exceptions inherit from the base SpaceWeatherError.
        This guarantees that 'except SpaceWeatherError' catches them all.
        """
        assert issubclass(DataRetrievalError, SpaceWeatherError)
        assert issubclass(DataProcessingError, SpaceWeatherError)
        assert issubclass(CatalogError, SpaceWeatherError)
        assert issubclass(SpaceWeatherError, Exception)

    def test_catch_base_exception(self) -> None:
        """
        Verify that raising a specific error is caught by the base class block.
        """
        try:
            raise DataRetrievalError("Network down")
        except SpaceWeatherError as e:
            assert str(e) == "Network down"
            assert isinstance(e, DataRetrievalError)
        except Exception:
            pytest.fail("Should have been caught by SpaceWeatherError")

    def test_distinct_exceptions(self) -> None:
        """
        Verify that specific exceptions can be caught individually.
        """
        try:
            raise CatalogError("Invalid JSON")
        except DataRetrievalError:
            pytest.fail("Should not catch CatalogError in DataRetrievalError block")
        except CatalogError as e:
            assert str(e) == "Invalid JSON"
