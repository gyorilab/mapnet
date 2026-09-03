# MapNet

An aggregator for biomedical ontology mapping tools. MapNet runs matchers over a pair of ontologies, classifies the results against known evidence, and scores them against a gold standard.

Each matcher runs in its own isolated environment. MapNet supplies the input a tool asks for and collects its output. The core never imports a tool.

## Install

```bash
pip install mapnet
```

Adapters resolve their own environments through [uv](https://docs.astral.sh/uv/), which must be on the path to run a matcher.

## Usage

```bash
mapnet fetch mondo
mapnet fetch mondo --format owl
mapnet fetch mondo --version 2026-08-04
mapnet fetch https://example.org/my.obo
mapnet fetch biomappings
mapnet fetch
mapnet map --tool gilda --src mondo --tgt mesh
mapnet map --tool gilda --src https://example.org/my.obo --tgt mesh
```

| Command | Options |
| --- | --- |
| `fetch [source]` | `--format {obo,owl}` `--version` `--redownload` `--workdir` |
| `map` | `--tool` `--src` `--tgt` `--reverse` `--classify` `--gold` `--evidence` `--workdir` |
| `classify <predictions>` | `--out` `--reverse` `--gold` `--evidence` `--workdir` |
| `eval <results>` | `--gold` `--workdir` |

There is no `tools` or `evidence` command. An unknown name lists what is registered, so `--tool nope` answers the same question.

`--workdir` is the one location everything is created under, the current directory by default, so the flag can be skipped entirely. `data/`, `logs/` and `outputs/` are made inside it.

## Output structure

Each run gets its own directory, and the files inside carry plain names. Running:

```bash
mapnet map --tool gilda --src icd10 --tgt mesh --reverse --classify
```

gives these outputs, where the stamp changes with the run:

```text
outputs/gilda/icd10_mesh/20260827_14301274/raw_mappings.sssom.tsv          the run
outputs/gilda/icd10_mesh/20260827_14301274/reverse_raw_mappings.sssom.tsv  the reverse run
outputs/gilda/icd10_mesh/20260827_14301274/right.sssom.tsv                 the four sets
outputs/gilda/icd10_mesh/20260827_14301274/wrong.sssom.tsv
outputs/gilda/icd10_mesh/20260827_14301274/novel.sssom.tsv
outputs/gilda/icd10_mesh/20260827_14301274/conflicts.sssom.tsv
outputs/gilda/icd10_mesh/20260827_14301274/raw_mappings.eval.json          when --gold is given
```

`--workdir run1/` puts the whole run under `run1/`, downloads included, so a second workdir is a fully separate sandbox. Two matchers on the same pair land in different directories, and two runs of the same matcher in different ones. `conflicts` holds collisions nothing could separate, kept rather than resolved arbitrarily.

`classify` on its own writes the four sets beside the predictions unless `--out` says otherwise, so classifying a run's file lands the sets back in that run's directory.

Reduction cascades. When a candidate wins its subject but then loses its object to a stronger rival, the candidate it had beaten is reconsidered rather than lost with it, and this repeats until nothing more can be settled.

`--reverse` runs the tool again with the sides swapped. A collision that confidence cannot separate is then decided by whether the pair survives both directions, which is the last leg of the reduction cascade. `--classify` splits the predictions once written.

Any flag `map` does not recognise is passed to the tool, which validates it, so `--src-prefix mondo` or a tool's own `--threshold 0.8` need no change to MapNet.

`map` fetches both ontologies in the format the tool declares, runs the tool as a subprocess, and writes SSSOM. Each run is logged to `<workdir>/logs/`, named by the stamp that names the run directory. MapNet's own lines and the tool's output go to the same file, and to the terminal as they happen.

A source, `--src` or `--tgt` is a [Bioregistry](https://bioregistry.io) prefix or a download URL. Files land in `<workdir>/data/<prefix>/`, named after the prefix and the extension they were served with.

`--version` names an OBO Foundry release. It applies to prefixes only, since a URL serves one release and cannot be versioned.

## Fetching and refreshing

`fetch` is the only thing that downloads. It takes an ontology prefix, a URL, an evidence name or a gold name, so everything a run needs lands under `data/` and nothing is pulled from elsewhere at classification time.

```bash
mapnet fetch mondo                 # an ontology
mapnet fetch biomappings           # an evidence set
mapnet fetch                       # refresh every volatile source
```

Nothing ever refreshes on its own. `REFRESH` in the manifest names the sources that change upstream, and a bare `mapnet fetch` refetches exactly those. Everything else stays pinned to whatever is already on disk, so a run is reproducible until you ask for it not to be.

Every download is recorded in `data/downloads.json` with its URL, sha256, size and date, and `fetch` reports what actually changed:

```text
biomappings            changed    version latest    data/evidence/biomappings/latest/positive.sssom.tsv
biomappings-negative   unchanged  version latest    data/evidence/biomappings-negative/latest/negative.sssom.tsv
semra                  changed    version 21935586  data/evidence/semra/21935586/processed.sssom.tsv
```

A refresh is not small. Semra alone is about 950 MB, and the biomappings predictions about 21 MB. Since `data/` holds every downloaded file, version control it there if you need a run pinned to an exact snapshot.

## Python API

Three objects, each passed to the next. `MapNet` holds the workdir and the stamp naming a run. `Dataset` holds the two ontologies and the sets a run is judged against. The adapter holds its own parameters.

```python
import mapnet
from adapters.gilda_utils import GildaMapper

space = mapnet.MapNet(workdir=Path("."))
dataset = mapnet.Dataset(src="doid", tgt="mesh", gold="biomappings", mapnet=space)

result = GildaMapper(dataset=dataset).run()
split = result.classify()
scores = result.evaluate()
```

`run` returns a `Result`: where the files landed, and how to classify and score them. An adapter's own settings are its constructor arguments, so MapNet never carries them:

```python
result = LeonMapMapper(dataset=dataset, threshold=0.9, top_k=1, reverse=True).run()
```

A results file already on disk is a run too. `Result.load` names the pair from the file's own rows, so nothing has to be restated:

```python
result = mapnet.Result.load(raw=Path("outputs/.../raw_mappings.sssom.tsv"), gold="biomappings")
split = result.classify()
```

`classify` returns a `Split`: `right`, `wrong`, `novel` and `conflicts` as attributes, the `evidence` behind them, the `prefixes` read off the rows, and `rescued`, the count of `right` rows resting on an uncurated prediction alone. `split.sets()` yields each name with its rows.

Reading, computing and writing are separate. The `classify` and `evaluate` functions take what was already read and return objects. The caller writes.

```python
path = space.fetch(name="semra")               # data/evidence/semra/<record>/
path = space.fetch(name="mondo", fmt="owl")    # data/mondo/mondo.owl
combined = mapnet.aggregate(results=[gilda_run, leonmap_run])
```

`fetch` is the one door: an evidence name, a gold name, a prefix, or a URL. A Zenodo concept resolves to the newest record already cached, and only asks Zenodo when you pass `redownload=True`, so classification never depends on the network. `downloads()` reads the index of every file fetched. `aggregate` unions runs, keeping the first row for each subject and object pair, and returns a `Result` that classifies and scores like any other.

## Evaluation

`--gold` names an SSSOM or CSV table of correct mappings: a file path, a URL, or a name registered in `GOLD`. MapNet reads the run's own output and scores it, so any result can be scored without rerunning the matcher. Without a gold nothing is scored at all.

```bash
mapnet map --tool gilda --src doid --tgt mesh --gold biomappings
mapnet eval outputs/gilda/doid_mesh/20260827_14301274/raw_mappings.sssom.tsv --gold biomappings
```

Metrics are restricted to the entities the gold standard covers. A prediction touching neither side of any gold pair is reported as `ignored` rather than counted wrong, because a partial gold has no opinion on it. Scoring gilda on doid to mesh without that restriction gives precision 0.324; the honest figure is 0.925, since 2,874 of its predictions are uncurated rather than incorrect.

```json
{"hits": 1433, "judged": 1549, "ignored": 2874, "expected": 1505,
 "precision": 0.925, "recall": 0.952, "f1": 0.938,
 "mrr": 0.994, "hits_at_1": 0.992}
```

A gold set covering none of the run's prefixes is an error, not a row of zeros.

| Function | Takes | Gives |
| --- | --- | --- |
| `score(predicted, gold, ranked)` | two sets of pairs, optional ranking | hits, counts, precision, recall, f1, mrr, hits_at_1 |
| `evaluate(rows, gold)` | rows and gold pairs | the same, over the prefixes the rows use |
| `candidates(rows)` | rows | each subject's objects, best confidence first |

Pairs are compared without direction, since an exact match holds both ways round. `mrr` and `hits_at_1` only say something when a tool writes more than one candidate per subject; a matcher that prunes to one before writing scores the same on both.

Gold sets are registered in `GOLD` in the manifest and fetched like anything else. `mp-hp-mgi` is the MGI mouse to human phenotype set, 1,517 pairs. Check the coverage before trusting a number: biomappings holds 1,505 curated doid to mesh pairs, 323 for mondo to mesh, and none at all for icd10 or mp to hp.

## Evidence

`classify` judges candidates against the sources named in `EVIDENCE`, and takes the same names or file paths on `--evidence`.

| Name | Is | Effect on a candidate |
| --- | --- | --- |
| `biomappings` | curated mappings | an exact pair is right |
| `biomappings-negative` | curated rejections | an exact pair is wrong, before anything else |
| `biomappings-predicted` | uncurated predictions | an exact pair is right only where no curated set has ruled |
| `semra` | an assembled landscape | an exact pair is right |
| `obo-xref` | the xrefs of the ontologies being mapped | an exact pair is right |

An entity already mapped into the other prefix, by any curated source, makes a different candidate for it wrong. What survives is novel, then reduced to one subject and one object.

A file path is asserted evidence by default. Tag it to say otherwise:

```bash
mapnet classify preds.sssom.tsv --out sets/ --evidence biomappings,rejected:mine.sssom.tsv
```

`pairs:`, `rejected:` and `predicted:` work on registered names too, so `rejected:biomappings` reads the curated positives as rejections. `classify` reports how many pairs each source actually contributed and warns on stderr about any that contributed none, which is the fastest way to notice a source that is costing a download and earning nothing.

Because predictions are consulted last, they only ever move a candidate that no curated source has ruled on. Moving one out of `novel` also removes it from the collision groups, so enabling predictions changes which of the remaining candidates survive reduction, not just their labels.

## Configuration

`mapnet/manifest.py` is the central registry. Adding a source, an evidence set, a gold set, a matcher or a sink is an edit there. A local path is never one of them: those come from pystow.

| Name | Holds |
| --- | --- |
| `URLS` | download and API endpoints |
| `SOURCES` | every evidence set: what a match means, and where the file comes from |
| `EVIDENCE` | the subset classify actually consults |
| `REFRESH` | the sources a bare `mapnet fetch` refetches |
| `GOLD` | gold standards fetched by name |
| `DEPOSITION` | the Zenodo concept the run's own mapping sets are published under |
| `TOOLS` | registered matchers and the format each one wants |

## Layout

```text
mapnet/          the core package
adapters/        one script per matcher
demos/           end to end scripts over real data
design/          architecture and design documents
```

Everything a run produces goes under the workdir, the current directory by default:

```text
<workdir>/data/       downloaded ontologies, evidence and gold sets
<workdir>/outputs/    one directory per run
<workdir>/logs/       one file per run
```

All three are gitignored at the repository root, and `--workdir` moves them together, so a second workdir is a fully separate sandbox. A tool that caches models puts them under the workdir too, wherever it chooses.

## Demos

`demos/` holds end to end scripts. Each imports the adapter it needs and declares that adapter's dependencies, so the environments never mix.

```bash
uv run --script demos/gilda_doid_mesh.py
uv run --script demos/gilda_mondo_mesh.py
uv run --script demos/leonmap_mp_hp.py
```

`gilda_icd10_mesh.py` blends the enriched ICD-10 concept table into OBO before mapping it to MeSH.

## Design

| Document | Covers |
| --- | --- |
| [design.md](design/design.md) | architecture, class and flow diagrams, classification, modules |
| [adapters.md](design/adapters.md) | the adapter contract, the manifest, and how to add a matcher |

## License

MIT
