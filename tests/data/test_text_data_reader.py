"""Test the text data reader"""

import numpy as np
import pytest
import tensorflow as tf

from src.data.text_data_reader import Set, TextDataReader


def test_initialization():
    dr = TextDataReader()
    assert dr.name == "text"
    assert dr.folder == "data/train/text"
    for set_type in [Set.TRAIN, Set.VAL, Set.TEST]:
        assert dr.file_map[set_type]


def test_reading():
    dr = TextDataReader(folder="tests/test_data")
    assert dr.folder == "tests/test_data"
    dr.file_map[Set.TRAIN] = "text_test.csv"
    dataset = dr.get_emotion_data("neutral_ekman", Set.TRAIN, batch_size=5)
    assert isinstance(dataset, tf.data.Dataset)
    batch = 0
    for texts, labels in dataset:
        batch += 1
        assert texts.numpy().shape == (5, 1)
        assert labels.numpy().shape == (5, 7)
        for text in texts.numpy():
            text = str(text)
            assert len(text) > 5
        for label in labels.numpy():
            assert label.shape == (7,)
            assert np.sum(label) == 1
    assert batch == 6

    with pytest.raises(ValueError):
        _ = dr.get_emotion_data("wrong")


def test_reading_three():
    dr = TextDataReader(folder="tests/test_data")
    assert dr.folder == "tests/test_data"
    dr.file_map[Set.TRAIN] = "text_test.csv"
    dataset = dr.get_emotion_data("three", Set.TRAIN, batch_size=4)
    assert isinstance(dataset, tf.data.Dataset)
    batch = 0
    for texts, labels in dataset:
        batch += 1
        if batch <= 7:
            assert texts.numpy().shape == (4, 1)
            assert labels.numpy().shape == (4, 3)
            for text in texts.numpy():
                text = str(text)
                assert len(text) > 5
            for label in labels.numpy():
                assert label.shape == (3,)
                assert np.sum(label) == 1
        elif batch == 8:
            assert texts.numpy().shape == (2, 1)
            assert labels.numpy().shape == (2, 3)
            for text in texts.numpy():
                text = str(text)
                assert len(text) > 5
            for label in labels.numpy():
                assert label.shape == (3,)
                assert np.sum(label) == 1
    assert batch == 8
