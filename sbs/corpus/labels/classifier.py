from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

UNKNOWN = "unknown"


def fit_predict(train_texts: list[str], train_labels: list[str], texts: list[str],
                cfg: dict) -> list[tuple[str, float]]:
    if not texts:
        return []
    if len(set(train_labels)) < 2:
        return [(UNKNOWN, 0.0)] * len(texts)
    vectorizer = TfidfVectorizer(max_features=cfg["max_features"], min_df=cfg["min_df"],
                                 sublinear_tf=True, token_pattern=r"\S+")
    model = LogisticRegression(C=cfg["C"], max_iter=1000)
    model.fit(vectorizer.fit_transform(train_texts), train_labels)
    probs = model.predict_proba(vectorizer.transform(texts))
    out = []
    for row in probs:
        best = row.argmax()
        p = float(row[best])
        out.append((model.classes_[best] if p >= cfg["min_confidence"] else UNKNOWN, p))
    return out
