"""
Generate MONDO <-> MeSH disease mappings using LeonMap.

Downloads data if missing, builds collections if missing, runs mapping, then classifies predictions into right/wrong/novel using known mappings.

The classification task is offloaded to Mapnet using the utils module. 

Config: https://github.com/HarshitSoni1903/Weakly-Supervised-Representation-Learning-for-Cross-Ontology-Mapping/blob/main/leonmap/test_config.yaml

Install:
  requirements: PYTHON 3.10
  pip install git+https://github.com/HarshitSoni1903/Weakly-Supervised-Representation-Learning-for-Cross-Ontology-Mapping.git
  pip install indra gilda biomappings lxml obonet
"""
from __future__ import annotations

import csv
import gzip
import os
import shutil
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path
import re

import obonet
import pandas as pd
import polars as pl
from lxml import etree
from huggingface_hub import snapshot_download

from indra.databases import mesh_client
from mapnet.utils.filtering import load_semera_landscape_df, repair_names_with_semra, get_right_wrong_mappings
from mapnet.utils.utils import make_undirected
from biomappings.resources import PREDICTIONS_SSSOM_PATH, POSITIVES_SSSOM_PATH


LEONMAP_ROOT = "."
STUDY = "mondo_mesh"
CONFIG_YAML = None
CHECK_SEMRA = True
EXPORT_ALL = True

MONDO_OWL_URL = "http://purl.obolibrary.org/obo/mondo.owl"
MONDO_OBO_URL = "https://raw.githubusercontent.com/monarch-initiative/mondo/refs/heads/master/src/ontology/mondo-edit.obo"
MESH_OWL_GZ_URL = "https://w3id.org/biopragmatics/resources/mesh/mesh.owl.gz"
MESH_DISEASE_TREE_PREFIXES = ("C", "F03")
HF_MODEL_REPO = "harshitsoni1903/sapbert-finetuned-semra"

def _canonical(curie: str) -> str:
    ns, _, local = curie.partition(":")
    return f"{ns.lower()}:{local}"

_ROMAN = {"i":"1","ii":"2","iii":"3","iv":"4","v":"5","vi":"6","vii":"7","viii":"8","ix":"9","x":"10","xi":"11","xii":"12"}

def _norm_label(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"'s\b", "", s)
    tokens = re.findall(r"[a-z0-9]+", s)
    tokens = [_ROMAN.get(t, t) for t in tokens]
    return " ".join(sorted(tokens))

def download_mondo_owl(data_dir: Path) -> Path:
    out = data_dir / "mondo.owl"
    if out.exists():
        print(f"MONDO OWL exists, skipping: {out}")
        return out
    print(f"Downloading MONDO OWL -> {out}")
    urllib.request.urlretrieve(MONDO_OWL_URL, str(out))
    print(f"Done ({out.stat().st_size / 1024 / 1024:.1f} MB)")
    return out


#Download and filter the MeSH OWL to only disease descriptors using simple XML parsing.
def download_and_filter_mesh_owl(data_dir: Path) -> Path:
    out = data_dir / "mesh_disease.owl"
    if out.exists():
        print(f"MeSH disease OWL exists, skipping: {out}")
        return out

    print("Getting MeSH disease IDs from indra.mesh_client ...")
    keep_iris = set()
    for mesh_id in mesh_client.mesh_id_to_name:
        if any(mesh_client.has_tree_prefix(mesh_id, p) for p in MESH_DISEASE_TREE_PREFIXES):
            keep_iris.add(f"http://id.nlm.nih.gov/mesh/{mesh_id}")
    print(f"{len(keep_iris)} disease descriptors (tree {', '.join(MESH_DISEASE_TREE_PREFIXES)})")

    gz_path = data_dir / "mesh.owl.gz"
    owl_full = data_dir / "mesh.owl"
    print("Downloading full MeSH OWL ...")
    urllib.request.urlretrieve(MESH_OWL_GZ_URL, str(gz_path))
    with gzip.open(str(gz_path), "rb") as f_in, open(str(owl_full), "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    gz_path.unlink()

    print("Filtering to disease subset ...")
    RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    OWL = "http://www.w3.org/2002/07/owl#"
    parser = etree.XMLParser(remove_comments=False, huge_tree=True)
    tree = etree.parse(str(owl_full), parser)
    root = tree.getroot()
    new_root = etree.Element(root.tag, nsmap=root.nsmap)
    children = list(root)
    kept = i = 0
    while i < len(children):
        node = children[i]
        if node.tag in (f"{{{OWL}}}Ontology", f"{{{OWL}}}AnnotationProperty"):
            new_root.append(node); i += 1; continue
        if isinstance(node, etree._Comment):
            if i + 1 < len(children) and children[i+1].get(f"{{{RDF}}}about") in keep_iris:
                new_root.append(node); new_root.append(children[i+1]); kept += 1
            i += 2; continue
        if node.get(f"{{{RDF}}}about") in keep_iris:
            new_root.append(node); kept += 1
        i += 1
    etree.ElementTree(new_root).write(str(out), encoding="utf-8", xml_declaration=True, pretty_print=True)
    print(f"Kept {kept} blocks -> {out}")
    return out

def _run(entry_main, cli_name: str, argv: list[str]) -> None:
    old = sys.argv
    sys.argv = [cli_name] + argv
    try: entry_main()
    finally: sys.argv = old

#Conver the TSV generated to DF expected by Mapnet Utils.
def _mapper_tsv_to_mapnet_df(mapper_tsv: Path) -> pl.DataFrame:
    rows = []
    with open(mapper_tsv, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if r["src_label"].startswith("obsolete ") or r["tgt_label"].startswith("obsolete "):
                continue
            rows.append({
                "source identifier": _canonical(r["src_id"]),
                "source name": r["src_label"],
                "source prefix": r["src_id"].split(":")[0].lower(),
                "target identifier": _canonical(r["tgt_id"]),
                "target name": r["tgt_label"],
                "target prefix": r["tgt_id"].split(":")[0].lower(),
                "confidence": float(r["score"]),
                "remarks": r.get("remarks", ""),
            })
    return pl.DataFrame(rows)

def _load_obo_xrefs() -> tuple[pl.DataFrame, dict[str, set[str]], dict[str, set[str]]]:
    records = []
    mondo_to_mesh = defaultdict(set)
    mesh_to_mondo = defaultdict(set)
    g = obonet.read_obo(MONDO_OBO_URL)
    for node in g:
        if not node.startswith("MONDO"):
            continue
        nd = g.nodes[node]
        if not nd:
            continue
        mondo_id = f"mondo:{node.split(':')[1]}"
        for xref in nd.get("xref", []):
            if not xref.startswith("MESH"):
                continue
            mid = xref.split(":")[1]
            mesh_id = f"mesh:{mid}"
            mondo_to_mesh[mondo_id].add(mesh_id)
            mesh_to_mondo[mesh_id].add(mondo_id)
            records.append({
                "source identifier": mondo_id,
                "source name": nd.get("name", ""),
                "source prefix": "mondo",
                "target identifier": mesh_id,
                "target name": mesh_client.get_mesh_name(mid, offline=True) or "",
                "target prefix": "mesh",
            })
    print(f"  {len(records)} MONDO->MeSH xrefs from OBO")
    return pl.DataFrame(records), dict(mondo_to_mesh), dict(mesh_to_mondo)


def _load_biomappings_sssom() -> pl.DataFrame:
    records = []
    for path in [POSITIVES_SSSOM_PATH, PREDICTIONS_SSSOM_PATH]:
        try:
            df = pd.read_csv(path, comment="#", sep="\t")
        except Exception as e:
            print(f"  Warning: could not load {path}: {e}"); continue
        for _, row in df.iterrows():
            s, o = _canonical(str(row.get("subject_id", ""))), _canonical(str(row.get("object_id", "")))
            sl, ol = str(row.get("subject_label", "")), str(row.get("object_label", ""))
            if s.startswith("mondo:") and o.startswith("mesh:"):
                records.append({"source identifier": s, "source name": sl, "source prefix": "mondo", "target identifier": o, "target name": ol, "target prefix": "mesh"})
            elif s.startswith("mesh:") and o.startswith("mondo:"):
                records.append({"source identifier": o, "source name": ol, "source prefix": "mondo", "target identifier": s, "target name": sl, "target prefix": "mesh"})
    print(f"  {len(records)} from biomappings SSSOM files")
    return pl.DataFrame(records)


def _write_sssom(df: pl.DataFrame, out_path: Path, mapping_set_id: str) -> None:
    has_pred = "predicted identifier" in df.columns
    rows = []
    for r in df.iter_rows(named=True):
        remarks = r.get("remarks", "")
        obj_id = r["predicted identifier"] if has_pred else r["target identifier"]
        obj_label = r.get("predicted name", r.get("target name", "")) if has_pred else r["target name"]
        if "predicate id" in df.columns and r.get("predicate id"):
            predicate = r["predicate id"]
            justification = "semapv:LexicalMatching" if (r.get("source name", "") or "").lower() == (obj_label or "").lower() else "semapv:SemanticSimilarity"
        elif "cosine=" in remarks:
            justification = "semapv:LexicalMatching"
            predicate = "skos:exactMatch" if r["source name"].lower() == obj_label.lower() else "skos:broadMatch"
        else:
            justification = "semapv:SemanticSimilarity"
            predicate = "skos:exactMatch"
        comment_parts = []
        if remarks:
            comment_parts.append(remarks)
        if has_pred:
            comment_parts.append(f"known_target={r.get('true identifier', '')}")
        entry = {"subject_id": r["source identifier"], "subject_label": r["source name"],
                 "predicate_id": predicate, "object_id": obj_id, "object_label": obj_label,
                 "confidence": str(r["confidence"]), "mapping_justification": justification,
                 "mapping_tool": "leonmap", "comment": "; ".join(comment_parts)}
        rows.append(entry)
    with open(out_path, "w") as f:
        f.write("#curie_map:\n#  mondo: http://purl.obolibrary.org/obo/MONDO_\n")
        f.write("#  mesh: https://meshb.nlm.nih.gov/record/ui?ui=\n")
        f.write("#  skos: http://www.w3.org/2004/02/skos/core#\n")
        f.write("#  semapv: https://w3id.org/semapv/vocab/\n")
        f.write(f"#mapping_set_id: https://github.com/gyorilab/mapnet/blob/main/scripts/leonmap_mondo_mesh_classified/{mapping_set_id}.sssom.tsv\n#mapping_tool: leonmap\n")
    pd.DataFrame(rows).to_csv(out_path, sep="\t", index=False, mode="a")
    print(f"Wrote {len(rows)} mappings -> {out_path}")


def _filter_novel(novel: pl.DataFrame, mondo_to_mesh: dict[str, set[str]], mesh_to_mondo: dict[str, set[str]]) -> tuple[pl.DataFrame, pl.DataFrame]:
    truly_novel_rows, false_novel_rows = [], []
    for r in novel.iter_rows(named=True):
        src, tgt = r["source identifier"], r["target identifier"]
        known_mesh = mondo_to_mesh.get(src, set())
        known_mondo = mesh_to_mondo.get(tgt, set())
        if not known_mesh and not known_mondo:
            truly_novel_rows.append(r)
        else:
            reason = []
            if known_mesh: reason.append(f"mondo_maps_to:{','.join(sorted(known_mesh))}")
            if known_mondo: reason.append(f"mesh_maps_to:{','.join(sorted(known_mondo))}")
            r_aug = dict(r)
            r_aug["predicted identifier"] = tgt
            r_aug["predicted name"] = r["target name"]
            r_aug["true identifier"] = " | ".join(reason)
            r_aug["true name"] = ""
            false_novel_rows.append(r_aug)
    truly_novel = pl.DataFrame(truly_novel_rows) if truly_novel_rows else pl.DataFrame()
    false_novel = pl.DataFrame(false_novel_rows) if false_novel_rows else pl.DataFrame()
    return truly_novel, false_novel

def _load_owl_hierarchy(owl_path: str, id_prefixes: list[str] | None = None) -> dict:
    """
    Returns {curie: {"parents": [curies], "children": [curies]}}.
    Walks rdfs:subClassOf, canonicalizes IRIs to match VDB id2pos keys.
    """
    from rdflib import Graph, URIRef, RDFS
    from leonmap.utils import canonicalize_id, normalize_prefix

    g = Graph()
    g.parse(owl_path)

    norm_prefixes = [normalize_prefix(p) for p in id_prefixes] if id_prefixes else None

    def iri_to_curie(iri: str) -> str:
        s = str(iri)
        tail = s.split("#")[-1].rsplit("/", 1)[-1].strip()
        if not tail:
            return ""
        if "id.nlm.nih.gov/mesh/" in s or "obo/mesh#" in s or "purl.obolibrary.org/obo/mesh" in s:
            return canonicalize_id(f"mesh:{tail}")
        return canonicalize_id(tail)

    parents: dict[str, set] = {}
    children: dict[str, set] = {}
    for s, _, o in g.triples((None, RDFS.subClassOf, None)):
        if not isinstance(s, URIRef) or not isinstance(o, URIRef):
            continue
        c, p = iri_to_curie(str(s)), iri_to_curie(str(o))
        if not c or not p:
            continue
        if norm_prefixes and not (any(c.startswith(x) for x in norm_prefixes) and any(p.startswith(x) for x in norm_prefixes)):
            continue
        parents.setdefault(c, set()).add(p)
        children.setdefault(p, set()).add(c)

    all_ids = set(parents) | set(children)
    return {cid: {"parents": sorted(parents.get(cid, set())),
                  "children": sorted(children.get(cid, set()))} for cid in all_ids}


def _classify_pair(src_id, src_label, tgt_id, tgt_label, src_db, tgt_db, src_hier, tgt_hier) -> str:
    """Per-pair predicate classification: exact / narrow / broad / close."""
    if src_label and tgt_label and _norm_label(src_label) == _norm_label(tgt_label):
        return "skos:exactMatch"

    def vec(db, cid):
        pos = db.id2pos.get(cid)
        return None if pos is None else db.reconstruct(pos)

    sv, tv = vec(src_db, src_id), vec(tgt_db, tgt_id)
    if sv is None or tv is None:
        return "skos:closeMatch"

    NEG = float("-inf")
    def maxcos(qv, db, ids):
        best = NEG
        for cid in ids:
            v = vec(db, cid)
            if v is None:
                continue
            best = max(best, float(qv @ v))
        return best

    direct = float(sv @ tv)
    tgt_p_ids = tgt_hier.get(tgt_id, {}).get("parents", [])
    tgt_c_ids = tgt_hier.get(tgt_id, {}).get("children", [])
    src_p_ids = src_hier.get(src_id, {}).get("parents", [])
    src_c_ids = src_hier.get(src_id, {}).get("children", [])

    scores = {
        "direct":     direct,
        "tgt_parent": maxcos(sv, tgt_db, tgt_p_ids),
        "tgt_child":  maxcos(sv, tgt_db, tgt_c_ids),
        "src_parent": maxcos(tv, src_db, src_p_ids),
        "src_child":  maxcos(tv, src_db, src_c_ids),
    }
    winner = max(scores, key=scores.get)

    if winner == "direct":
        return "skos:exactMatch"
    if winner in ("tgt_parent", "src_child"):
        return "skos:narrowMatch"
    if winner in ("tgt_child", "src_parent"):
        return "skos:broadMatch"
    return "skos:closeMatch"


def reclassify_predicates(df: pl.DataFrame) -> pl.DataFrame:
    """Adds 'predicate id' column to df by classifying each (src, tgt) pair."""
    if len(df) == 0:
        return df.with_columns(pl.lit("").alias("predicate id"))

    from leonmap.config import BuildConfig, COLLECTIONS, MAPPINGS, resolve_path
    from leonmap.utils import load_collection

    cfg = BuildConfig()
    study = MAPPINGS[STUDY]
    src_name, tgt_name = study["src_collection"], study["tgt_collection"]
    src_db = load_collection(cfg, src_name)
    tgt_db = load_collection(cfg, tgt_name)

    src_spec, tgt_spec = COLLECTIONS[src_name], COLLECTIONS[tgt_name]
    src_owl = resolve_path(cfg.data_dir) / src_spec["owl_path"]
    tgt_owl = resolve_path(cfg.data_dir) / tgt_spec["owl_path"]

    print(f"  Loading hierarchy: {src_name} from {src_owl.name}")
    src_hier = _load_owl_hierarchy(str(src_owl), src_spec.get("id_prefixes"))
    print(f"  Loading hierarchy: {tgt_name} from {tgt_owl.name}")
    tgt_hier = _load_owl_hierarchy(str(tgt_owl), tgt_spec.get("id_prefixes"))
    print(f"  {len(src_hier)} {src_name} / {len(tgt_hier)} {tgt_name} concepts have hierarchy")

    preds = []
    for r in df.iter_rows(named=True):
        preds.append(_classify_pair(
            r["source identifier"], r["source name"],
            r["target identifier"], r["target name"],
            src_db, tgt_db, src_hier, tgt_hier,
        ))
    counts = {p: preds.count(p) for p in set(preds)}
    print(f"  Predicates: {counts}")
    return df.with_columns(pl.Series("predicate id", preds))

def classify_mappings(predictions_df: pl.DataFrame, output_dir: Path, check_semra: bool = False, export_all: bool = True) -> None:
    evidence, mondo_to_mesh, mesh_to_mondo = _load_obo_xrefs()
    bio_evidence = _load_biomappings_sssom()
    for r in bio_evidence.iter_rows(named=True):
        s, t = r["source identifier"], r["target identifier"]
        if s.startswith("mondo:") and t.startswith("mesh:"):
            mondo_to_mesh.setdefault(s, set()).add(t)
            mesh_to_mondo.setdefault(t, set()).add(s)
    evidence = evidence.vstack(bio_evidence)

    if check_semra:
        semra_df = load_semera_landscape_df(
            landscape_name="disease", resources={"mondo": {}, "mesh": {}},
            additional_namespaces={"mondo": "mondo", "mesh": "mesh"}, sssom=False)
        predictions_df = repair_names_with_semra(predictions_df, semra_df)
        for r in semra_df.iter_rows(named=True):
            s, t = r["source identifier"], r["target identifier"]
            if s.startswith("mondo:") and t.startswith("mesh:"):
                mondo_to_mesh.setdefault(s, set()).add(t)
                mesh_to_mondo.setdefault(t, set()).add(s)
            elif s.startswith("mesh:") and t.startswith("mondo:"):
                mondo_to_mesh.setdefault(t, set()).add(s)
                mesh_to_mondo.setdefault(s, set()).add(t)
        evidence = evidence.vstack(semra_df)

    evidence = make_undirected(evidence.unique())
    print(f"  {len(evidence)} total evidence pairs (undirected)")
    print(f"  {len(mondo_to_mesh)} MONDO with MeSH, {len(mesh_to_mondo)} MeSH with MONDO")

    no_name = predictions_df.filter(
        (pl.col("source name").eq("NO_NAME_FOUND")) |
        (pl.col("target name").eq("NO_NAME_FOUND"))
    )
    if len(no_name) > 0:
        output_dir.mkdir(parents=True, exist_ok=True)
        no_name.write_csv(str(output_dir / "maps_with_no_names.tsv"), separator="\t")
        print(f"  {len(no_name)} predictions with missing names saved separately")
    predictions_df = predictions_df.filter(
        ~((pl.col("source name").eq("NO_NAME_FOUND")) |
          (pl.col("target name").eq("NO_NAME_FOUND")))
    )

    right, wrong, novel = get_right_wrong_mappings(predictions_df, evidence)

    remarks_lookup = predictions_df.select(["source identifier", "target identifier", "remarks"])
    join_cols = ["source identifier", "target identifier"]
    right = right.join(remarks_lookup, on=join_cols, how="left")
    novel = novel.join(remarks_lookup, on=join_cols, how="left")
    wrong_join_cols = ["source identifier"]
    wrong = wrong.join(
        remarks_lookup.rename({"target identifier": "predicted identifier"}),
        on=["source identifier", "predicted identifier"], how="left")

    output_dir.mkdir(parents=True, exist_ok=True)
    base = f"leonmap_{STUDY}"

    truly_novel, false_novel = _filter_novel(novel, mondo_to_mesh, mesh_to_mondo)
    if len(false_novel) > 0 and len(wrong) > 0:
        false_novel = false_novel.select(wrong.columns)
        wrong = wrong.vstack(false_novel)
    elif len(false_novel) > 0:
        wrong = false_novel
    print(f"  Post-hoc: {len(truly_novel)} truly novel, {len(false_novel)} reclassified to wrong")

    truly_novel = reclassify_predicates(truly_novel)
    _write_sssom(truly_novel, output_dir / f"{base}_novel.sssom.tsv", f"{base}_novel")
    if export_all:
        _write_sssom(right, output_dir / f"{base}_right.sssom.tsv", f"{base}_right")
        _write_sssom(wrong, output_dir / f"{base}_wrong.sssom.tsv", f"{base}_wrong")
    print(f"Classification: {len(right)} right, {len(wrong)} wrong, {len(truly_novel)} novel")
    print(f"Files -> {output_dir}/")


if __name__ == "__main__":
    root = Path(os.path.abspath(LEONMAP_ROOT))

    import leonmap.config as _cfg
    _cfg.PROJECT_ROOT = root

    if CONFIG_YAML:
        from leonmap.config_loader import load_user_config
        load_user_config(CONFIG_YAML)
    from leonmap.config import BuildConfig, MAPPINGS, COLLECTIONS, resolve_path
    from leonmap.build_vdb import main as build_main
    from leonmap.mapper import main as mapper_main

    if STUDY not in MAPPINGS:
        raise SystemExit(f"Unknown study: {STUDY}. Available: {sorted(MAPPINGS.keys())}")

    study = MAPPINGS[STUDY]
    cfg = BuildConfig()
    data_dir = resolve_path(cfg.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    model_dir = resolve_path(cfg.ft_model_path)
    if not model_dir.exists():
        print(f"Model not found locally, downloading from HF: {HF_MODEL_REPO}")
        snapshot_download(repo_id=HF_MODEL_REPO, local_dir=str(model_dir))
        print(f"Model downloaded -> {model_dir}")

    src_owl = COLLECTIONS[study["src_collection"]]["owl_path"]
    tgt_owl = COLLECTIONS[study["tgt_collection"]]["owl_path"]
    if "mondo.owl" in (src_owl, tgt_owl):
        download_mondo_owl(data_dir)
    if "mesh_disease.owl" in (src_owl, tgt_owl):
        download_and_filter_mesh_owl(data_dir)

    cols = [study["src_collection"], study["tgt_collection"]]
    _run(build_main, "leonmap-build", ["--collections"] + cols)

    map_argv = ["--study", STUDY]
    if CONFIG_YAML:
        map_argv += ["--config", CONFIG_YAML]
    _run(mapper_main, "leonmap-map", map_argv)

    results_dir = resolve_path("mapper_results") / STUDY
    latest_run = max(results_dir.glob("run_*"), key=lambda p: p.name)
    mapper_tsv = latest_run / "mondo_to_mesh.tsv"
    if mapper_tsv.exists():
        predictions_df = _mapper_tsv_to_mapnet_df(mapper_tsv)
        classify_mappings(predictions_df, root / f"leonmap_{STUDY}_classified", check_semra=CHECK_SEMRA, export_all=EXPORT_ALL)

    print(f"Done. Results in: {results_dir}/")