"""MapNet: an aggregator for biomedical ontology mapping tools."""

import importlib.metadata as md
import logging

from curies import Reference
from sssom_pydantic import SemanticMapping

from mapnet.classify import SETS, Evidence, Split, classify
from mapnet.data import Dataset, MapNet, downloads, get_source, get_version
from mapnet.eval import Scores, candidates, evaluate, score
from mapnet.manifest import DATA_ROOT, EVIDENCE, REFRESH
from mapnet.mapper import Mapper
from mapnet.matchers import Config, Result, aggregate, load_tools, match
from mapnet.sssom import prefixes, read, to_pairs, write
from mapnet.utils import check_prefixes, table, to_curie, to_prefix, to_reference

__version__ = md.version("mapnet")

__all__ = [
    "__version__",
    "aggregate",
    "candidates",
    "check_prefixes",
    "classify",
    "Config",
    "DATA_ROOT",
    "Dataset",
    "downloads",
    "evaluate",
    "EVIDENCE",
    "Evidence",
    "get_source",
    "get_version",
    "load_tools",
    "MapNet",
    "Mapper",
    "match",
    "prefixes",
    "read",
    "Reference",
    "REFRESH",
    "Result",
    "score",
    "Scores",
    "SemanticMapping",
    "SETS",
    "Split",
    "table",
    "to_curie",
    "to_pairs",
    "to_prefix",
    "to_reference",
    "write",
]

logging.getLogger(__name__).addHandler(logging.NullHandler())
