"""Fetch the ontology and evidence files a run needs."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

import bioregistry
import pystow

from mapnet.logger import LOG_ROOT
from mapnet.manifest import (
    DATA_ROOT,
    EVIDENCE,
    GOLD,
    OUTPUT_ROOT,
    RUN_STAMP,
    SOURCES,
    URLS,
)
from mapnet.utils import header

DOWNLOADS = "downloads.json"

VERSION_INFO = re.compile(r"<owl:versionInfo[^>]*>([^<]+)</owl:versionInfo>")

VERSION_IRI = re.compile(r'<owl:versionIRI[^>]*rdf:resource="([^"]+)"')

DOWNLOADERS = {"obo": bioregistry.get_obo_download, "owl": bioregistry.get_owl_download}


@dataclass
class MapNet:
    """Where files land, and the stamp naming every run, to a hundredth of a second."""

    workdir: Path = Path(".")
    stamp: str = field(default_factory=lambda: datetime.now().strftime(RUN_STAMP)[:-4])

    @property
    def data(self) -> Path:
        return self.workdir / DATA_ROOT

    @property
    def logs(self) -> Path:
        return self.workdir / LOG_ROOT

    @property
    def outputs(self) -> Path:
        return self.workdir / OUTPUT_ROOT

    def fetch(
        self,
        name: str,
        fmt: str = "obo",
        version: str | None = None,
        redownload: bool = False,
    ) -> Path:
        """Return a local path to one source, downloading it when absent."""
        return get_source(name, fmt, version, redownload, self.data)


@dataclass
class Dataset:
    """The two ontologies one run maps and the sets it is judged against."""

    src: str
    tgt: str
    gold: str | None = None
    evidence: Sequence[str] = field(default_factory=lambda: list(EVIDENCE))
    mapnet: MapNet = field(default_factory=MapNet)

    def ontologies(self, fmt: str = "obo") -> tuple[Path, Path]:
        return self.mapnet.fetch(name=self.src, fmt=fmt), self.mapnet.fetch(
            name=self.tgt, fmt=fmt
        )


def get_source(
    name: str,
    fmt: str = "obo",
    version: str | None = None,
    redownload: bool = False,
    root: Path | None = None,
) -> Path:
    """Return a local path to anything mapnet fetches, downloading it when absent."""
    if Path(name).is_file():
        return Path(name)
    root = root or DATA_ROOT
    url, path = _locate(name, fmt, version, redownload, root)
    if path.exists() and not redownload:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _download(url, path)
    except pystow.utils.DownloadError as error:
        raise ValueError(f"cannot download {path.name} from {url}") from error
    _record(root, path, url)
    return path


def downloads(root: Path | None = None) -> dict[str, dict[str, str | int]]:
    """Read the index of every file mapnet has downloaded."""
    index = (root or DATA_ROOT) / DOWNLOADS
    return json.loads(index.read_text("utf-8")) if index.is_file() else {}


def _locate(
    name: str, fmt: str, version: str | None, resolve: bool, root: Path
) -> tuple[str, Path]:
    """Resolve a name to the URL it comes from and the file it lands in."""
    if name in GOLD:
        return GOLD[name], root / "gold" / _plain_name(GOLD[name])
    if name not in SOURCES:
        prefix, url = _ontology_url(name, fmt, version)
        stem = f"{prefix}_v_{version}" if version else prefix
        return url, root / prefix / f"{stem}{Path(_plain_name(url)).suffix}"
    if version:
        raise ValueError(f"{name!r} is an evidence set, which takes no version")
    source = SOURCES[name][1]
    if source == "obo":
        raise ValueError(f"{name!r} is not a downloadable evidence set")
    cached = root / "evidence" / name
    url, release = _evidence_url(source, cached, resolve)
    return url, cached / release / _plain_name(url)


def _record(root: Path, path: Path, url: str) -> None:
    """Note a download's url, digest, size and date in the download index."""
    index = downloads(root)
    index[str(path)] = {
        "url": url,
        "sha256": _digest(path),
        "bytes": path.stat().st_size,
        "downloaded": datetime.now().isoformat(timespec="seconds"),
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / DOWNLOADS).write_text(json.dumps(index, indent=2, sort_keys=True), "utf-8")


def _digest(path: Path) -> str:
    """Hash a file's contents, reading it in chunks."""
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            sha.update(chunk)
    return sha.hexdigest()


def _plain_name(url: str) -> str:
    """Name the file a URL lands in, once unzipped."""
    name = Path(urlparse(url).path).name
    return pystow.utils.base_from_gzip_name(name) if name.endswith(".gz") else name


def _evidence_url(source: str, cached: Path, resolve: bool) -> tuple[str, str]:
    """Turn a manifest entry into a download URL and the version to file it under."""
    if not source.startswith("zenodo:"):
        return source, "latest"
    concept, _, filename = source.removeprefix("zenodo:").partition("/")
    resolved = _record_dir(int(concept), cached, resolve)
    return URLS["zenodo_file"].format(record=resolved, filename=filename), resolved


def _record_dir(concept: int, cached: Path, resolve: bool) -> str:
    """Take the newest cached Zenodo record, asking Zenodo only when refetching."""
    newest = _newest_cached(cached)
    if newest and not resolve:
        return newest
    return _latest_record(concept, cached)


def _newest_cached(cached: Path) -> str | None:
    """Take the newest Zenodo record already downloaded for one source."""
    have = [d.name for d in cached.glob("*") if d.is_dir() and d.name.isdigit()]
    return max(have, key=int) if have else None


def _latest_record(concept: int, cached: Path) -> str:
    """Resolve a Zenodo concept to its newest version, else the newest cached."""
    url = URLS["zenodo_latest"].format(concept=concept)
    try:
        with urlopen(url, timeout=30) as response:
            return str(json.load(response)["id"])
    except (URLError, TimeoutError, KeyError) as error:
        newest = _newest_cached(cached)
        if newest is None:
            raise ValueError(
                f"cannot resolve Zenodo record {concept}: {error}"
            ) from error
        print(
            f"[evidence] Zenodo unreachable ({error}), using cached record {newest}",
            file=sys.stderr,
        )
        return newest


def get_version(path: Path) -> str | None:
    """Read an ontology's version from its filename or its header."""
    stem = path.name.split(".")[0]
    if "_v_" in stem:
        return stem.split("_v_", 1)[1]
    return _header_version(path)


def _header_version(path: Path) -> str | None:
    """Read a version from an OBO or an OWL header."""
    head = header(path)
    for line in head.splitlines():
        if line.startswith("data-version:"):
            return _version_part(line.split(":", 1)[1].strip())
        if line.startswith("["):
            break
    info = VERSION_INFO.search(head)
    if info:
        return info.group(1).strip()
    iri = VERSION_IRI.search(head)
    return _version_part(iri.group(1)) if iri else None


def _download(url: str, path: Path) -> None:
    """Download a URL to a path, unzipping a gzipped source into it."""
    if not url.endswith(".gz"):
        pystow.utils.download(url, path, force=True)
        return
    archive = path.with_name(f"{path.name}.gz")
    pystow.utils.download(url, archive, force=True)
    pystow.utils.gunzip(archive, path, cleanup=True)


def _version_part(value: str) -> str:
    """Strip the release path OBO headers wrap a version in."""
    parts = value.split("/")
    if parts[-1].endswith((".obo", ".owl", ".json")):
        parts.pop()
    parts = [part for part in parts if part and part != "releases"]
    return parts[-1] if parts else value


def _ontology_url(source: str, fmt: str, version: str | None) -> tuple[str, str]:
    """Return the cache key and download URL for a prefix or a URL."""
    if source.startswith(("http://", "https://")):
        if version:
            raise ValueError("a URL serves one release, so it cannot be versioned")
        return _plain_name(source).split(".")[0], source
    if fmt not in DOWNLOADERS:
        raise ValueError(f"unknown format {fmt!r}, expected {sorted(DOWNLOADERS)}")
    if version:
        if bioregistry.get_obofoundry_prefix(source) is None:
            raise ValueError(f"{source!r} is not an OBO Foundry ontology, pass a URL")
        return source, URLS["obo_release"].format(
            prefix=source, version=version, fmt=fmt
        )
    url = DOWNLOADERS[fmt](source)
    if url is None:
        raise ValueError(f"bioregistry has no {fmt} download for {source!r}")
    return source, url
