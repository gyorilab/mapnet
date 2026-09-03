"""The command line surface."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from mapnet.classify import Split
from mapnet.data import Dataset, MapNet, downloads, get_version
from mapnet.eval import Scores, evaluate
from mapnet.logger import LOG_ROOT
from mapnet.manifest import DATA_ROOT, EVIDENCE, GOLD, REFRESH, SOURCES
from mapnet.matchers import Config, Result, match
from mapnet.sssom import read, stem, to_pairs


def _parser() -> argparse.ArgumentParser:
    """Build the parser with every command registered."""
    parser = argparse.ArgumentParser(prog="mapnet")
    commands = parser.add_subparsers(dest="command", required=True)
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--workdir",
        type=Path,
        default=Path("."),
        help=f"where {DATA_ROOT}, {LOG_ROOT} and outputs are created",
    )
    shared.add_argument(
        "--evidence",
        default=",".join(EVIDENCE),
        help="comma separated evidence names or file paths, each optionally "
        "tagged rejected: or predicted:",
    )
    known = sorted(GOLD) or sorted(SOURCES)
    shared.add_argument("--gold", help=f"a file path, a URL, or one of {known}")

    mapping = commands.add_parser(
        "map",
        parents=[shared],
        help="run a matcher",
        epilog="Any other flag is passed to the tool, which validates it.",
    )
    mapping.add_argument("--tool", required=True)
    mapping.add_argument("--src", required=True, help="source prefix, path or URL")
    mapping.add_argument("--tgt", required=True, help="target prefix, path or URL")
    mapping.add_argument("--classify", action="store_true")
    mapping.add_argument("--reverse", action="store_true", help="also run swapped")
    mapping.set_defaults(run=_map)

    split = commands.add_parser("classify", parents=[shared], help="split predictions")
    split.add_argument("predictions", type=Path)
    split.add_argument("--out", type=Path, help="else beside the predictions")
    split.add_argument("--reverse", type=Path, help="predictions from the swapped run")
    split.set_defaults(run=_classify)

    scoring = commands.add_parser(
        "eval", parents=[shared], help="score results against a gold standard"
    )
    scoring.add_argument("results", type=Path)
    scoring.set_defaults(run=_eval)

    fetch = commands.add_parser(
        "fetch", parents=[shared], help="download or refresh a source"
    )
    fetch.add_argument(
        "source",
        nargs="?",
        help=f"a prefix, an evidence name, or a URL; omit to refresh {REFRESH}",
    )
    fetch.add_argument("--format", default="obo", choices=("obo", "owl"))
    fetch.add_argument("--version", help="an OBO Foundry release, such as 2024-01-31")
    fetch.add_argument("--redownload", action="store_true")
    fetch.set_defaults(run=_fetch)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command asked for, reporting a failure as one line."""
    parser = _parser()
    args, extra = parser.parse_known_args(argv)
    if extra and args.command != "map":
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    args.extra = extra
    try:
        return args.run(args)
    except (ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _dataset(args: argparse.Namespace, src: str, tgt: str) -> Dataset:
    """Build the dataset one command runs against."""
    names = [name.strip() for name in args.evidence.split(",") if name.strip()]
    return Dataset(
        src=src,
        tgt=tgt,
        gold=args.gold,
        evidence=names,
        mapnet=MapNet(workdir=args.workdir),
    )


def _map(args: argparse.Namespace) -> int:
    """Fetch both ontologies, run the tool, and report what it wrote."""
    dataset = _dataset(args=args, src=args.src, tgt=args.tgt)
    config = Config(tool=args.tool, reverse=args.reverse, extra=args.extra)
    result = match(dataset=dataset, config=config)
    print(f"{result.raw}  ({len(to_pairs([result.raw])) // 2} mappings)")
    if args.classify:
        _split(split=result.classify(), result=result)
    if dataset.gold:
        _scores(scores=result.evaluate(), gold=dataset.gold)
    return 0


def _classify(args: argparse.Namespace) -> int:
    """Split one prediction file into right, wrong, novel and conflicts."""
    result = Result.load(
        raw=args.predictions,
        mapnet=MapNet(workdir=args.workdir),
        evidence=[n.strip() for n in args.evidence.split(",") if n.strip()],
        gold=args.gold,
        reverse=args.reverse,
        out=args.out,
    )
    _split(split=result.classify(), result=result)
    return 0


def _eval(args: argparse.Namespace) -> int:
    """Score one results file against a gold standard, writing the scores beside it."""
    if not args.gold:
        raise ValueError("eval needs --gold")
    space = MapNet(workdir=args.workdir)
    gold = to_pairs([space.fetch(name=args.gold)])
    scores = evaluate(rows=read(args.results), gold=gold)
    scores.write(path=args.results.with_name(f"{stem(args.results)}.eval.json"))
    _scores(scores=scores, gold=args.gold)
    return 0


def _fetch(args: argparse.Namespace) -> int:
    """Download one source, or refresh every volatile one, and report what changed."""
    if args.source is None and args.version:
        raise ValueError("--version names one release, so it needs a source")
    space = MapNet(workdir=args.workdir)
    root = space.data
    before = downloads(root)
    for name in [args.source] if args.source else REFRESH:
        path = space.fetch(
            name=name,
            fmt=args.format,
            version=args.version,
            redownload=args.redownload or args.source is None,
        )
        was, now = before.get(str(path), {}), downloads(root).get(str(path), {})
        state = "cached" if not now else "new" if not was else "changed"
        if was.get("sha256") == now.get("sha256") and was:
            state = "unchanged"
        seen = path.parent.name if name in SOURCES else get_version(path) or "unknown"
        print(f"{name:24} {state:10} version {seen}  {path}")
    return 0


def _split(split: Split, result: Result) -> None:
    """Print what one classify run found and where the four sets landed."""
    total = sum(len(rows) for _, rows in split.sets())
    print(f"candidates  {total} over {', '.join(split.prefixes)}")
    for kind, entries in split.evidence.sources.items():
        for path, count in entries:
            print(f"{kind:11} {count:9} pairs  {path}")
    held, kept = len(split.conflicts), len(split.novel)
    print(
        f"rescued     {split.rescued} right on an uncurated prediction alone\n"
        f"reduced     {kept + held} novel candidates -> "
        f"{kept} one to one, {held} held as conflicts"
    )
    for name, rows in split.sets():
        share = 100 * len(rows) / total if total else 0.0
        path = result.directory / f"{name}.sssom.tsv"
        print(f"  {name:10} {len(rows):6} ({share:4.1f}%)  {path}")


def _scores(scores: Scores, gold: str) -> None:
    """Print the metrics one results file scored against a gold standard."""
    print(f"[eval] against {gold}")
    for name, value in scores.as_dict().items():
        print(f"  {name:10} {value:g}")


if __name__ == "__main__":
    raise SystemExit(main())
