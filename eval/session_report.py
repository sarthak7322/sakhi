"""Bug inbox built from every saved session: what to fix next.

    PYTHONPATH=. python eval/session_report.py            # writes data/BUG_REPORT.md and prints a summary

Sections: problems reported in the app (⚑), turns that failed, impossible / odd values, safety-guard drops,
and the sentences that still needed the LLM, re-run through the CURRENT local model so you can see which ones
a recent fix already covers and which are still candidates for new rules.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date

from server import config, dataset, fastpath, record

OUT = config.DATA_DIR / "BUG_REPORT.md"


def _fmt(r: dict) -> str:
    rec = r.get("record_after") or {}
    keep = {k: rec[k] for k in ("beneficiary_id", "beneficiary_name", "visit_type", "weight_kg", "bp_systolic", "bp_diastolic",
                                "hb_g_dl", "temperature_f", "temperature_c", "respiratory_rate", "ifa_tablets_given") if k in rec}
    lines = [f"- **{r['session']} #{r['turn']}** ({r.get('lang')}, {r.get('source')}, handled by `{r['handled_by']}`)",
             f"  - she said: “{r.get('text')}”"]
    if r.get("heard") and r["heard"].get("text") and r["heard"]["text"] != r.get("text"):
        lines.append(f"  - STT heard: “{r['heard']['text']}”")
    if r.get("replies"):
        lines.append("  - Sakhi: " + " / ".join(f"“{x}”" for x in r["replies"]))
    if r.get("set"):
        lines.append(f"  - update: `{json.dumps(r['set'], ensure_ascii=False)}`")
    if keep:
        lines.append(f"  - record after: `{json.dumps(keep, ensure_ascii=False)}`")
    for ev in r.get("events", []):
        if ev["kind"] not in ("local", "learned"):
            lines.append(f"  - {ev['kind']}: {ev['text']}")
    for f in r.get("feedback", []):
        lines.append(f"  - ⚑ REPORTED: {f['problem']}" + (f" — expected: {f['expected']}" if f.get("expected") else ""))
    return "\n".join(lines)


def main():
    rows = dataset.build()
    s = dataset.summary(rows)
    rec0 = record.new_record(date.today())
    reported = [r for r in rows if r["feedback"]]
    failed = [r for r in rows if r["handled_by"] == "failed"]
    odd = [r for r in rows if any(e["kind"] == "validate" for e in r["events"])]
    guard = [r for r in rows if any(e["kind"] == "guard" for e in r["events"])]
    needed_llm = [r for r in rows if r["handled_by"] in ("llm", "failed")]
    now_local, still = [], Counter()
    for r in needed_llm:
        lang = r.get("lang") if r.get("lang") in fastpath.LANGS else "hi-IN"
        p = fastpath.parse(r["text"], rec0, "collecting", lang)
        (now_local.append((r, p)) if p else still.update([r["text"]]))
    md = [f"# Sakhi bug report ({date.today().isoformat()})", "",
          f"{s['sessions']} sessions, {s['turns']} turns; handled by {s['handled_by']}; "
          f"local share {s['local_share']:.0%}; {s['reports']} problem reports.", ""]
    md += ["## ⚑ Problems reported in the app", ""] + ([_fmt(r) for r in reported] or ["(none)"]) + [""]
    md += ["## Failed turns (AI unavailable and not understood locally)", ""] + ([_fmt(r) for r in failed] or ["(none)"]) + [""]
    md += ["## Odd / impossible values questioned or rejected", ""] + ([_fmt(r) for r in odd] or ["(none)"]) + [""]
    md += ["## Safety guard dropped a line", ""] + ([_fmt(r) for r in guard] or ["(none)"]) + [""]
    md += ["## Sentences that needed the LLM, still not understood locally (candidates for new rules)", ""]
    md += [f"- ({n}×) “{t}”" for t, n in still.most_common()] or ["(none)"]
    md += ["", "## Sentences that needed the LLM but the CURRENT local model now handles", ""]
    md += [f"- “{r['text']}” → `{p.intent} {json.dumps(p.set, ensure_ascii=False)}`" for r, p in now_local] or ["(none)"]
    OUT.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps(s, ensure_ascii=False))
    print(f"reported {len(reported)} · failed {len(failed)} · odd values {len(odd)} · guard {len(guard)} · "
          f"still-LLM {sum(still.values())} · now local {len(now_local)}")
    print(f"written: {OUT}")


if __name__ == "__main__":
    main()
