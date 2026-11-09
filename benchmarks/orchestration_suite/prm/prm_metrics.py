"""Ranking metrics for the orchestration PRM (pure Python, shared by the dataset baseline and the trainer)."""
from collections import defaultdict


def auroc(scores, labels):
    """P(score of a positive > score of a negative), ties count half. None if one class is missing."""
    pairs = sorted(zip(scores, labels))
    pos = sum(1 for _, y in pairs if y)
    neg = len(pairs) - pos
    if not pos or not neg:
        return None
    rank_sum, i = 0.0, 0
    while i < len(pairs):
        j = i
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        mid = (i + j + 1) / 2  # mean of the 1-based ranks i+1..j
        rank_sum += mid * sum(1 for k in range(i, j) if pairs[k][1])
        i = j
    return (rank_sum - pos * (pos + 1) / 2) / (pos * neg)


def _ranks(xs):
    order = sorted(range(len(xs)), key=xs.__getitem__)
    out, i = [0.0] * len(xs), 0
    while i < len(order):
        j = i
        while j < len(order) and xs[order[j]] == xs[order[i]]:
            j += 1
        for k in range(i, j):
            out[order[k]] = (i + j + 1) / 2
        i = j
    return out


def spearman(a, b):
    if len(a) < 2:
        return None
    ra, rb = _ranks(list(a)), _ranks(list(b))
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va, vb = sum((x - ma) ** 2 for x in ra), sum((y - mb) ** 2 for y in rb)
    return cov / (va * vb) ** 0.5 if va and vb else None


def brier(pred, target):
    return sum((p - t) ** 2 for p, t in zip(pred, target)) / len(pred)


def evaluate(rows, pred):
    """rows: dataset rows (need q, group, step); pred: predicted value per row.

    - auroc_success: does the model rank states with q > 0.5 above those with q <= 0.5
    - spearman: rank correlation with the Monte-Carlo estimate q
    - within_task_pairwise: among states of the SAME task whose q differ, how often the one with higher q gets the
      higher prediction (the use case: telling a good state from a bad one for the same question)
    """
    q = [r["q"] for r in rows]
    res = {"n": len(rows), "brier": brier(pred, q), "auroc_success": auroc(pred, [x > 0.5 for x in q]),
           "spearman": spearman(pred, q)}
    by_task = defaultdict(list)
    for r, p in zip(rows, pred):
        by_task[r["group"]].append((r["q"], p))
    right = total = 0
    for items in by_task.values():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                (q1, p1), (q2, p2) = items[i], items[j]
                if q1 == q2:
                    continue
                total += 1
                right += 1.0 if (p1 - p2) * (q1 - q2) > 0 else 0.5 if p1 == p2 else 0.0
    res["within_task_pairwise"] = right / total if total else None
    res["within_task_pairs"] = total
    return res
