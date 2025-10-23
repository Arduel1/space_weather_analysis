class DataPreprocessor:
    def __init__(self, batch_size=10000) -> None:
        self.batch_size = batch_size
        pass

    def batch_generator(self, data_list):
        for i in range(0, len(data_list), self.batch_size):
            yield data_list[i:i + self.batch_size]