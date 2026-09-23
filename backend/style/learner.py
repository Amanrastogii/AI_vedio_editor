"""
Learning components (pure numpy, deterministic):

- Standardizer            z-scoring fitted on training windows
- train_selector          class-balanced L2 logistic regression: P(editor keeps window)
- evaluate                grouped cross-validation (leave-one-example-out when possible)
- Memory                  kNN retrieval over past decisions (cosine on z-scored features)
- blend                   final keep probability = model ⊕ retrieved memories

Small-data honesty: with few examples the metrics say so ("low_data") and the
retrieval memory carries more weight than the parametric model.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np


@dataclass
class Standardizer:
    mean: np.ndarray
    std: np.ndarray

    @classmethod
    def fit(cls, X: np.ndarray) -> "Standardizer":
        std = X.std(axis=0)
        return cls(X.mean(axis=0), np.where(std < 1e-6, 1.0, std))

    def transform(self, X: np.ndarray) -> np.ndarray:
        return (X - self.mean) / self.std

    def to_dict(self) -> dict:
        return {"mean": self.mean.tolist(), "std": self.std.tolist()}

    @classmethod
    def from_dict(cls, d: dict) -> "Standardizer":
        return cls(np.array(d["mean"], dtype=np.float64), np.array(d["std"], dtype=np.float64))


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def train_selector(Xz: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 800, lr: float = 0.2) -> dict:
    """Class-balanced L2 logistic regression. Returns {"w", "b", "constant"}."""
    n = len(y)
    pos = y.sum()
    if n == 0:
        return {"w": [0.0] * Xz.shape[1], "b": 0.0, "constant": 0.5}
    if pos == 0 or pos == n:
        return {"w": [0.0] * Xz.shape[1], "b": 0.0, "constant": float(pos / n)}
    sw = np.where(y == 1, n / (2 * pos), n / (2 * (n - pos)))
    w = np.zeros(Xz.shape[1])
    b = 0.0
    for _ in range(iters):
        p = _sigmoid(Xz @ w + b)
        g = (p - y) * sw
        w -= lr * (Xz.T @ g / n + l2 * w / n)
        b -= lr * g.mean()
    return {"w": w.tolist(), "b": float(b), "constant": None}


def predict_selector(model: dict, Xz: np.ndarray) -> np.ndarray:
    if model.get("constant") is not None:
        return np.full(len(Xz), float(model["constant"]))
    return _sigmoid(Xz @ np.array(model["w"]) + float(model["b"]))


def _auc(y: np.ndarray, p: np.ndarray) -> Optional[float]:
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    ranks = np.concatenate([pos, neg]).argsort().argsort() + 1
    return float((ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def _balanced_acc(y: np.ndarray, p: np.ndarray) -> Optional[float]:
    pred = p >= 0.5
    accs = [float((pred[y == c] == bool(c)).mean()) for c in (0, 1) if (y == c).any()]
    return float(np.mean(accs)) if accs else None


def evaluate(X: np.ndarray, y: np.ndarray, groups: np.ndarray) -> Dict[str, object]:
    """Held-out metrics: leave-one-example-out if ≥2 examples, else 5-fold; blended model+memory."""
    n = len(y)
    out: Dict[str, object] = {
        "samples": int(n), "kept": int(y.sum()), "dropped": int(n - y.sum()),
        "examples": int(len(set(groups.tolist()))),
    }
    out["low_data"] = bool(n < 40 or out["kept"] < 5 or out["dropped"] < 5)
    if n < 8 or y.sum() == 0 or y.sum() == n:
        out["cv"] = None
        out["note"] = "not enough varied decisions to measure accuracy yet — add more examples"
        return out
    uniq = sorted(set(groups.tolist()))
    if len(uniq) >= 2:
        folds = [groups == g for g in uniq]
        scheme = "leave-one-example-out"
    else:
        rng = np.random.default_rng(0)
        perm = rng.permutation(n)
        folds = [np.isin(np.arange(n), perm[k::5]) for k in range(5)]
        scheme = "5-fold"
    preds = np.zeros(n)
    for test in folds:
        train = ~test
        if train.sum() < 4 or test.sum() == 0:
            preds[test] = y[train].mean() if train.any() else 0.5
            continue
        sc = Standardizer.fit(X[train])
        m = train_selector(sc.transform(X[train]), y[train])
        mem = Memory(sc.transform(X[train]), y[train], [{}] * int(train.sum()), [""] * int(train.sum()))
        preds[test] = blend(predict_selector(m, sc.transform(X[test])),
                            mem.keep_probability(sc.transform(X[test])), len(y[train]))
    base = max(y.mean(), 1 - y.mean())
    out["cv"] = {
        "scheme": scheme,
        "balanced_accuracy": _balanced_acc(y, preds),
        "auc": _auc(y, preds),
        "majority_baseline": float(base),
    }
    return out


class Memory:
    """kNN retrieval over past editing decisions (the retrieval half of RAG)."""

    def __init__(self, Z: np.ndarray, kept: np.ndarray, decisions: List[dict], descriptions: List[str],
                 sources: Optional[List[str]] = None):
        self.Z = Z
        norms = np.linalg.norm(Z, axis=1, keepdims=True)
        self.U = Z / np.where(norms < 1e-9, 1.0, norms)
        self.kept = kept.astype(float)
        self.decisions = decisions
        self.descriptions = descriptions
        self.sources = sources or [""] * len(kept)

    def __len__(self) -> int:
        return len(self.kept)

    def neighbors(self, zq: np.ndarray, k: int = 7):
        if len(self) == 0:
            return np.zeros((len(zq), 0), dtype=int), np.zeros((len(zq), 0))
        q = zq / np.where(np.linalg.norm(zq, axis=1, keepdims=True) < 1e-9, 1.0,
                          np.linalg.norm(zq, axis=1, keepdims=True))
        sims = q @ self.U.T
        k = min(k, len(self))
        idx = np.argsort(-sims, axis=1)[:, :k]
        return idx, np.take_along_axis(sims, idx, axis=1)

    def keep_probability(self, zq: np.ndarray, k: int = 7) -> np.ndarray:
        idx, sims = self.neighbors(zq, k)
        if idx.shape[1] == 0:
            return np.full(len(zq), 0.5)
        w = np.maximum(sims, 0) + 1e-3
        return (self.kept[idx] * w).sum(axis=1) / w.sum(axis=1)

    def trim_advice(self, zq_row: np.ndarray, k: int = 7) -> Optional[Dict[str, float]]:
        """Weighted head-trim / keep-fraction of the most similar windows the editor KEPT."""
        kept_idx = np.where(self.kept > 0.5)[0]
        if kept_idx.size == 0:
            return None
        sub = Memory(self.Z[kept_idx], self.kept[kept_idx],
                     [self.decisions[i] for i in kept_idx], [self.descriptions[i] for i in kept_idx])
        idx, sims = sub.neighbors(zq_row[None, :], k)
        w = np.maximum(sims[0], 0) + 1e-3
        heads = np.array([sub.decisions[i].get("head_frac", 0.0) for i in idx[0]])
        keeps = np.array([sub.decisions[i].get("keep_frac", 1.0) for i in idx[0]])
        return {"head_frac": float((heads * w).sum() / w.sum()), "keep_frac": float((keeps * w).sum() / w.sum())}

    def explain(self, zq_row: np.ndarray, k: int = 1) -> List[dict]:
        idx, sims = self.neighbors(zq_row[None, :], k)
        return [{"similarity": round(float(s), 3), "kept": bool(self.kept[i] > 0.5),
                 "description": self.descriptions[i], "source": self.sources[i]}
                for i, s in zip(idx[0], sims[0])]


def blend(p_model: np.ndarray, p_memory: np.ndarray, n_memories: int) -> np.ndarray:
    """Memory dominates with little data, the model with more (both are always used)."""
    w_mem = 0.7 if n_memories < 60 else 0.5 if n_memories < 300 else 0.35
    return (1 - w_mem) * p_model + w_mem * p_memory


def feature_importance(model: dict, names: Sequence[str]) -> List[dict]:
    if model.get("constant") is not None:
        return []
    w = np.array(model["w"])
    order = np.argsort(-np.abs(w))
    return [{"feature": names[i], "weight": round(float(w[i]), 3)} for i in order]
