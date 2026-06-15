"""
Aggregate predictions of mappings between MONDO and MeSH from Gilda, LeonMap and LogMap. These files can be recreated by running:
- `scripts/generate_mondo_mesh_mappings.py` to produce Gilda mappings
- `scripts/generate_mondo_logmap_maps.py` to produce LogMpa Mappings
- `scripts/generate_leonmap_mondo_mesh_mapping.py` to produce LeonMpa mappings

"""

import polars as pl
from datetime import datetime
from pathlib import Path

SSSOM_HEADER = {
    "curie_map": None,
    "mapping_set_id": "https://github.com/gyorilab/mapnet/blob/main/scripts/aggregated_mondo_mesh_predictions.sssom.tsv",
    "mapping_tool": "MapNet",
    "mapping_tool_version": "https://github.com/gyorilab/mapnet/blob/main/scripts/aggregate_mondo_mesh_predictions.py",
}

CURIE_MAP = {
    "mondo": "http://purl.obolibrary.org/obo/MONDO_",
    "mesh": "https://meshb.nlm.nih.gov/record/ui?ui=",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "semapv": "https://w3id.org/semapv/vocab/",
}


def write_sssom_header(f, mapping_date: str) -> None:
    f.write("#curie_map:\n")
    for prefix, uri in CURIE_MAP.items():
        f.write(f"#  {prefix}: {uri}\n")
    for key, value in SSSOM_HEADER.items():
        if key == "curie_map":
            continue
        f.write(f"#{key}: {value}\n")
    f.write(f"#mapping_date: {mapping_date}\n")


if __name__ == "__main__":
    script_dir = Path(__file__).parent

    ## load in the original mapping dataframes ##
    logmap_maps = pl.read_csv(
        script_dir / "logmap_mondo_mesh_predictions.sssom.tsv",
        separator="\t",
        comment_prefix="#",
    ).drop("comment")

    gilda_maps = (
        pl.read_csv(
            script_dir / "gilda_mondo_mesh_predictions.sssom.tsv",
            separator="\t",
            comment_prefix="#",
        )
        .with_columns(confidence=1.0)
        .select(logmap_maps.columns)
    )

    leonmap_maps = pl.read_csv(
        script_dir / "leonmap_mondo_mesh_classified/leonmap_mondo_mesh_novel.sssom.tsv",
        separator="\t",
        comment_prefix="#",
    )

    lexical_sources = (
        pl.concat([gilda_maps, logmap_maps])
        .group_by(["subject_id", "object_id"])
        .agg(pl.all().sort_by("confidence").last())
    )
    canonical_cols = lexical_sources.columns

    ## get mappings produced with only lexical methods ##
    lexical_mappings = lexical_sources.join(
        leonmap_maps,
        on=["subject_id", "object_id"],
        how="anti",
    ).with_columns(mapping_justification=pl.lit("semapv:LexicalMatching"))

    ## get mappings that have both lexical and semantic justification ##
    composite_mappings = lexical_sources.join(
        leonmap_maps,
        on=["subject_id", "object_id"],
        how="inner",
    ).with_columns(mapping_justification=pl.lit("semapv:CompositeMatching"))

    ## get mappings with only semantic justification ##
    semantic_mappings = leonmap_maps.join(
        lexical_sources,
        on=["subject_id", "object_id"],
        how="anti",
    ).with_columns(
        mapping_justification=pl.lit("semapv:SemanticSimilarityThresholdMatching")
    )

    ## stack and write out the mappings ##
    mapping_stack = pl.concat(
        [
            lexical_mappings.select(canonical_cols),
            composite_mappings.select(canonical_cols),
            semantic_mappings.select(canonical_cols),
        ]
    ).with_columns(mapping_tool=pl.lit("MapNet"))

    output_path = script_dir / "aggregated_mondo_mesh_predictions.sssom.tsv"
    with open(output_path, "w") as f:
        write_sssom_header(f, datetime.today().strftime("%Y-%m-%d"))
        mapping_stack.sort(by=["subject_id", "predicate_id", "object_id"]).write_csv(
            f, separator="\t"
        )

    print(f"Wrote {len(mapping_stack)} mappings to {output_path}")
