""" This data reader reads the Happimeter data from the experiments. """
import copy
import glob
import json
import os
import warnings
from typing import Dict, Generator, List, Tuple

import numpy as np
import pandas as pd
import tensorflow as tf

from src.data.data_reader import Set
from src.data.experiment_data_reader import ExperimentDataReader
from src.utils import reader_main
from src.utils.ground_truth import experiment_ground_truth


class WatchExperimentDataReader(ExperimentDataReader):
    """
    This data reader reads the watch csv files from the experiments
    """

    def __init__(
        self,
        folder: str = os.path.join("data", "watch"),
        default_label_mode: str = "expected",
    ) -> None:
        """
        Initialize the watch data reader for the experiment data.

        :param folder: The folder that contains the watch files
        :param default_label_mode: Whether to use expected emotion
            or face as ground truth.
        """
        super().__init__("watch", folder or os.path.join("data", "watch"))
        self.default_label_mode = default_label_mode
        assert default_label_mode in ["expected", "faceapi", "both"]
        if (
            default_label_mode == "faceapi"
            and len(glob.glob(os.path.join("data", "ground_truth", "*.json")))
            != 54
        ):
            self.prepare_faceapi_labels()  # pragma: no cover
        self.raw_data = None
        self.raw_labels = None

    def get_seven_emotion_data(
        self, which_set: Set, batch_size: int = 64, parameters: Dict = None
    ) -> tf.data.Dataset:
        """
        Method that returns a dataset of watch data.

        :param which_set: Which set to use.
        :param batch_size: Batch size for the dataset.
        :param parameters: Additional parameters.
        :return: Dataset instance.
        """
        parameters = parameters or {}
        self.get_raw_data(parameters)
        dataset = tf.data.Dataset.from_generator(
            self.get_data_generator(which_set, parameters),
            output_types=(tf.float32, tf.float32),
            output_shapes=(
                tf.TensorShape([*self.get_input_shape(parameters)]),
                tf.TensorShape([7]),
            ),
        )
        if parameters.get(
            "shuffle", True if which_set == Set.TRAIN else False
        ):
            dataset = dataset.shuffle(1024)
        dataset = dataset.batch(batch_size)
        return dataset

    def get_data_generator(
        self, which_set: Set, parameters: Dict
    ) -> Generator[Tuple[np.ndarray, np.ndarray], None, None]:
        """
        Generator that generates the data

        :param which_set: Train, val or test set
        :param parameters: Additional parameters including:
            - window: The length of the window to use in seconds
        :return: Generator that yields data and label.
        """

        def generator():
            indices = self.get_cross_validation_indices(which_set, parameters)
            for data_index in indices:
                data = self.raw_data[data_index]
                label = self.raw_labels[data_index]
                yield (
                    data,
                    tf.keras.utils.to_categorical(
                        np.array(label), num_classes=7
                    ),
                )

        return generator

    def get_cross_validation_indices(
        self, which_set: Set, parameters: Dict
    ) -> List[int]:
        """
        Generate a list of indices according to CrossValidation.

        :param which_set: Which set to use.
        :param parameters: Additional parameters including:
            - cv_portions: Number of cv splits to do.
            - cv_index: Which split to use.
        :return: List of indexes in a cv form.
        """
        cv_portions = parameters.get("cv_splits", 5)
        if which_set == Set.ALL:
            indices = []
            cv_params = copy.deepcopy(parameters)
            for cv_index in range(cv_portions):
                cv_params["cv_index"] = cv_index
                indices.extend(
                    self.get_cross_validation_indices(Set.TEST, cv_params)
                )
            return indices
        cv_index = parameters.get("cv_index", 0)
        assert cv_portions - 1 >= cv_index >= 0
        all_indices = []
        for emotion_index in range(7):
            emotion_samples = np.where(self.raw_labels == emotion_index)[0]
            borders = np.linspace(
                0, emotion_samples.shape[0], cv_portions + 1
            ).astype(int)
            if which_set == Set.TEST:
                test_split = cv_portions - cv_index
                all_indices.extend(
                    list(
                        emotion_samples[
                            borders[test_split - 1] : borders[test_split]
                        ]
                    )
                )
            elif which_set == Set.VAL:
                val_split = (cv_portions - 1 - cv_index) % cv_portions
                val_split = val_split - 1 if val_split == 0 else val_split
                all_indices.extend(
                    list(
                        emotion_samples[
                            borders[val_split - 1] : borders[val_split]
                        ]
                    )
                )
            elif which_set == Set.TRAIN:
                for i in range(1, cv_portions - 1):
                    train_split = (i - cv_index) % cv_portions
                    train_split = (
                        train_split - 1 if train_split == 0 else train_split
                    )
                    all_indices.extend(
                        list(
                            emotion_samples[
                                borders[train_split - 1] : borders[train_split]
                            ]
                        )
                    )
        all_indices.sort()
        return all_indices

    def get_three_emotion_data(
        self, which_set: Set, batch_size: int = 64, parameters: Dict = None
    ) -> tf.data.Dataset:
        """
        Create a dataset that uses only three emotions.

        :param which_set: Which set: Train, val or test
        :param batch_size: Batch size
        :param parameters: Additional parameters
        :return: Dataset with three emotion labels.
        """
        dataset = self.get_seven_emotion_data(
            which_set, batch_size, parameters
        )
        dataset = dataset.map(
            lambda x, y: tf.numpy_function(
                func=self.map_emotions,
                inp=[x, y],
                Tout=(tf.float32, tf.float32),
            )
        )
        return dataset

    def get_labels(
        self, which_set: Set = Set.TRAIN, parameters: Dict = None
    ) -> np.ndarray:
        """
        This function returns labels for the dataset

        :param which_set: Which set to get the labels for.
        :param parameters: Additional parameters.
        :return: Label numpy array
        """
        parameters = parameters or {}
        parameters["shuffle"] = False
        dataset = self.get_seven_emotion_data(which_set, 100, parameters)
        labels = np.empty((0,))
        for _, batch_labels in dataset:
            labels = np.concatenate(
                [labels, np.argmax(batch_labels, axis=1)], axis=0
            )
        return labels

    def get_raw_labels(self, label_mode: str) -> np.ndarray:
        """
        Get the raw labels per experiment and time.
        Populates the raw_labels member of this class.
        The two axis are [experiment_index, time_in_seconds]

        :param label_mode: Whether to use expected or faceapi labels
        :return: Array of all labels in shape (file, second)
        """
        raw_labels = np.zeros((len(self.get_complete_data_indices()), 690))
        if label_mode == "expected":
            raw_labels = self.get_raw_expected_labels()
        elif label_mode == "faceapi":
            raw_labels = self.get_raw_faceapi_labels()
        elif label_mode == "both":
            expected = self.get_raw_expected_labels()
            faceapi = self.get_raw_faceapi_labels()
            expected[expected != faceapi] = -1
            raw_labels = expected
        return raw_labels[:, :614]

    def get_raw_expected_labels(self) -> np.ndarray:
        """
        Load the raw emotions from the expected emotions during the video.
        The expected emotion means that while the participant is watching a
        happy video, we expect them to be happy, thus the label is happy.

        :return: Labels that are expected from the user.
        """
        labels = np.zeros((len(self.get_complete_data_indices()), 690))
        for emotion, times in self.emotion_times.items():
            start_time = (
                0 if int(times["start"]) == 0 else int(times["start"]) + 1
            )
            labels[:, start_time : int(times["end"])] = self.emotion_labels[
                emotion
            ]
        return labels

    def get_raw_faceapi_labels(self) -> np.ndarray:
        """
        Load the raw labels from the faceapi output files.

        :return: Labels that are collected from the user's face expression.
        """
        data_indices = self.get_complete_data_indices()
        gt_folder = os.path.join("data", "ground_truth")
        labels = np.zeros((len(data_indices), 690))
        emotions_sorted = [
            "angry",
            "surprised",
            "disgusted",
            "happy",
            "fearful",
            "sad",
            "neutral",
        ]
        if self.folder.startswith("tests"):  # Testing
            gt_folder = os.path.join("tests", "test_data", "ground_truth")
            data_indices = [5]
        for file_index, experiment_index in enumerate(data_indices):
            ground_truth_file = glob.glob(
                f"{gt_folder}{os.sep}{experiment_index:03d}*.json"
            )[0]
            with open(ground_truth_file, "r") as emotions_file:
                raw_emotions = json.load(emotions_file)
            previous = None
            for time, emotion_probs in raw_emotions:
                time_index = int(float(time)) - 1
                if emotion_probs != ["undefined"]:
                    emotion_probs_sorted = [
                        emotion_probs[0][emotion]
                        for emotion in emotions_sorted
                    ]
                    label = np.argmax(emotion_probs_sorted)
                    previous = label
                else:
                    label = previous
                labels[file_index, time_index] = label
        return labels

    @staticmethod
    def prepare_faceapi_labels() -> None:  # pragma: no cover
        """
        This function prepares the faceapi labels if they are not computed yet.
        """
        video_files = glob.glob("data/video/*.mp4")
        video_files.sort()
        for file in video_files:
            emotions_file = os.path.join(
                "data",
                "ground_truth",
                f"{os.path.basename(file).split('.')[0]}_emotions.json",
            )
            if not os.path.exists(emotions_file):
                experiment_ground_truth(file)

    def get_raw_data(self, parameters: Dict) -> None:
        """
        Load the raw watch data from the csv files and split it into
        windows according to the parameters.

        :param parameters: Additional parameters
        """
        window = parameters.get("window", 20)
        normalize = parameters.get("normalize", True)
        hop = parameters.get("hop", 5)
        all_labels = self.get_raw_labels(
            parameters.get("label_mode", self.default_label_mode)
        )
        columns = [
            "Heartrate",
            "AccelerometerX",
            "AccelerometerY",
            "AccelerometerZ",
            "Accelerometer",
        ]
        if normalize:
            columns = [f"{col}Norm" for col in columns.copy()]
        raw_data = np.empty((0, window, 5))
        raw_labels = np.empty((0,))
        for part_id in self.get_complete_data_indices():
            part_data = np.empty((0, window, 5))
            part_labels = np.empty((0,))
            for emotion in self.emotions:
                file = glob.glob(
                    f"{os.path.join(self.folder, emotion)}/{part_id:03d}*.csv"
                )
                if not len(file):
                    warnings.warn(
                        f"Happimeter data file for participant {part_id} "
                        f"and emotion {emotion} not found!"
                    )
                    continue
                data = pd.read_csv(file[0], delimiter=",", usecols=columns)
                seconds = pd.read_csv(
                    file[0], delimiter=",", usecols=["Second"]
                )
                for second in range(window, len(data), hop):
                    index = self.get_complete_data_indices().index(part_id)
                    label = all_labels[index, seconds.values[second][0]]
                    sample = np.expand_dims(
                        data.values[(second - window) : second, :], axis=0
                    )
                    part_data = np.concatenate([part_data, sample], axis=0)
                    part_labels = np.concatenate(
                        [part_labels, np.array([label])], axis=0
                    )
            # Normalize
            if part_data.size:
                raw_data = np.concatenate(
                    [raw_data, part_data[part_labels != -1]], axis=0
                )
                raw_labels = np.concatenate(
                    [raw_labels, part_labels[part_labels != -1]], axis=0
                )
        self.raw_data = raw_data
        self.raw_labels = raw_labels

    @staticmethod
    def get_input_shape(parameters: Dict) -> tuple:
        """
        Returns the shape of a preprocessed sample.

        :param parameters: Parameter dictionary
        :return: Tuple that is the shape of the sample.
        """
        parameters = parameters or {}
        window = parameters.get("window", 20)
        return window, 5


def _main():  # pragma: no cover
    reader = WatchExperimentDataReader()
    parameters = {
        "label_mode": "both",
        "cv_portions": 5,
        "window": 20,
        "hop": 3,
    }
    for split in range(5):
        print(f"Split {split}/5")
        parameters["cv_index"] = split
        reader_main(reader, parameters)


if __name__ == "__main__":  # pragma: no cover
    _main()
