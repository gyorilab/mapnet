"""Combine and classify predictions against curated evidence."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from sys import stderr

from sssom_pydantic import SemanticMapping

from mapnet.data import MapNet
from mapnet.manifest import SOURCES
from mapnet.sssom import to_pairs

SETS = ("right", "wrong", "novel", "conflicts")

KINDS = ("pairs", "rejected", "predicted")


@dataclass(frozen=True)
class Evidence:
    """The pair sets a candidate is judged against, and the files behind them."""

    pairs: set[tuple[str, str]] = field(default_factory=set)
    rejected: set[tuple[str, str]] = field(default_factory=set)
    predicted: set[tuple[str, str]] = field(default_factory=set)
    mapped: dict[str, set[str]] = field(default_factory=dict)
    sources: dict[str, list[tuple[Path, int]]] = field(default_factory=dict)

    @classmethod
    def load(
        cls, names: Iterable[str], prefixes: Iterable[str], mapnet: MapNet | None = None
    ) -> Evidence:
        """Resolve evidence names to files, one pair set per kind."""
        space = mapnet or MapNet()
        files: dict[str, list[Path]] = {kind: [] for kind in KINDS}
        for entry in [names] if isinstance(names, str) else names:
            tagged, name = _tagged(entry)
            kind, source = SOURCES.get(name, ("pairs", name))
            kind = tagged or kind
            if kind not in files:
                raise ValueError(
                    f"{name!r} has unknown kind {kind!r}, expected {KINDS}"
                )
            if source == "obo":
                for prefix in prefixes:
                    try:
                        files[kind].append(space.fetch(name=prefix))
                    except ValueError as error:
                        print(f"[evidence] no xrefs for {prefix}: {error}", file=stderr)
            elif Path(name).is_file():
                files[kind].append(Path(name))
            elif name in SOURCES:
                files[kind].append(space.fetch(name=name))
            else:
                raise ValueError(f"{name!r} is not a file or one of {sorted(SOURCES)}")
        sets, sources = _pair_sets(files)
        mapped: dict[str, set[str]] = defaultdict(set)
        for subject, obj in sets["pairs"]:
            mapped[subject].add(obj.split(":")[0])
        return cls(
            pairs=sets["pairs"],
            rejected=sets["rejected"],
            predicted=sets["predicted"],
            mapped=dict(mapped),
            sources=sources,
        )


@dataclass(frozen=True)
class Split:
    """The four sets, the evidence behind them, and what a prediction alone rescued."""

    right: list[SemanticMapping]
    wrong: list[SemanticMapping]
    novel: list[SemanticMapping]
    conflicts: list[SemanticMapping]
    evidence: Evidence
    prefixes: Sequence[str]
    rescued: int

    def sets(self) -> Iterator[tuple[str, list[SemanticMapping]]]:
        """Yield each set's name and its rows."""
        for name in SETS:
            yield name, getattr(self, name)


def _tagged(entry: str) -> tuple[str, str]:
    """Split a leading `rejected:` or `predicted:` kind off an evidence name."""
    head, sep, rest = entry.partition(":")
    return (head, rest) if sep and head in KINDS else ("", entry)


def _pair_sets(
    files: dict[str, list[Path]],
) -> tuple[dict[str, set[tuple[str, str]]], dict[str, list[tuple[Path, int]]]]:
    """Read each kind's files into one pair set, counting what every file gave."""
    sets: dict[str, set[tuple[str, str]]] = {}
    sources: dict[str, list[tuple[Path, int]]] = {}
    for kind, paths in files.items():
        found: set[tuple[str, str]] = set()
        sources[kind] = []
        for path in paths:
            pairs = to_pairs([path])
            if not pairs:
                print(f"[evidence] {path} yielded no pairs", file=stderr)
            sources[kind].append((path, len(pairs) // 2))
            found |= pairs
        sets[kind] = found
    return sets, sources


def classify(
    rows: Sequence[SemanticMapping],
    evidence: Evidence,
    prefixes: Sequence[str],
    reverse: Sequence[SemanticMapping] = (),
) -> Split:
    """Split already read candidates against already loaded evidence."""
    used = list(prefixes)
    sets: dict[str, list[SemanticMapping]] = {name: [] for name in SETS}
    for row in rows:
        sets[_bucket(row, evidence)].append(row)
    survived = {(row.object.curie, row.subject.curie) for row in reverse}
    novel, conflicts = reduce(sets["novel"], survived)
    rescued = sum(
        (row.subject.curie, row.object.curie) not in evidence.pairs
        for row in sets["right"]
    )
    return Split(
        sets["right"], sets["wrong"], novel, conflicts, evidence, used, rescued
    )


def reduce(
    rows: Sequence[SemanticMapping], reverse: set[tuple[str, str]] | None = None
) -> tuple[list[SemanticMapping], list[SemanticMapping]]:
    """Keep one candidate per subject and object, cascading as rivals are eliminated."""
    survived = reverse or set()
    pool = list(rows)
    won: set[int] = set()
    while pool:
        by_subject, by_object = _groups(pool)
        winners = [
            row
            for row in pool
            if _wins(row, by_subject[row.subject.curie], survived)
            and _wins(row, by_object[row.object.curie], survived)
        ]
        if not winners:
            break
        won.update(id(row) for row in winners)
        subjects = {row.subject.curie for row in winners}
        objects = {row.object.curie for row in winners}
        pool = [
            row
            for row in pool
            if id(row) not in won
            and row.subject.curie not in subjects
            and row.object.curie not in objects
        ]
    kept = [row for row in rows if id(row) in won]
    return kept, [row for row in rows if id(row) not in won]


def _groups(
    rows: Sequence[SemanticMapping],
) -> tuple[dict[str, list[SemanticMapping]], dict[str, list[SemanticMapping]]]:
    """Index rows by their subject and by their object."""
    by_subject: dict[str, list[SemanticMapping]] = defaultdict(list)
    by_object: dict[str, list[SemanticMapping]] = defaultdict(list)
    for row in rows:
        by_subject[row.subject.curie].append(row)
        by_object[row.object.curie].append(row)
    return by_subject, by_object


def _bucket(row: SemanticMapping, evidence: Evidence) -> str:
    """Name the bucket one candidate belongs in, judged within its own prefix pair."""
    subject, obj = row.subject.curie, row.object.curie
    if (subject, obj) in evidence.rejected:
        return "wrong"
    if (subject, obj) in evidence.pairs:
        return "right"
    mapped_subject = row.object.prefix in evidence.mapped.get(subject, ())
    mapped_object = row.subject.prefix in evidence.mapped.get(obj, ())
    if mapped_subject or mapped_object:
        return "wrong"
    return "right" if (subject, obj) in evidence.predicted else "novel"


def _wins(
    row: SemanticMapping, group: list[SemanticMapping], reverse: set[tuple[str, str]]
) -> bool:
    """Whether the row is its group's single survivor, by confidence then reverse."""
    if len(group) == 1:
        return True
    best = max(other.confidence or 0.0 for other in group)
    top = [other for other in group if (other.confidence or 0.0) == best]
    if len(top) == 1:
        return top[0] is row
    survivors = [o for o in top if (o.subject.curie, o.object.curie) in reverse]
    return len(survivors) == 1 and survivors[0] is row
