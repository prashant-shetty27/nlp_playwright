"""
core/ml_engine.py
ML-powered self-healing engine using Scikit-Learn.
Moved from root ml_engine.py and healing/ml_engine.py — unified version.
"""
import logging
import numpy as np  # noqa: F401 — kept for scikit-learn compatibility
from sklearn.feature_extraction import DictVectorizer
from sklearn.neighbors import NearestNeighbors

logger = logging.getLogger(__name__)

_CONFIDENCE_THRESHOLD = 0.65


class LocatorHealer:
    """
    Nearest-Neighbor ML engine for self-healing broken locators.
    Trained on-the-fly from a live DOM snapshot.
    Platform-agnostic: works with any driver (Playwright, Appium, etc.)
    """

    def __init__(self):
        self._vectorizer = DictVectorizer(sparse=False)
        self._model = NearestNeighbors(n_neighbors=1, metric="euclidean")
        self._fitted = False

    def _featurize(self, element: dict) -> dict:
        """Converts raw element DNA into a flat feature dict for ML."""
        attrs = element.get("attributes", {}) or {}
        return {
            "tag":        element.get("tagName", ""),
            "id":         attrs.get("id", ""),
            "name":       attrs.get("name", ""),
            "class":      attrs.get("class", ""),
            "aria_label": attrs.get("aria-label", ""),
            "title":      attrs.get("title", ""),
            "text":       (element.get("innerText") or "")[:80],
            "type":       attrs.get("type", ""),
            "href":       attrs.get("href", ""),
            "placeholder": attrs.get("placeholder", ""),
        }

    def train_and_predict(self, target_dna: dict, candidates: list) -> dict | None:
        """
        Trains a fresh NearestNeighbors model on the current page's DOM
        and predicts the best matching element for the broken target.

        Returns the winning element DNA dict, or None if confidence is too low.
        """
        if not candidates:
            logger.error("❌ No candidates provided for ML healing.")
            return None

        features = [self._featurize(c) for c in candidates]
        target_feature = self._featurize(target_dna)

        try:
            X = self._vectorizer.fit_transform(features)
            self._model.fit(X)

            target_vec = self._vectorizer.transform([target_feature])
            distances, indices = self._model.kneighbors(target_vec)

            distance = distances[0][0]
            if distance > _CONFIDENCE_THRESHOLD:
                logger.warning(
                    "⚠️ ML confidence too low (distance=%.4f > threshold=%.2f). Skipping heal.",
                    distance, _CONFIDENCE_THRESHOLD
                )
                return None

            winner = candidates[indices[0][0]]
            logger.info("✅ ML Winner found (distance=%.4f): %s", distance, winner.get("tagName"))
            return winner

        except Exception:
            logger.exception("❌ ML Engine failed during prediction.")
            return None
