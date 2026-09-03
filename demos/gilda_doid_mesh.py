# /// script
# requires-python = ">=3.11"
# dependencies = ["mapnet", "gilda", "obonet", "indra"]
#
# [tool.uv.sources]
# mapnet = { path = "..", editable = true }
# ///
"""Map DOID to MeSH with gilda, taking the source ontology from a URL."""

from pathlib import Path

import mapnet
from adapters.gilda_utils import GildaMapper

space = mapnet.MapNet(workdir=Path("."))
dataset = mapnet.Dataset(
    src="http://purl.obolibrary.org/obo/doid.obo",
    tgt="mesh",
    gold="biomappings",
    evidence=mapnet.EVIDENCE,
    mapnet=space,
)
result = GildaMapper(dataset=dataset).run()
split = result.classify()
scores = result.evaluate()

print(f"\n{result.directory}")
for name, rows in split.sets():
    print(f"  {name:10} {len(rows):6}")

print(f"\nagainst {dataset.gold}")
for name, value in scores.as_dict().items():
    print(f"  {name:10} {value:g}")
