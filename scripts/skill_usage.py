"""Read-only skill counts derived from native callback observations."""

import argparse
import json
from collections import Counter
from pathlib import Path

from evaluators.langfuse_data import ROOT, api_client, completed_trace, skill_calls


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    counts = Counter()
    with api_client() as api:
        for trace_id in {case["response"]["trace_id"] for case in manifest}:
            root, rows = completed_trace(api, trace_id)
            counts.update(call["name"] for call in skill_calls(root, rows))
    report = [{"skill": name, "calls": count} for name, count in counts.most_common()]
    target = ROOT / "artifacts" / f"skill-usage-{args.manifest.stem}.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
