# Adapters

An adapter is a script that runs one matcher. It lives outside the importable `mapnet` package
and runs as a subprocess.

## Arguments

MapNet appends these to the command from the manifest:

| Argument | Value |
| --- | --- |
| `--source` | ontology file to map from |
| `--target` | ontology file to map to |
| `--out` | path to write the SSSOM file |
| `--log` | the run's log file, which the adapter appends to |
| `--workdir` | root the run writes under, and where a tool puts anything it keeps |
| `--config` | the manifest's `config` path, appended only when the manifest declares one |

- `--source` and `--target` are local files already downloaded in the format the manifest names.
- `--log` is one file per run, shared with MapNet. An adapter that shells out runs the command through `self.log.run`, which echoes to the terminal and appends to that file at once. Left off, the adapter names its own.
- `--workdir` defaults to the current directory. A tool that keeps models or an index puts
  them somewhere under it and names that place itself. MapNet does not dictate the layout, it
  only guarantees the workdir is the boundary.
- Any flag `mapnet map` does not recognise is appended verbatim, so a tool's own options reach
  the adapter without the CLI declaring them. The adapter validates them.
- Thresholds and model settings are not MapNet's arguments. They are the adapter class's own constructor parameters, or they go in the file `--config` points at, which MapNet passes through without reading.
- `--gold` is absent unless asked for. When absent, no scoring happens.

## Output

The deliverable is one SSSOM TSV file at `--out`.

- The adapter process writes every row to `<out>.tmp`.
- The adapter process then renames `<out>.tmp` onto `<out>` with `os.replace`.
- `mapnet.sssom.write` performs both steps, so an adapter that calls it inherits them.
- Nothing partial ever appears at `--out`.

The core checks two things after the subprocess ends:

- A non zero exit code is a failure.
- A missing file at `--out` is a failure.

Both raise with the last non empty line of the run log.

## Manifest

`mapnet/manifest.py` is the registry. Its `TOOLS` entry registers a matcher.

```python
TOOLS = {
    "gilda": {
        "command": ["uv", "run", "--script", "gilda_utils.py"],
        "wants_format": "obo",
    },
}
```

| Field | Value |
| --- | --- |
| `command` | argv list. A part naming a file in `adapters/` is made absolute. |
| `wants_format` | `obo` or `owl`. The format MapNet downloads and passes. |
| `config` | Optional path to the tool's config file, resolved against `adapters/`. |

- `command` and `wants_format` are required. A missing one raises on load.
- `config` is optional. No tool declares one.
- A containerised tool is `["docker", "run", ...]`.
- Dependencies go in the adapter's PEP 723 header, never in the manifest.

## Writing one

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["mapnet", "gilda", "obonet", "indra"]
#
# [tool.uv.sources]
# mapnet = { path = "..", editable = true }
# ///
"""Match two ontologies on Gilda-normalized labels."""

import importlib.metadata as md

from mapnet import Mapper


class GildaMapper(Mapper):
    name = "gilda"
    version = md.version("gilda")

    def match(self, args):
        """Yield a SemanticMapping for each candidate pair."""
        ...


if __name__ == "__main__":
    raise SystemExit(GildaMapper.main())
```

An adapter holds a `Dataset` and its own parameters. `Mapper.run` fetches both ontologies, calls `match`, and writes SSSOM stamped with `name`, `version` and `tool_id`, returning a `Result`. `Mapper.main` is the same path when MapNet launches the adapter as a subprocess, so the library and the CLI end in one place.

```python
result = LeonMapMapper(dataset=dataset, threshold=0.9, top_k=1).run()
```

- Subclass `Mapper` and implement `match`, which yields `SemanticMapping` objects.
- Set `name` and `version` as class attributes. `tool_id` is optional and takes a CURIE.
- Take the adapter's own settings as constructor arguments and call `super().__init__(dataset, config)`.
- Call `self.prefixes()` for the source and target prefixes, and `self.work()` for a directory of the adapter's own under the workdir.
- The format an adapter reads comes from the manifest, so `wants_format` is declared once.
- Name the file `<tool>_utils.py`. A module named after the tool shadows the package on import.
- Emit every candidate found. Reduction to one to one happens in `classify`.
- Keep confidence comparable within one adapter's output.

The `[tool.uv.sources]` block is a local override while `mapnet` is unpublished. It is removed
on release.

## Scoring

An adapter does not score itself. MapNet reads the run's own output, so any result can be scored later without rerunning the matcher, and numbers from different tools stay comparable because one function produces them all.

| Function | Takes | Gives |
| --- | --- | --- |
| `score(predicted, gold, ranked)` | two sets of pairs, optional ranking | hits, counts, precision, recall, f1, mrr, hits_at_1 |
| `evaluate(rows, gold)` | rows and gold pairs | the same, over the prefixes the rows use |
| `candidates(rows)` | rows | each subject's objects, best confidence first |

- Emit every candidate found, ranked by confidence. Ranking metrics are recovered from the file, so an adapter that prunes to one match per subject makes `mrr` and `hits_at_1` say nothing.
- Keep confidence comparable within one adapter's output.

## Field ownership

| Column | Filled by |
| --- | --- |
| subject_id, object_id, and their labels | adapter |
| predicate_id | adapter |
| mapping_justification | adapter |
| confidence | adapter |
| mapping_tool, mapping_tool_version, mapping_tool_id | adapter |
| subject_source_version, object_source_version | core |
| mapping_date | core |
| curie map header | core |

The curie map covers the prefixes the rows use, resolved through bioregistry. A row that already
carries a core column keeps its own value.
