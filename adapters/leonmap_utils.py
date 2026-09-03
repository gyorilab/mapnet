# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "mapnet",
#     "leonmap @ git+https://github.com/HarshitSoni1903/Weakly-Supervised-Representation-Learning-for-Cross-Ontology-Mapping.git",
# ]
#
# [tool.uv.sources]
# mapnet = { path = "..", editable = true }
# ///
"""Run the LeonMap embedding matcher over two ontologies."""

import importlib.metadata as md
import sys

from mapnet import Mapper, SemanticMapping, table, to_reference

EXACT = to_reference("skos:exactMatch")
CLOSE = to_reference("skos:closeMatch")
LEXICAL = to_reference("semapv:LexicalMatching")
SEMANTIC = to_reference("semapv:SemanticSimilarityThresholdMatching")


class LeonMapMapper(Mapper):
    name = "leonmap"
    version = md.version("leonmap")

    def __init__(
        self,
        dataset,
        config=None,
        threshold=0.9,
        top_k=1,
        build_missing=True,
        reverse=False,
    ):
        super().__init__(dataset, config)
        self.threshold = threshold
        self.top_k = top_k
        self.build_missing = build_missing
        self.reverse = reverse

    def match(self):
        """Run leonmap-map and yield every prediction it wrote."""
        work = self.work()
        src, tgt = self.prefixes()
        out = work / f"{src}_to_{tgt}.tsv"
        self.log.run(self.command(work, out, src, tgt))
        yield from self.rows(out)

    def command(self, work, out, src, tgt):
        """Build the leonmap-map invocation."""
        source, target = self.ontologies()
        command = [sys.executable, "-m", "leonmap.mapper"]
        command += ["--source", str(source.resolve())]
        command += ["--target", str(target.resolve())]
        command += ["--src-name", src, "--tgt-name", tgt]
        command += ["--src-prefix", f"{src}:", "--tgt-prefix", f"{tgt}:"]
        command += ["--work-dir", str(work), "--out", str(out)]
        command += ["--threshold", str(self.threshold), "--top_k", str(self.top_k)]
        if self.build_missing:
            command.append("--build-missing")
        if self.reverse:
            command.append("--reverse")
        if self.config:
            command += ["--config", str(self.config)]
        return command

    def rows(self, out):
        """Yield every scored prediction from the TSV leonmap copied to --out."""
        for row in table(out):
            if row.get("score"):
                yield self.mapping(row)

    def mapping(self, row):
        """Turn one LeonMap prediction into an SSSOM row."""
        same = row["src_label"].strip().lower() == row["tgt_label"].strip().lower()
        return SemanticMapping(
            subject=to_reference(row["src_id"], name=row["src_label"]),
            predicate=EXACT if same else CLOSE,
            object=to_reference(row["tgt_id"], name=row["tgt_label"]),
            justification=LEXICAL if same else SEMANTIC,
            confidence=float(row["score"]),
        )


if __name__ == "__main__":
    raise SystemExit(LeonMapMapper.main())
