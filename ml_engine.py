
import numpy as np
from sklearn.feature_extraction import DictVectorizer
from sklearn.neighbors import NearestNeighbors
import logging

logger = logging.getLogger(__name__)

class LocatorHealer:
    def __init__(self):
        # DictVectorizer converts text attributes (like class="btn") into mathematical matrices (1s and 0s)
        self.vectorizer = DictVectorizer(sparse=False)
        
        # NearestNeighbors is the core ML algorithm. We want the 1 closest match.
        # algorithm='auto' lets sklearn pick the fastest math for the job (KDTree or BallTree).
        self.nn_model = NearestNeighbors(n_neighbors=1, metric='euclidean', algorithm='auto')

    def _prepare_features(self, element_data):
        """
        Flattens the complex JSON into a flat dictionary that scikit-learn can read.
        We assign 'weights' to prioritize structural clues over visual ones.
        """
        features = {}
        
        # 1. Structural Features (High Priority)
        features[f"tag_{element_data.get('tagName', '')}"] = 1.0
        features[f"class_{element_data.get('className', '')}"] = 0.8
        
        # 2. Textual Features (Medium Priority)
        text = element_data.get('innerText')
        if text:
            features[f"text_{text[:20]}"] = 0.7 # Only use first 20 chars to avoid noise
            
        # 3. Spatial Features (Low Priority - coordinates change with screen size)
        rect = element_data.get('rect', {})
        features['pos_x'] = rect.get('x', 0) / 1000.0 # Normalize down so it doesn't overpower text math
        features['pos_y'] = rect.get('y', 0) / 1000.0
        
        # 4. Dynamic Attributes (ID is king)
        attrs = element_data.get('attributes', {})
        if attrs.get('id'):
            features[f"id_{attrs['id']}"] = 2.0 # Give ID massive mathematical weight
            
        return features

    def train_and_predict(self, target_dna, current_page_elements):
        """
        Takes the broken element (target) and an array of all elements currently on screen,
        and mathematically calculates the closest match.
        """
        if not current_page_elements:
            logger.error("No current elements provided to heal against.")
            return None

        # 1. Convert all raw JSON elements into flattened feature dictionaries
        target_features = self._prepare_features(target_dna)
        candidate_features_list = [self._prepare_features(el) for el in current_page_elements]
        
        # 2. Combine them to ensure the Vectorizer knows about all possible words/classes
        all_data = [target_features] + candidate_features_list
        
        # 3. Vectorize: Convert the text dictionaries into a matrix of numbers
        X_matrix = self.vectorizer.fit_transform(all_data)
        
        # Extract the target vector (Row 0) and the candidate matrix (Row 1 to End)
        X_target = X_matrix[0:1]
        X_candidates = X_matrix[1:]
        
        # 4. Train the Nearest Neighbors model on the current candidates
        self.nn_model.fit(X_candidates)
        
        # 5. Ask the model: "Which candidate is mathematically closest to the target?"
        distances, indices = self.nn_model.kneighbors(X_target)
        
        best_match_index = indices[0][0]
        shortest_distance = distances[0][0]
        
        logger.info(f"🧠 ML Match Found! Distance Score: {shortest_distance:.2f}")
        
        # Return the winning element payload
        return current_page_elements[best_match_index]

# Example Usage (You do not need to run this block, it is just for architecture reference)
if __name__ == "__main__":
    # Test Data: Pretend the ID changed from 'cpinput' to 'cpinput-new'
    target = {"tagName": "input", "id": "cpinput", "className": "jdmart_search_input", "rect": {"x": 424, "y": 21}}
    
    candidates = [
        {"tagName": "div", "className": "header", "rect": {"x": 0, "y": 0}},
        {"tagName": "input", "id": "cpinput-new", "className": "jdmart_search_input", "rect": {"x": 426, "y": 21}}, # The obvious match
        {"tagName": "button", "className": "submit", "rect": {"x": 100, "y": 100}}
    ]
    
    healer = LocatorHealer()
    winner = healer.train_and_predict(target, candidates)
    print(f"Winner ID: {winner.get('id')}")