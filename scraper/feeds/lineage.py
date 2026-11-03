"""First-seen keys: which records are versions of one published item, and when any version was first captured.

`record_id` names one version (it includes `valid_from`, and for travel_updates a hash of the text), so a
notice that is edited, rescheduled or whose dates cannot be parsed gets new record_ids. Held-out eligibility is
"first captured after the tag", so it must be decided per item, not per version, or a pre-tag notice that changed
would look new. Each adapter names the identifiers a version carries (`Adapter.lineage_keys`) and may link versions
for reasons no single record carries (`Adapter.lineage_links`, e.g. one notice replacing another on the page);
records linked either way, directly or through other records, are one item (union-find over the whole build).
The item's key is the identifier of its earliest-captured record, so it does not change as later versions join.

An item whose earliest record came through a coverage extension (`Adapter.coverage_extension`, e.g. a newly
added map tile), in the first capture of that extension, is marked `baseline`: it existed before coverage reached
it. Later items from the extension are new as usual.

This errs towards merging: two records ever linked are one item and keep the earlier first-seen.
`first_seen` is relative to the captures in the build, so eligibility needs a build without `--since`.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from .model import EvidenceRecord


def assign_lineage(records: list[EvidenceRecord], adapters: dict, options: Optional[dict] = None,
                   captures: Optional[dict[str, list[tuple[str, str]]]] = None) -> None:
    """Set `item_key`, `first_seen` and `baseline` on every record in place. `options` maps a source to its lineage
    options (e.g. {"travel_updates": {"rule": "title_or_body"}}); each adapter documents its own. `captures` maps a
    source to every (fetched_at, file) in the build; without it, only captures that yielded records are known."""
    options = options or {}
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    keys: list[list[str]] = []
    for r in records:
        a = adapters.get(r.source)
        ks = [f"{r.source}|{k}" for k in (a.lineage_keys(r, options.get(r.source)) if a is not None else [r.record_id]) if k] \
            or [f"{r.source}|{r.record_id}"]
        keys.append(ks)
        for k in ks:
            parent.setdefault(k, k)
        for k in ks[1:]:
            union(ks[0], k)
    key_of = {id(r): ks[0] for r, ks in zip(records, keys)}
    by_source: dict[str, list[EvidenceRecord]] = {}
    for r in records:
        by_source.setdefault(r.source, []).append(r)
    for src, rs in by_source.items():
        a = adapters.get(src)
        caps = (captures or {}).get(src) or sorted({(r.fetched_at or "", r.provenance.get("file") or "") for r in rs})
        for x, y in (a.lineage_links(rs, caps, options.get(src)) if a is not None else ()):
            union(key_of[id(x)], key_of[id(y)])
    earliest: dict[str, tuple[str, str]] = {}     # component root -> (first fetched_at, identifier it came with)
    first_ext: dict[str, str] = {}                # extension -> its first capture (fetched_at)
    exts: list = []
    for r, ks in zip(records, keys):
        root = find(ks[0])
        cand = (r.fetched_at or "", min(ks))
        if root not in earliest or cand < earliest[root]:
            earliest[root] = cand
        a = adapters.get(r.source)
        ext = a.coverage_extension(r) if a is not None else None
        exts.append(ext)
        if ext and (ext not in first_ext or (r.fetched_at or "") < first_ext[ext]):
            first_ext[ext] = r.fetched_at or ""
    baseline: set[str] = set()
    for r, ks, ext in zip(records, keys, exts):
        root = find(ks[0])
        if ext and (r.fetched_at or "") == first_ext[ext] == earliest[root][0]:
            baseline.add(root)
    for r, ks in zip(records, keys):
        root = find(ks[0])
        first, ident = earliest[root]
        r.item_key = hashlib.sha1(ident.encode("utf-8")).hexdigest()[:20]
        r.first_seen = first or None
        r.baseline = root in baseline

