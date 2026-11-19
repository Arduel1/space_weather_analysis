import os
import signal

import pytest

from space_weather_analysis.observability.process import stop_on_termination


def test_sigterm_ends_the_main_thread_like_ctrl_c() -> None:
    """After stop_on_termination, SIGTERM raises KeyboardInterrupt in the main thread instead of ending the process."""
    previous_handler = signal.getsignal(signal.SIGTERM)
    try:
        stop_on_termination()
        with pytest.raises(KeyboardInterrupt):
            os.kill(os.getpid(), signal.SIGTERM)
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
