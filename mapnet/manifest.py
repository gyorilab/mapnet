"""The central registry."""

from __future__ import annotations

from pathlib import Path

URLS = {
    "obo_release": (
        "http://purl.obolibrary.org/obo/{prefix}/releases/{version}/{prefix}.{fmt}"
    ),
    "zenodo_latest": "https://zenodo.org/api/records/{concept}/versions/latest",
    "zenodo_file": "https://zenodo.org/records/{record}/files/{filename}",
}

MAPPING_SET_BASE = "https://w3id.org/mapnet/mappings"

BIOMAPPINGS = (
    "https://raw.githubusercontent.com/biopragmatics/biomappings/main/"
    "src/biomappings/resources/{name}.sssom.tsv"
)

# Every evidence set mapnet knows, as what a match means and where the file comes from.
# obo is the run's own ontologies, zenodo: a concept resolved to its newest record.
SOURCES = {
    "biomappings": ("pairs", BIOMAPPINGS.format(name="positive")),
    "biomappings-negative": ("rejected", BIOMAPPINGS.format(name="negative")),
    "biomappings-predicted": ("predicted", BIOMAPPINGS.format(name="predictions")),
    "semra": ("pairs", "zenodo:11091885/processed.sssom.tsv.gz"),
    "obo-xref": ("pairs", "obo"),
}

# Evidence sets classify consults.
EVIDENCE = [
    "biomappings",
    "semra",
    "obo-xref",
    "biomappings-negative",
    "biomappings-predicted",
]

# Evidence names or ontology prefixes a bare `mapnet fetch` refetches.
REFRESH = ["biomappings", "biomappings-negative", "biomappings-predicted", "semra"]

# Gold standards fetched by name, each one table of pairs landing in data/gold.
GOLD: dict[str, str] = {
    "mp-hp-mgi": (
        "https://raw.githubusercontent.com/mapping-commons/mh_mapping_initiative/"
        "master/mappings/mp_hp_mgi_all.sssom.tsv"
    )
}

# Zenodo concept the run's own mapping sets are published under.
DEPOSITION = "zenodo:0000000"

# Where a run's files land, one directory per <root>/<tool>/<src>_<tgt>/<stamp>.
OUTPUT_ROOT = "outputs"

DATA_ROOT = Path("data")

RAW = "raw_mappings.sssom.tsv"

RUN_STAMP = "%Y%m%d_%H%M%S%f"

TOOLS = {
    "gilda": {
        "command": ["uv", "run", "--script", "gilda_utils.py"],
        "wants_format": "obo",
    },
    "leonmap": {
        "command": ["uv", "run", "--script", "leonmap_utils.py"],
        "wants_format": "owl",
    },
}
