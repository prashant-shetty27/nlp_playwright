"""
ml_engine.py  —  LocatorHealer v2

Upgrade summary over v1:
  ① Cosine similarity    → better than Euclidean for sparse categorical data
  ② 5-tier feature weights → stability-ordered (data-testid > aria > id > class > position)
  ③ Individual class encoding → avoids one-hot class-string explosion
  ④ DOM pre-filter       → reduces 500-2000 node DOMs to same-tag pool before ML runs
  ⑤ Confidence threshold → rejects matches whose distance exceeds a safe ceiling
  ⑥ Double-check layer   → rule-based attribute cross-validation on top of ML result
  ⑦ Combined rejection gate → only rejects when BOTH ML AND rule-check fail (avoids over-rejection)
"""

import logging
import numpy as np  # noqa: F401 — kept for scikit-learn compatibility
from sklearn.feature_extraction import DictVectorizer
from sklearn.neighbors import NearestNeighbors

logger = logging.getLogger(__name__)


class LocatorHealer:
    """
    Self-healing element locator using a tiered, validated ML strategy.

    Full pipeline:
      1. PRE-FILTER    — shrink 500-2000 DOM nodes to same-tag / semantic-group candidates
      2. FEATURE EXT   — stability-weighted, tiered attribute encoding
      3. ML MATCH      — cosine-similarity nearest neighbour
      4. CONFIDENCE    — reject if cosine distance > CONFIDENCE_THRESHOLD
      5. DOUBLE-CHECK  — rule-based attribute cross-validation (second opinion)
      6. COMBINED GATE — only reject when BOTH ML distance AND validation are poor
    """

    # ── Tunable thresholds ──────────────────────────────────────────────────
    # Cosine distance range: 0 = identical, 2 = perfectly opposite.
    # 1.2 is a safe ceiling; raise to 1.5 for more permissive healing.
    CONFIDENCE_THRESHOLD: float = 1.2

    # Combined-gate: reject only when BOTH conditions are true
    VALIDATION_MIN_SCORE: float = 0.3  # double-check score below this AND...
    COMBINED_ML_FLOOR: float = 0.8     # ...ML distance above this → reject

    # ── Stability-tier weights ───────────────────────────────────────────────
    # Higher = more stable across UI redesigns / class renames / layout shifts.
    WEIGHTS = {
        "data-testid": 5.0,  # Most stable: explicit test hook
        "id":          4.0,  # Stable, but can be generated/dynamic
        "aria-label":  3.5,  # Stable: accessibility contract
        "name":        3.0,  # Stable for forms
        "role":        2.5,  # Stable accessibility role
        "type":        2.0,  # Stable for inputs
        "placeholder": 1.8,  # Somewhat stable
        "href":        1.5,  # Normalized to path (strips query-string noise)
        "tag":         1.5,  # Structural
        "text":        1.2,  # Can shift with content updates
        "class":       0.8,  # Brittle — class names change frequently
        "position":    0.3,  # Very brittle — changes with any layout shift
    }

    # Semantic tag groups used for pre-filter expansion
    _TAG_GROUPS: dict = {
        "button":  ["button", "a", "span", "div"],
        "input":   ["input", "textarea", "select"],
        "a":       ["a", "button", "span"],
        "div":     ["div", "section", "article", "main", "aside"],
        "span":    ["span", "p", "label", "div"],
        "select":  ["select", "input"],
    }

    # Class tokens that signal utility/animation — stripped before encoding (brittle)
    _CLASS_BLOCKLIST = ("font", "animate", "transition", "hover", "active", "focus", "visited")

    def __init__(self):
        self.vectorizer = DictVectorizer(sparse=False)
        # brute-force required for cosine metric in sklearn NearestNeighbors
        self.nn_model = NearestNeighbors(n_neighbors=1, metric="cosine", algorithm="brute")

    # ──────────────────────────────────────────────────────────────────────────
    # STEP 1: DOM PRE-FILTER
    # ──────────────────────────────────────────────────────────────────────────
    def _pre_filter(self, target_dna: dict, candidates: list) -> list:
        """
        Reduces a large DOM candidate pool before ML computation.
        Critical for pages with 500-2000 nodes: keeps ML fast and focused.

        Priority:
          a) Same tag      — most focused  (e.g. input → only inputs)
          b) Tag group     — semantic fallback when same-tag pool < 3
          c) All candidates — last resort, no reduction possible
        """
        target_tag = target_dna.get("tagName", "").lower()

        same_tag = [c for c in candidates if c.get("tagName", "").lower() == target_tag]
        if len(same_tag) >= 3:
            logger.info(
                "🔍 Pre-filter: %d → %d candidates (same tag: <%s>)",
                len(candidates), len(same_tag), target_tag,
            )
            return same_tag

        related = self._TAG_GROUPS.get(target_tag, [target_tag])
        expanded = [c for c in candidates if c.get("tagName", "").lower() in related]
        if expanded:
            logger.info(
                "🔍 Pre-filter (semantic group): %d → %d candidates",
                len(candidates), len(expanded),
            )
            return expanded

        logger.info("🔍 Pre-filter: no reduction possible, using all %d candidates", len(candidates))
        return candidates

    # ──────────────────────────────────────────────────────────────────────────
    # STEP 2: FEATURE EXTRACTION
    # ──────────────────────────────────────────────────────────────────────────
    def _prepare_features(self, element_data: dict) -> dict:
        """
        Encodes element DNA as a stability-weighted feature dict for scikit-learn.

        Key changes vs v1:
          - Each class token encoded individually (avoids full-string one-hot explosion)
          - Utility/animation classes stripped via blocklist
          - Class tokens capped at 5 to prevent vector size blowup on utility-heavy UIs
          - href normalized to path only (strips query strings)
          - Position normalized to [0, 1] range then further downweighted
        """
        features: dict = {}
        attrs = element_data.get("attributes", {})
        rect = element_data.get("rect", {})
        W = self.WEIGHTS

        # ── TIER 1: Developer-intent signals (most stable) ──
        if attrs.get("data-testid"):
            features[f"testid_{attrs['data-testid']}"] = W["data-testid"]

        if attrs.get("id"):
            features[f"id_{attrs['id']}"] = W["id"]

        if attrs.get("aria-label"):
            features[f"aria_{attrs['aria-label'][:50]}"] = W["aria-label"]

        if attrs.get("name"):
            features[f"name_{attrs['name']}"] = W["name"]

        # ── TIER 2: Semantic signals ──
        if attrs.get("role"):
            features[f"role_{attrs['role']}"] = W["role"]

        if attrs.get("type"):
            features[f"type_{attrs['type']}"] = W["type"]

        if attrs.get("placeholder"):
            features[f"placeholder_{attrs['placeholder'][:30]}"] = W["placeholder"]

        if attrs.get("href"):
            path = attrs["href"].split("?")[0][:50]
            features[f"href_{path}"] = W["href"]

        tag = element_data.get("tagName", "")
        if tag:
            features[f"tag_{tag}"] = W["tag"]

        # ── TIER 3: Text content (medium stability) ──
        text = (element_data.get("innerText") or "").strip()[:30]
        if text:
            features[f"text_{text}"] = W["text"]

        # ── TIER 4: Individual class tokens (brittle, capped + filtered) ──
        class_str = attrs.get("class", "") or element_data.get("className", "") or ""
        if class_str:
            stable = [
                c for c in class_str.split()
                if not any(skip in c.lower() for skip in self._CLASS_BLOCKLIST)
            ]
            for cls in stable[:5]:  # cap at 5 to prevent vector explosion
                features[f"cls_{cls}"] = W["class"]

        # ── TIER 5: Spatial (lowest weight — very brittle) ──
        features["pos_x"] = (rect.get("x", 0) / 2000.0) * W["position"]
        features["pos_y"] = (rect.get("y", 0) / 2000.0) * W["position"]

        return features

    # ──────────────────────────────────────────────────────────────────────────
    # STEP 5: DOUBLE-CHECK (rule-based cross-validation)
    # ──────────────────────────────────────────────────────────────────────────
    def _double_check(self, target_dna: dict, winner_dna: dict) -> float:
        """
        Independently validates the ML winner using exact attribute matching.
        Returns a score in [0.0, 1.0] — purely deterministic, no ML.

        Prevents cases where ML picks the geometrically closest but semantically
        wrong element (silent false positive).
        """
        STABLE_ATTRS = ["data-testid", "id", "name", "aria-label", "role", "type", "placeholder"]

        target_attrs = target_dna.get("attributes", {})
        winner_attrs = winner_dna.get("attributes", {})

        matches = 0
        total = 0

        for attr in STABLE_ATTRS:
            t_val = target_attrs.get(attr)
            if t_val:  # only score attributes that existed in the original element
                total += 1
                if t_val == winner_attrs.get(attr):
                    matches += 1

        # Always include tag agreement
        if target_dna.get("tagName") == winner_dna.get("tagName"):
            matches += 1
        total += 1

        score = matches / total if total > 0 else 0.5
        logger.info(
            "✅ Double-check: %d/%d attributes matched → confidence %.0f%%",
            matches, total, score * 100,
        )
        return score

    # ──────────────────────────────────────────────────────────────────────────
    # MAIN ENTRY POINT
    # ──────────────────────────────────────────────────────────────────────────
    def train_and_predict(self, target_dna: dict, current_page_elements: list):
        """
        Executes the full healing pipeline.
        Returns the winner element DNA dict, or None if it's not safe to heal.

        Returns None when:
          - No candidates available
          - ML cosine distance > CONFIDENCE_THRESHOLD
          - Double-check score is low AND ML distance is mediocre (combined gate)
        """
        if not current_page_elements:
            logger.error("No current elements provided to heal against.")
            return None

        # ── Step 1: Pre-filter ──
        candidates = self._pre_filter(target_dna, current_page_elements)

        # ── Step 2: Feature extraction ──
        target_features = self._prepare_features(target_dna)
        candidate_features = [self._prepare_features(el) for el in candidates]

        # ── Step 3: Vectorize ──
        all_data = [target_features] + candidate_features
        X_matrix = self.vectorizer.fit_transform(all_data)
        X_target = X_matrix[0:1]
        X_candidates = X_matrix[1:]

        if X_candidates.shape[0] == 0:
            logger.error("No candidates survived vectorization.")
            return None

        # ── Step 4: ML nearest neighbour ──
        self.nn_model.fit(X_candidates)
        distances, indices = self.nn_model.kneighbors(X_target)

        ml_distance = float(distances[0][0])
        best_idx = int(indices[0][0])
        winner_dna = candidates[best_idx]

        logger.info(
            "🧠 ML result | distance=%.4f | threshold=%.2f | tag=<%s>",
            ml_distance, self.CONFIDENCE_THRESHOLD, winner_dna.get("tagName"),
        )

        # ── Step 5: Hard confidence gate ──
        if ml_distance > self.CONFIDENCE_THRESHOLD:
            logger.warning(
                "⚠️  Heal rejected: ML distance %.4f > threshold %.2f. No safe match found.",
                ml_distance, self.CONFIDENCE_THRESHOLD,
            )
            return None

        # ── Step 6: Double-check cross-validation ──
        validation_score = self._double_check(target_dna, winner_dna)

        # ── Step 7: Combined gate — only reject when BOTH are poor ──
        if validation_score < self.VALIDATION_MIN_SCORE and ml_distance > self.COMBINED_ML_FLOOR:
            logger.warning(
                "⚠️  Heal rejected (combined gate): double-check=%.0f%% AND ml_distance=%.4f "
                "— too risky to avoid false positive.",
                validation_score * 100, ml_distance,
            )
            return None

        logger.info(
            "🏥 Heal accepted | ML distance=%.4f | validation=%.0f%% | tag=<%s>",
            ml_distance, validation_score * 100, winner_dna.get("tagName"),
        )
        return winner_dna


# ─────────────────────────────────────────────────────────────────────────────
# Architecture reference — not part of production flow
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Simulates: ID changed from 'cpinput' → 'cpinput-new', aria-label preserved
    target = {
        "tagName": "input",
        "attributes": {"id": "cpinput", "type": "text", "aria-label": "Search city"},
        "className": "jdmart_search_input",
        "rect": {"x": 424, "y": 21},
    }
    candidates = [
        {"tagName": "div",    "attributes": {},                                                         "className": "header",             "rect": {"x": 0,   "y": 0}},
        {"tagName": "input",  "attributes": {"id": "cpinput-new", "type": "text", "aria-label": "Search city"}, "className": "jdmart_search_input", "rect": {"x": 426, "y": 21}},
        {"tagName": "button", "attributes": {},                                                         "className": "submit",             "rect": {"x": 100, "y": 100}},
    ]
    healer = LocatorHealer()
    winner = healer.train_and_predict(target, candidates)
    if winner:
        print(f"Winner: <{winner.get('tagName')}> id={winner.get('attributes', {}).get('id')}")
    else:
        print("No safe heal found.")
