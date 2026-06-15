"""
Aggregate predictions of mappings between MONDO and MesH from Gilda, LeonMap and LogMap
"""

import polars as pl
import os

if __name__ == "__main__":
    gilda_maps = pl.read_csv(
        "scripts/gilda_mondo_mesh_predictions.sssom.tsv",
        separator="\t",
        comment_prefix="#",
    )
    logmap_maps = pl.read_csv(
        "scripts/logmap_mondo_mesh_predictions.sssom.tsv",
        separator="\t",
        comment_prefix="#",
    ).drop('comment')
    leonmap_maps = pl.read_csv(
        "scripts/leonmap_mondo_mesh_classified/leonmap_mondo_mesh_novel.sssom.tsv",
        separator="\t",
        comment_prefix="#",
    )

    mapping_stack = pl.concat(
        [
            gilda_maps.with_columns(confidence=1.0).select(logmap_maps.columns),
            leonmap_maps.select(logmap_maps.columns),
            logmap_maps,
        ]
    )

    output_path = os.path.join(
        os.path.dirname(__file__), "aggregated_mondo_mesh_predictions.sssom.tsv"
    )
    with open(output_path, "w") as f:
        f.write("#curie_map:\n")
        f.write("#  mondo: http://purl.obolibrary.org/obo/MONDO_\n")
        f.write("#  mesh: https://meshb.nlm.nih.gov/record/ui?ui=\n")
        f.write("#  skos: http://www.w3.org/2004/02/skos/core#\n")
        f.write("#  semapv: https://w3id.org/semapv/vocab/\n")
        f.write(
            "#mapping_set_id: https://github.com/gyorilab/mapnet/blob/main/scripts/aggregated_mondo_mesh_predictions.sssom.tsv\n"
        )
        f.write("#mapping_tool: Gilda, logmap, LeonMap\n")
        mapping_stack.write_csv(f, separator="\t")
    print(f"Wrote {len(mapping_stack)} mappings to {output_path}")
