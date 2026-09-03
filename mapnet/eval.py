"""Score predicted mappings against a gold standard."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from sssom_pydantic import SemanticMapping

Pair = tuple[str, str]

Ranked = Mapping[str, Sequence[str]]


@dataclass(frozen=True)
class Scores:
    """One prediction set judged against a gold standard, with the counts behind it."""

    hits: int
    judged: int
    ignored: int
    expected: int
    precision: float
    recall: float
    f1: float
    mrr: float = 0.0
    hits_at_1: float = 0.0

    def as_dict(self) -> dict[str, float]:
        """Return the scores as plain numbers."""
        return asdict(self)

    def write(self, path: Path) -> None:
        """Write the scores as JSON."""
        path.write_text(json.dumps(self.as_dict(), indent=2, sort_keys=True), "utf-8")


def evaluate(rows: Sequence[SemanticMapping], gold: Iterable[Pair]) -> Scores:
    """Score already read rows against an already read gold standard."""
    found = {(row.subject.curie, row.object.curie) for row in rows}
    used = {side.partition(":")[0] for pair in found for side in pair}
    covered = {p for p in gold if {s.partition(":")[0] for s in p} <= used}
    if not covered:
        raise ValueError(f"the gold standard covers none of {sorted(used)}")
    return score(predicted=found, gold=covered, ranked=candidates(rows))


def candidates(rows: Iterable[SemanticMapping]) -> Ranked:
    """Group each subject's candidate objects, best confidence first."""
    found: dict[str, list[tuple[float, str]]] = {}
    for row in rows:
        found.setdefault(row.subject.curie, []).append(
            (row.confidence or 0.0, row.object.curie)
        )
    return {
        subject: [obj for _, obj in sorted(scored, key=lambda entry: -entry[0])]
        for subject, scored in found.items()
    }


def score(
    predicted: Iterable[Pair], gold: Iterable[Pair], ranked: Ranked | None = None
) -> Scores:
    """Score predictions over the entities the gold standard covers."""
    found = {_unordered(pair) for pair in predicted}
    wanted = {_unordered(pair) for pair in gold}
    covered = {side for pair in wanted for side in pair}
    judged = {pair for pair in found if covered & set(pair)}
    hits = len(judged & wanted)
    precision = hits / len(judged) if judged else 0.0
    recall = hits / len(wanted) if wanted else 0.0
    total = precision + recall
    f1 = 2 * precision * recall / total if total else 0.0
    ranks = list(_ranks(ranked or {}, gold))
    return Scores(
        hits=hits,
        judged=len(judged),
        ignored=len(found) - len(judged),
        expected=len(wanted),
        precision=precision,
        recall=recall,
        f1=f1,
        mrr=sum(1 / rank for rank in ranks if rank) / len(ranks) if ranks else 0.0,
        hits_at_1=sum(1 for rank in ranks if rank == 1) / len(ranks) if ranks else 0.0,
    )


def _ranks(ranked: Ranked, gold: Iterable[Pair]) -> Iterator[int]:
    """Yield each gold-covered subject's rank of its first correct object, 0 if none."""
    answers: dict[str, set[str]] = {}
    for subject, obj in gold:
        answers.setdefault(subject, set()).add(obj)
        answers.setdefault(obj, set()).add(subject)
    for subject, candidates in ranked.items():
        correct = answers.get(subject)
        if not correct:
            continue
        yield next((i for i, o in enumerate(candidates, 1) if o in correct), 0)


def _unordered(pair: Pair) -> Pair:
    """Sort a pair's two ids into a fixed order."""
    subject, obj = pair
    return (subject, obj) if subject <= obj else (obj, subject)
