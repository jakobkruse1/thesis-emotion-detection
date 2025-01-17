"""Implementation of an emotion classifier using BERT"""

import os
from typing import Dict

import numpy as np
import tensorflow as tf
import tensorflow_hub as hub
import tensorflow_text as text  # noqa: F401
from official.nlp import optimization

from src.classification.text.text_emotion_classifier import (
    TextEmotionClassifier,
)
from src.data.data_reader import Set
from src.utils import logging, training_loop


class BertClassifier(TextEmotionClassifier):
    """
    Emotion classifier based on the BERT model
    """

    def __init__(self, parameters: Dict = None):
        """
        Initialize the emotion classifier.

        :param parameters: Configuration parameters dictionary
            model_name: tf hub model name
        """
        super().__init__("bert", parameters)
        tf.get_logger().setLevel("ERROR")
        parameters = parameters or {}
        self.model_name = parameters.get(
            "model_name", "bert_en_uncased_L-6_H-256_A-4"
        )
        self.model_path = (
            f"https://tfhub.dev/tensorflow/small_bert/{self.model_name}/2"
        )
        self.preprocess_path = (
            "https://tfhub.dev/tensorflow/bert_en_uncased_preprocess/3"
        )
        self.logger = logging.KerasLogger()
        self.logger.log_start(
            {
                "init_parameters": parameters,
                "model_name": self.model_name,
            }
        )
        self.classifier = None

    def _build_classifier_model(self, parameters: Dict) -> tf.keras.Model:
        """
        Define the tensorflow model that uses BERT for classification

        :param parameters: Parameters dictionary
            dropout_rate: Dropout rate
            dense_layer: 0 means no additional layer, otherwise this is the
                number of neurons in an additional dense layer
        :return: The keras model that classifies the text
        """
        dropout_rate = parameters.get("dropout_rate", 0.1)
        dense_layer = parameters.get("dense_layer", 0)
        input_tensor = tf.keras.layers.Input(
            shape=(), dtype=tf.string, name="text"
        )
        preprocessor = hub.KerasLayer(
            self.preprocess_path, name="preprocessing"
        )
        encoder_inputs = preprocessor(input_tensor)
        encoder = hub.KerasLayer(
            self.model_path, trainable=True, name="BERT_encoder"
        )
        outputs = encoder(encoder_inputs)
        net = outputs["pooled_output"]
        net = tf.keras.layers.Dropout(dropout_rate)(net)
        if dense_layer:
            net = tf.keras.layers.Dense(dense_layer, activation="relu")(net)
        net = tf.keras.layers.Dense(
            7, activation="softmax", name="classifier"
        )(net)
        return tf.keras.Model(input_tensor, net)

    def train(self, parameters: Dict = None, **kwargs) -> None:
        """
        Training method for the BERT classifier

        :param parameters: Training parameters
            init_lr: initial learning rate
            epochs: Number of epochs to train for
            which_set: Which dataset to use for training
            batch_size: The batch size for training
        :param kwargs: Additional parameters
            Not used currently
        """
        parameters = self.init_parameters(parameters, **kwargs)
        self.logger.log_start({"train_parameters": parameters})
        init_lr = parameters.get("init_lr", 1e-5)
        epochs = parameters.get("epochs", 100)
        which_set = parameters.get("which_set", Set.TRAIN)
        batch_size = parameters.get("batch_size", 64)
        patience = parameters.get("patience", 5)

        num_samples = self.data_reader.get_labels(which_set).shape[0]
        num_train_steps = int(num_samples * epochs / batch_size)
        self.classifier = self._build_classifier_model(parameters)
        loss = tf.keras.losses.CategoricalCrossentropy()
        metrics = [tf.metrics.CategoricalAccuracy()]
        optimizer = optimization.create_optimizer(
            init_lr=init_lr,
            num_train_steps=num_train_steps,
            num_warmup_steps=0.1 * num_train_steps,
            optimizer_type="adamw",
        )
        callback = tf.keras.callbacks.EarlyStopping(
            monitor="val_loss", patience=patience, restore_best_weights=True
        )
        self.classifier.compile(
            optimizer=optimizer, loss=loss, metrics=metrics
        )

        train_data = self.data_reader.get_emotion_data(
            self.emotions, which_set, batch_size, parameters
        )
        val_data = self.data_reader.get_emotion_data(
            self.emotions, Set.VAL, batch_size
        )

        history = self.classifier.fit(
            x=train_data,
            validation_data=val_data,
            epochs=epochs,
            callbacks=[callback],
        )
        self.logger.log_end({"history": history})

        self.is_trained = True

    def load(self, parameters: Dict = None, **kwargs) -> None:
        """
        Loading method for the BERT classifier that loads a stored model from
        disk.

        :param parameters: Loading parameters
            save_path: The folder to load the model from
        :param kwargs: Additional parameters
            Not used currently
        """
        parameters = self.init_parameters(parameters, **kwargs)
        save_path = parameters.get(
            "save_path", os.path.join("models", "text", "bert")
        )
        self.classifier = tf.keras.models.load_model(save_path)

    def save(self, parameters: Dict = None, **kwargs) -> None:
        """
        Saving method for the BERT classifier that saves a trained model from
        disk.

        :param parameters: Saving parameters
            save_path: The folder where the model is saved
        :param kwargs: Additional parameters
            Not used currently
        """
        if not self.is_trained:
            raise RuntimeError(
                "Model needs to be trained in order to save it!"
            )
        parameters = self.init_parameters(parameters, **kwargs)
        save_path = parameters.get(
            "save_path", os.path.join("models", "text", "bert")
        )
        self.classifier.save(save_path, include_optimizer=False)
        self.logger.save_logs(save_path)

    def classify(self, parameters: Dict = None, **kwargs) -> np.array:
        """
        Classify method for the BERT classifier

        :param parameters: Loading parameters
            which_set: Dataset to use for classification
            batch_size: Batch size
        :param kwargs: Additional parameters
            Not used currently
        """
        parameters = self.init_parameters(parameters, **kwargs)
        which_set = parameters.get("which_set", Set.TEST)
        batch_size = parameters.get("batch_size", 64)
        dataset = self.data_reader.get_emotion_data(
            self.emotions, which_set, batch_size, parameters
        )
        if not self.classifier:
            raise RuntimeError(
                "Please load or train the model before inference!"
            )
        results = self.classifier.predict(dataset)
        return np.argmax(results, axis=1)


def _main():  # pragma: no cover
    classifier = BertClassifier()
    parameters = {"init_lr": 1e-05, "dropout_rate": 0.1, "dense_layer": 0}
    save_path = os.path.join("models", "text", "bert")
    training_loop(classifier, parameters, save_path)


if __name__ == "__main__":  # pragma: no cover
    _main()
