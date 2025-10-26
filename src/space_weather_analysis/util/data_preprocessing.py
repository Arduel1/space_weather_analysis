class DataPreprocessor:
    """
    A utility class for common data preprocessing tasks, such as batching.
    """

    def __init__(self, batch_size=10000) -> None:
        """
        Initializes the DataPreprocessor.

        Args:
            batch_size (int, optional): The default number of items
                                        to include in each batch.
                                        Defaults to 10000.
        """
        self.batch_size = batch_size

    def batch_generator(self, data_list):
        """
        A generator function that yields data in batches.

        This is used to split a large list of documents before a bulk
        database insertion to avoid timeouts.

        Args:
            data_list (list): The complete list of items to be batched.

        Yields:
            list: A sub-list (batch) of the original data_list of
                  size 'self.batch_size'.
        """
        for i in range(0, len(data_list), self.batch_size):
            yield data_list[i:i + self.batch_size]