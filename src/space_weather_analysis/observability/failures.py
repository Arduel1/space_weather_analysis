import logging
import threading


class RepeatedFailures:
    """Logs an expected failure as warning only when it starts or changes its cause, and the next success at info level.

    Repeats of a failure go to debug level. The state lives in the process, so after a restart the next failure is logged
    as warning again. Safe to share between threads.
    """

    def __init__(self, logger: logging.Logger) -> None:
        """Creates the log of repeated failures.

        Args:
            logger: The logger the messages go to.
        """
        self.logger = logger
        # The cause of the running failure and the number of failures since the last success, by subject
        self._failures: dict[str, tuple[str, int]] = {}
        self._lock = threading.Lock()

    def failed(self, subject: str, cause: str, message: str, *args: object) -> None:
        """Logs a failure of a subject, as warning unless the subject failed with the same cause before.

        Args:
            subject: What failed, such as a dataset ID.
            cause: The kind of the failure, such as the name of the exception class.
            message: The log message, with %-style placeholders for args.
            args: The values of the placeholders.
        """
        with self._lock:
            previous_cause, failure_count = self._failures.get(subject, ("", 0))
            self._failures[subject] = (cause, failure_count + 1)
        if previous_cause == cause:
            self.logger.debug("%s (failure %d since the last success)", message % args, failure_count + 1)
        else:
            self.logger.warning(message, *args)

    def succeeded(self, subject: str) -> None:
        """Logs at info level that a subject works again, if it failed before.

        Args:
            subject: What succeeded, such as a dataset ID.
        """
        with self._lock:
            failure = self._failures.pop(subject, None)
        if failure is not None:
            self.logger.info("%s works again after %d failures", subject, failure[1])
