from space_weather_analysis.util.error_handling import DataRetrievalError, SpaceWeatherError


def test_retrieval_error_is_a_project_error_carrying_the_requested_wait() -> None:
    """A DataRetrievalError is caught as SpaceWeatherError and keeps the wait the provider asked for."""
    error = DataRetrievalError("Server overloaded", retry_after_seconds=30.0)

    assert (isinstance(error, SpaceWeatherError), str(error), error.retry_after_seconds) == (True, "Server overloaded", 30.0)
