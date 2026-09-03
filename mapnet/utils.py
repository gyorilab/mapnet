"""Shared helpers used across every other module."""

from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from functools import cache
from itertools import islice
from pathlib import Path
from urllib.parse import urlparse

import bioregistry
import curies
from curies import NamableReference

SAMPLE = 200

TABLES = {".tsv": "\t", ".csv": ","}

ONTOLOGY_LINE = re.compile(r"^ontology:\s*(\S+)", re.MULTILINE)

ONTOLOGY_IRI = re.compile(r'<owl:Ontology rdf:about="([^"]+)"')


@cache
def converter() -> curies.Converter:
    """Build the bioregistry converter."""
    return bioregistry.get_converter()


def header(path: Path) -> str:
    """Read the opening lines, where OBO and OWL files declare their metadata."""
    with path.open(encoding="utf-8", errors="replace") as handle:
        return "".join(islice(handle, SAMPLE))


def table(path: Path) -> Iterator[dict[str, str]]:
    """Yield each row of a TSV or CSV, skipping the comments SSSOM writes above it."""
    if path.suffix not in TABLES:
        raise ValueError(f"{path.name} is not one of {sorted(TABLES)}")
    with path.open(encoding="utf-8") as handle:
        body = (line for line in handle if not line.startswith("#"))
        yield from csv.DictReader(body, delimiter=TABLES[path.suffix])


def to_prefix(path: Path) -> str:
    """Read the prefix a file declares in its header, or the one leading its ids."""
    if path.suffix in TABLES:
        ids = (row.get("id") or "" for row in islice(table(path), SAMPLE))
        seen = count_prefixes(ids)
        if not seen:
            raise ValueError(f"{path.name} has no prefixed ids to read a prefix from")
        return seen.most_common(1)[0][0]
    head = header(path)
    for pattern in (ONTOLOGY_LINE, ONTOLOGY_IRI):
        found = pattern.search(head)
        if found:
            # Either a bare word, uberon/basic, or a release IRI ending in hp.owl.
            text = found.group(1).strip()
            if text.startswith(("http://", "https://")):
                text = Path(urlparse(text).path).name
            return text.split(".")[0].split("/")[0].lower()
    raise ValueError(f"{path.name} declares no ontology prefix in its header")


def count_prefixes(values: Iterable[str]) -> Counter[str]:
    """Count the prefix each id carries, ignoring any that names none."""
    seen: Counter[str] = Counter()
    for value in values:
        try:
            seen[to_curie(value).partition(":")[0]] += 1
        except ValueError:
            continue
    return seen


def check_prefixes(path: Path, prefix: str, nodes: Iterable[str]) -> None:
    """Warn when the file carries prefixes other than the one being mapped."""
    seen = count_prefixes(nodes)
    others = [f"{name} ({n})" for name, n in seen.most_common(6) if name != prefix]
    if not others:
        return
    print(
        f"[prefix] {path.name}: mapping {prefix} ({seen[prefix]} terms), "
        f"ignoring {', '.join(others[:5])}. "
        f"Pass --src-prefix or --tgt-prefix to choose another.",
        file=sys.stderr,
    )


@cache
def _normalize(head: str) -> str | None:
    """Normalize one CURIE prefix."""
    return bioregistry.normalize_prefix(head)


def to_curie(value: str) -> str:
    """Turn an IRI or a CURIE into a normalized CURIE."""
    text = value.strip()
    head, sep, local = text.partition(":")
    if text.startswith(("http://", "https://")):
        curie = converter().compress(text)
    elif sep and (prefix := _normalize(head)):
        curie = f"{prefix}:{local}"
    else:
        curie = bioregistry.normalize_curie(text)
    if curie is None:
        raise ValueError(f"cannot normalize {value!r} to a known prefix")
    return curie


def to_reference(value: str, name: str | None = None) -> NamableReference:
    """Turn an IRI or a CURIE into a normalized reference."""
    return NamableReference.from_curie(to_curie(value), name=name)
