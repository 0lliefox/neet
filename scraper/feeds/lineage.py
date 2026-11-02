"""First-seen keys: which records are versions of one published item, and when any version was first captured.

`record_id` names one version (it includes `valid_from`, and for travel_updates a hash of the text), so a
notice that is edited, rescheduled or whose dates cannot be parsed gets new record_ids. Held-out eligibility is
"first captured after the tag", so it must be decided per item, not per version, or a pre-tag notice that changed
would look new. Each adapter names the identifiers a version carries (`Adapter.lineage_keys`); records that share
any identifier, directly or through other records, are one item (union-find over the whole build). The item's
key is the identifier of its earliest-captured record, so it does not change as later versions join.

This errs towards merging: two items that ever share an identifier are one item and keep the earlier first-seen.
`first_seen` is relative to the captures in the build, so eligibility needs a build without `--since`.
"""
from __future__ import annotations

import hashlib
from .model import EvidenceRecord


def assign_lineage(records: list[EvidenceRecord], adapters: dict) -> None:
    """Set `item_key` and `first_seen` on every record in place."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    keys: list[list[str]] = []
    for r in records:
        a = adapters.get(r.source)
        ks = [f"{r.source}|{k}" for k in (a.lineage_keys(r) if a is not None else [r.record_id]) if k] or [f"{r.source}|{r.record_id}"]
        keys.append(ks)
        for k in ks:
            parent.setdefault(k, k)
        root = find(ks[0])
        for k in ks[1:]:
            other = find(k)
            if other != root:
                parent[other] = root
    earliest: dict[str, tuple[str, str]] = {}     # component root -> (first fetched_at, identifier it came with)
    for r, ks in zip(records, keys):
        root = find(ks[0])
        cand = (r.fetched_at or "", min(ks))
        if root not in earliest or cand < earliest[root]:
            earliest[root] = cand
    for r, ks in zip(records, keys):
        first, ident = earliest[find(ks[0])]
        r.item_key = hashlib.sha1(ident.encode("utf-8")).hexdigest()[:20]
        r.first_seen = first or None

