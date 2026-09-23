"""Family-disjoint splits for an explicitly curated external presentation corpus."""

from collections import Counter
import hashlib
import json
import re

from training.data_utils import check_split, encode_record
from training.prepare_drive import make_record


def content_group(row: dict) -> str:
    slide = row["slide"]
    text = " ".join([slide.get("title", ""), slide.get("body", ""), *slide.get("bullets", [])])
    normalized = re.sub(r"\s+", " ", text).strip().casefold()
    return hashlib.sha256(normalized.encode()).hexdigest()


def build_splits(rows: list[dict], config: dict, tokenizer) -> tuple[dict, dict]:
    sources = config["templates"]
    family_splits = {}
    for source in sources.values():
        split = source["split"]
        if split not in {"train", "validation", "test"}:
            raise ValueError(f"Unknown split: {split}")
        previous = family_splits.setdefault(source["family"], split)
        if previous != split:
            raise ValueError("A presentation family crosses dataset splits")
    if {r["template"] for r in rows} - sources.keys():
        raise ValueError("Missing template attribution/split")
    result = {k: [] for k in ("train", "validation", "test")}
    seen = set()
    rejected = Counter()
    token_lengths = []
    # Reserve evaluation content first; duplicate training content is removed.
    for split in ("test", "validation", "train"):
        for row in sorted(rows, key=lambda r: (r["template"], r["source_slide"])):
            source = sources[row["template"]]
            if source["split"] != split:
                continue
            slide = row["slide"]
            text = " ".join([slide["title"], *slide["bullets"]]).strip()
            if source.get("external") and len(re.sub(r"\W", "", text)) < 35:
                rejected["external_low_text"] += 1
                continue
            group = content_group(row)
            if group in seen:
                rejected["duplicate_content"] += 1
                continue
            records = [make_record(row, n) for n in range(3 if split == "train" else 1)]
            lengths = []
            try:
                for record in records:
                    lengths.append(len(encode_record(tokenizer, record, config.get("max_length", 3072))["input_ids"]))
            except ValueError as error:
                if "max_length=" not in str(error):
                    raise
                rejected["too_many_tokens"] += 1
                continue
            seen.add(group)
            for record in records:
                record["provenance"].update(source)
                record["provenance"]["content_group"] = group
            result[split].extend(records)
            token_lengths.extend(lengths)
    for left, right in (("train", "validation"), ("train", "test"), ("validation", "test")):
        check_split(result[left], result[right])
        for field in ("family", "content_group"):
            if {r["provenance"][field] for r in result[left]} & {r["provenance"][field] for r in result[right]}:
                raise ValueError(f"Split leakage: {field}")
    if any(len(result[split]) < 3 for split in result):
        raise ValueError("Too few records in a curated split")
    stats = {
        "rejected_examples": dict(rejected),
        "max_record_tokens": max(token_lengths),
        "max_length": config.get("max_length", 3072),
        "splits": {split: {
            "rows": len(records),
            "unique_content": len({r["provenance"]["content_group"] for r in records}),
            "families": sorted({r["provenance"]["family"] for r in records}),
            "templates": len({r["provenance"]["template"] for r in records}),
            "rows_by_family": dict(Counter(r["provenance"]["family"] for r in records)),
        } for split, records in result.items()},
    }
    return result, stats
