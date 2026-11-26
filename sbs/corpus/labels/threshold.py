import numpy as np

from sbs.corpus.labels.classifier import OTHER, UNKNOWN


def choose(probs: np.ndarray, classes: np.ndarray, truth: list[str], target: float,
           min_support: int) -> dict[str, dict]:
    """Per domain, the lowest confidence at which out-of-fold predictions of that domain reach
    `target` precision with at least `min_support` predictions; None when it is never reached."""
    pred = classes[probs.argmax(axis=1)]
    conf = probs.max(axis=1)
    truth = np.asarray(truth)
    out = {}
    for domain in classes:
        if domain == OTHER:
            continue
        mask = pred == domain
        order = np.argsort(-conf[mask])
        hits = (truth[mask][order] == domain).cumsum()
        n = np.arange(1, len(order) + 1)
        precision = hits / n
        ok = np.flatnonzero((precision >= target) & (n >= min_support))
        if len(ok):
            i = ok[-1]
            out[str(domain)] = {"threshold": float(conf[mask][order][i]), "cv_precision": float(precision[i]),
                                "cv_support": int(n[i])}
        else:
            out[str(domain)] = {"threshold": None, "cv_precision": None, "cv_support": int(mask.sum())}
    return out


def apply(probs: np.ndarray, classes: np.ndarray, thresholds: dict[str, dict]) -> list[tuple[str, float]]:
    out = []
    for row in probs:
        best = int(row.argmax())
        domain, p = str(classes[best]), float(row[best])
        if domain != OTHER:
            limit = thresholds.get(domain, {}).get("threshold")
            if limit is None or p < limit:
                domain = UNKNOWN
        out.append((domain, p))
    return out
