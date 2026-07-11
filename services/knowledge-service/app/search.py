import math
import re
from collections import Counter


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class TfidfIndex:
    def __init__(self, documents: dict[str, str]):
        self.documents = documents
        self.doc_tokens = {doc_id: tokenize(text) for doc_id, text in documents.items()}
        self.doc_freq = self._compute_doc_freq()
        self.doc_vectors = {
            doc_id: self._tfidf_vector(tokens)
            for doc_id, tokens in self.doc_tokens.items()
        }

    def _compute_doc_freq(self) -> Counter:
        df: Counter = Counter()
        for tokens in self.doc_tokens.values():
            df.update(set(tokens))
        return df

    def _tfidf_vector(self, tokens: list[str]) -> dict[str, float]:
        n_docs = max(len(self.doc_tokens), 1)
        term_counts = Counter(tokens)
        vector = {}
        for term, count in term_counts.items():
            tf = count / len(tokens) if tokens else 0
            df = self.doc_freq.get(term, 1)
            idf = math.log((n_docs + 1) / (df + 1)) + 1
            vector[term] = tf * idf
        return vector

    def search(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        query_tokens = tokenize(query)
        query_vector = self._tfidf_vector(query_tokens)
        scores = []
        for doc_id, doc_vector in self.doc_vectors.items():
            score = self._cosine_similarity(query_vector, doc_vector)
            if score > 0:
                scores.append((doc_id, score))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    @staticmethod
    def _cosine_similarity(v1: dict[str, float], v2: dict[str, float]) -> float:
        common_terms = set(v1) & set(v2)
        dot = sum(v1[t] * v2[t] for t in common_terms)
        norm1 = math.sqrt(sum(val ** 2 for val in v1.values()))
        norm2 = math.sqrt(sum(val ** 2 for val in v2.values()))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)
