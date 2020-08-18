import json
import logging
import os
import pickle
import typing
from typing import Any, Callable, Dict, List, Optional, Text, Tuple

import numpy as np
import rasa.utils.io
from rasa.core.constants import DEFAULT_POLICY_PRIORITY
from rasa.core.domain import Domain
from rasa.core.featurizers.tracker_featurizers import (
    MaxHistoryTrackerFeaturizer,
    TrackerFeaturizer,
)
from rasa.core.interpreter import NaturalLanguageInterpreter, RegexInterpreter
from rasa.core.policies.policy import Policy
from rasa.core.trackers import DialogueStateTracker
from rasa.core.training.generator import TrackerWithCachedStates
from rasa.utils.common import raise_warning
from sklearn.base import clone
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GridSearchCV
from sklearn.preprocessing import LabelEncoder

# noinspection PyProtectedMember
from sklearn.utils import shuffle as sklearn_shuffle

logger = logging.getLogger(__name__)

if typing.TYPE_CHECKING:
    import sklearn


class SklearnPolicy(Policy):
    """Use an sklearn classifier to train a policy."""

    def __init__(
        self,
        featurizer: Optional[MaxHistoryTrackerFeaturizer] = None,
        priority: int = DEFAULT_POLICY_PRIORITY,
        model: Optional["sklearn.base.BaseEstimator"] = None,
        param_grid: Optional[Dict[Text, List] or List[Dict]] = None,
        cv: Optional[int] = None,
        scoring: Optional[Text or List or Dict or Callable] = "accuracy",
        label_encoder: LabelEncoder = LabelEncoder(),
        shuffle: bool = True,
        **kwargs: Any,
    ) -> None:
        """Create a new sklearn policy.

        Args:
            featurizer: Featurizer used to convert the training data into
                vector format.
            model: The sklearn model or model pipeline.
            param_grid: If *param_grid* is not None and *cv* is given,
                a grid search on the given *param_grid* is performed
                (e.g. *param_grid={'n_estimators': [50, 100]}*).
            cv: If *cv* is not None, perform a cross validation on
                the training data. *cv* should then conform to the
                sklearn standard (e.g. *cv=5* for a 5-fold cross-validation).
            scoring: Scoring strategy, using the sklearn standard.
            label_encoder: Encoder for the labels. Must implement an
                *inverse_transform* method.
            shuffle: Whether to shuffle training data.
        """

        if featurizer:
            if not isinstance(featurizer, MaxHistoryTrackerFeaturizer):
                raise TypeError(
                    "Passed featurizer of type {}, should be "
                    "MaxHistoryTrackerFeaturizer."
                    "".format(type(featurizer).__name__)
                )
        super().__init__(featurizer, priority)

        self.model = model or self._default_model()
        self.cv = cv
        self.param_grid = param_grid
        self.scoring = scoring
        self.label_encoder = label_encoder
        self.shuffle = shuffle

        # attributes that need to be restored after loading
        self._pickle_params = ["model", "cv", "param_grid", "scoring", "label_encoder"]
        self._train_params = kwargs

    @staticmethod
    def _default_model() -> Any:
        return LogisticRegression(solver="liblinear", multi_class="auto")

    @property
    def _state(self):
        return {attr: getattr(self, attr) for attr in self._pickle_params}

    def model_architecture(self, **kwargs) -> Any:
        # filter out kwargs that cannot be passed to model
        train_params = self._get_valid_params(self.model.__init__, **kwargs)
        return self.model.set_params(**train_params)

    def _get_max_dialogue_length(self, X: np.ndarray) -> int:
        lengths = [row[:, 2].shape[0] for row in X]
        return max(lengths)

    def _fill_in_features(
        self, state_features: List[np.ndarray], max_length: int
    ) -> List[np.ndarray]:
        if len(state_features) == max_length:
            return state_features
        else:
            shape_of_input = state_features[0].shape[-1]
            return [np.ones((1, shape_of_input)) * -1] * (
                max_length - len(state_features)
            ) + state_features

    def _collect_features(self, X: np.ndarray) -> List[np.ndarray]:
        max_dialogue_length = (
            self.featurizer.max_history or self._get_max_dialogue_length(X)
        )
        X_intent = []
        X_previous_action = []
        X_slots = []
        for row in X:
            state_intent = [intent for intent in row[:, 2]]
            state_intent = self._fill_in_features(state_intent, max_dialogue_length)
            state_intent = np.hstack(state_intent)
            X_intent.append(state_intent)

            state_previous_action = [previous_action for previous_action in row[:, 6]]
            state_previous_action = self._fill_in_features(
                state_previous_action, max_dialogue_length
            )
            state_previous_action = np.hstack(state_previous_action)
            X_previous_action.append(state_previous_action)

            state_slots = [np.expand_dims(slots, 0) for slots in row[:, 8]]
            state_slots = self._fill_in_features(state_slots, max_dialogue_length)
            state_slots = np.hstack(state_slots)
            X_slots.append(state_slots)
        X_intent = np.vstack(X_intent)
        X_previous_action = np.vstack(X_previous_action)
        X_slots = np.vstack(X_slots)
        return X_intent, X_previous_action, X_slots

    def _preprocess_data(self, X: np.ndarray) -> np.ndarray:
        X_intent, X_previous_action, X_slots = self._collect_features(X)
        return np.concatenate((X_intent, X_previous_action, X_slots), axis=-1)

    def _search_and_score(self, model, X, y, param_grid) -> Tuple[Any, Any]:
        search = GridSearchCV(
            model, param_grid=param_grid, cv=self.cv, scoring="accuracy", verbose=1
        )
        search.fit(X, y)
        print("Best params:", search.best_params_)
        return search.best_estimator_, search.best_score_

    def train(
        self,
        training_trackers: List[TrackerWithCachedStates],
        domain: Domain,
        interpreter: NaturalLanguageInterpreter,
        **kwargs: Any,
    ) -> None:
        # TODO sklearn policy is broken
        X, y = self.featurize_for_training(
            training_trackers, domain, interpreter, **kwargs
        )
        if self.shuffle:
            X, y = sklearn_shuffle(X, y)

        self._train_params.update(kwargs)
        model = self.model_architecture(**self._train_params)
        score = None
        # Note: clone is called throughout to avoid mutating default
        # arguments.
        self.label_encoder = clone(self.label_encoder).fit(y)
        Xt = self._preprocess_data(X)
        yt = self.label_encoder.transform(y)

        if self.cv is None:
            model = clone(model).fit(Xt, yt)
        else:
            param_grid = self.param_grid or {}
            model, score = self._search_and_score(model, Xt, yt, param_grid)

        self.model = model
        logger.info("Done fitting sklearn policy model")
        if score is not None:
            logger.info(f"Cross validation score: {score:.5f}")

    def _postprocess_prediction(self, y_proba, domain) -> List[float]:
        yp = y_proba[0].tolist()

        # Some classes might not be part of the training labels. Since
        # sklearn does not predict labels it has never encountered
        # during training, it is necessary to insert missing classes.
        indices = self.label_encoder.inverse_transform(np.arange(len(yp)))
        y_filled = [0.0 for _ in range(domain.num_actions)]
        for i, pred in zip(indices, yp):
            y_filled[i] = pred

        return y_filled

    def predict_action_probabilities(
        self,
        tracker: DialogueStateTracker,
        domain: Domain,
        interpreter: NaturalLanguageInterpreter,
        **kwargs: Any,
    ) -> List[float]:
        X = self.featurizer.create_X([tracker], domain, interpreter)
        Xt = self._preprocess_data(X)
        y_proba = self.model.predict_proba(Xt)
        return self._postprocess_prediction(y_proba, domain)

    def persist(self, path: Text) -> None:

        if self.model:
            self.featurizer.persist(path)

            meta = {"priority": self.priority}

            meta_file = os.path.join(path, "sklearn_policy.json")
            rasa.utils.io.dump_obj_as_json_to_file(meta_file, meta)

            filename = os.path.join(path, "sklearn_model.pkl")
            rasa.utils.io.pickle_dump(filename, self._state)
        else:
            raise_warning(
                "Persist called without a trained model present. "
                "Nothing to persist then!"
            )

    @classmethod
    def load(cls, path: Text) -> Policy:
        filename = os.path.join(path, "sklearn_model.pkl")
        if not os.path.exists(path):
            raise OSError(
                "Failed to load dialogue model. Path {} "
                "doesn't exist".format(os.path.abspath(filename))
            )

        featurizer = TrackerFeaturizer.load(path)
        assert isinstance(featurizer, MaxHistoryTrackerFeaturizer), (
            "Loaded featurizer of type {}, should be "
            "MaxHistoryTrackerFeaturizer.".format(type(featurizer).__name__)
        )

        meta_file = os.path.join(path, "sklearn_policy.json")
        meta = json.loads(rasa.utils.io.read_file(meta_file))

        policy = cls(featurizer=featurizer, priority=meta["priority"])

        state = rasa.utils.io.pickle_load(filename)

        vars(policy).update(state)

        logger.info("Loaded sklearn model")
        return policy
