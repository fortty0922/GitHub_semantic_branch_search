import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline, make_pipeline

UNKNOWN = "unknown"
OTHER = "other"
NOT_LABELLED = (UNKNOWN, OTHER)


def build(cfg: dict) -> Pipeline:
    return make_pipeline(
        TfidfVectorizer(max_features=cfg["max_features"], min_df=cfg["min_df"], sublinear_tf=True,
                        token_pattern=r"\S+"),
        LogisticRegression(C=cfg["C"], max_iter=1000, class_weight="balanced"),
    )


def usable(labels: list[str], cfg: dict) -> set[str]:
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    return {c for c, n in counts.items() if n >= cfg["cv_folds"]}


def cv_probabilities(texts: list[str], labels: list[str], cfg: dict) -> tuple[np.ndarray, np.ndarray]:
    """Out-of-fold class probabilities for the training set; columns follow the returned classes."""
    folds = StratifiedKFold(n_splits=cfg["cv_folds"], shuffle=True, random_state=cfg["seed"])
    probs = cross_val_predict(build(cfg), texts, labels, cv=folds, method="predict_proba")
    return probs, np.array(sorted(set(labels)))


def fit_predict(train_texts: list[str], train_labels: list[str], texts: list[str],
                cfg: dict) -> tuple[np.ndarray, np.ndarray]:
    model = build(cfg).fit(train_texts, train_labels)
    probs = model.predict_proba(texts) if texts else np.zeros((0, len(model.classes_)))
    return probs, model.classes_
