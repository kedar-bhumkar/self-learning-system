"""Sentence embeddings. sentence_transformers is imported lazily inside the class."""
from __future__ import annotations

import numpy as np


class Embedder:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def encode(self, texts: list[str]) -> np.ndarray:
        """L2-normalized embeddings, one row per text (dot product = cosine)."""
        if not texts:
            return np.zeros((0, 384), dtype=float)
        vecs = self._load().encode(list(texts), normalize_embeddings=True,
                                   convert_to_numpy=True, show_progress_bar=False)
        vecs = np.asarray(vecs, dtype=float)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms
