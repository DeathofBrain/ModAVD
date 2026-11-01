#!/usr/bin/env python3
"""Summarize observed audit counters; never infer physical memory release."""
import argparse
import json
import pathlib
import re


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=pathlib.Path)
    args = parser.parse_args()
    blocks, causes, untracked = {}, {}, {}
    transactions, lifetimes, pauses, physical, backing, eligibility, subobjects, budgets = [], [], [], [], [], [], [], []
    enabled_seen = False
    for line in args.log.read_text(errors="replace").splitlines():
        prefix = next((p for p in ("hvf-ram-audit: ", "hvf-ram-reclaim: ",
                                  "hvf-ram-pause: ", "hvf-ram-physical: ",
                                  "hvf-ram-backing: ", "hvf-ram-eligibility: ",
                                  "hvf-ram-subobject: ", "hvf-ram-budget: ") if p in line), None)
        if prefix is None:
            continue
        fields = dict(re.findall(r"([a-z_]+)=([^\s]+)",
                                 line.split(prefix, 1)[1]))
        fields = {key: int(value) if value.isdecimal() else value
                  for key, value in fields.items()}
        enabled_seen |= bool(fields.get("reclaim_enabled", 0))
        if prefix == "hvf-ram-physical: ":
            physical.append(fields)
            continue
        if prefix == "hvf-ram-backing: ":
            backing.append(fields)
            continue
        if prefix == "hvf-ram-eligibility: ":
            eligibility.append(fields)
            continue
        if prefix == "hvf-ram-subobject: ":
            subobjects.append(fields)
            continue
        if prefix == "hvf-ram-budget: ":
            budgets.append(fields)
            continue
        if prefix == "hvf-ram-reclaim: ":
            if fields.get("event") == "external-lifetime":
                lifetimes.append(fields)
            else:
                transactions.append(fields)
            continue
        if prefix == "hvf-ram-pause: ":
            pauses.append(fields)
            continue
        event = fields.get("event")
        if event == "report-untracked":
            untracked = fields
        elif event == "cause":
            causes[(fields.get("block"), fields.get("cause"))] = fields
        elif "block" in fields:
            blocks[fields["block"]] = fields
    print(json.dumps({"blocks": blocks,
                      "causes": list(causes.values()),
                      "untracked_reports": untracked,
                      "transactions": transactions,
                      "external_lifetimes": lifetimes,
                      "pause_events": pauses,
                      "physical_observations": physical,
                      "backing_receipts": backing,
                      "eligibility_observations": eligibility,
                      "subobject_observations": subobjects,
                      "budget_observations": budgets,
                      "strict_bytes_total": sum(b.get("strict_bytes_total", 0)
                                                for b in blocks.values()),
                      "committed_bytes_total": sum(b.get("committed_bytes_total", 0)
                                                   for b in blocks.values()),
                      "reclaim_enabled_observed": enabled_seen,
                      "interpretation": "Candidate bounds and cumulative transaction bytes may repeat pages. "
                                        "Committed bytes count successful backing replacements, not distinct pages "
                                        "or system physical memory released. No physical-memory benefit is inferred."}, indent=2))


if __name__ == "__main__":
    main()
