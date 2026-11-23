"""Printable end-of-visit report: one A4 page per saved record, saved as PDF from the browser's print dialog.

The page is plain HTML + print CSS so Devanagari/Telugu names render with the phone's own fonts (no PDF
library, no bundled fonts). `?print=1` opens the print dialog straight away ("Save as PDF").
"""
from __future__ import annotations

import json
from datetime import date, datetime
from html import escape

from . import config, record

VOICELOGS = config.DATA_DIR / "voicelogs"

TYPE_LABEL = {"pregnant": "Pregnant woman", "postpartum": "Postpartum mother", "newborn": "Newborn", "child": "Child"}
PRIORITY_LABEL = {"urgent": "URGENT", "high": "High", "normal": "Normal", "check": "Check"}


def _e(v) -> str:
    return escape("" if v is None else str(v))


def _fmt_dt(s: str | None) -> str:
    if not s:
        return ""
    try:
        return datetime.fromisoformat(s).strftime("%d %b %Y, %H:%M")
    except ValueError:
        return s


def _fmt_d(s: str | None) -> str:
    if not s:
        return ""
    try:
        return date.fromisoformat(s[:10]).strftime("%d %b %Y")
    except ValueError:
        return s


def _age(rec: dict, ctx: dict) -> str | None:
    today = date.fromisoformat(rec["visit_date"]) if rec.get("visit_date") else date.today()
    try:
        if ctx.get("dob"):
            days = (today - date.fromisoformat(ctx["dob"])).days
            return f"{days} days" if days < 60 else f"{days // 30} months"
        if ctx.get("lmp"):
            weeks = (today - date.fromisoformat(ctx["lmp"])).days // 7
            return f"{weeks} weeks pregnant"
        if ctx.get("delivery_date"):
            return f"{(today - date.fromisoformat(ctx['delivery_date'])).days} days after delivery"
    except ValueError:
        pass
    return None


def transcript(rec: dict) -> list[dict]:
    """The conversation that produced this record: the turns of its session after the previous save, up to this
    one. Records saved before session ids were stored are matched by scanning the voice logs for the record id."""
    rid = rec.get("record_id")
    paths = []
    if rec.get("session_id"):
        p = VOICELOGS / f"{rec['session_id']}.jsonl"
        if p.exists():
            paths = [p]
    if not paths and VOICELOGS.exists() and rid:
        paths = [p for p in VOICELOGS.glob("*.jsonl") if rid in p.read_text(encoding="utf-8")]
    for p in paths[:1]:
        lines: list[dict] = []
        for raw in p.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = e.get("type")
            if t == "saved":
                if e.get("record_id") == rid:
                    return lines
                lines = []  # an earlier visit in the same session
            elif t == "user_turn" and e.get("text"):
                lines.append({"who": "asha", "text": e["text"], "source": e.get("source")})
            elif t == "agent_text" and e.get("text") and e.get("turn", 0) > 0:
                lines.append({"who": "sakhi", "text": e["text"]})
        return lines
    return []


def _rows(pairs) -> str:
    return "".join(f"<tr><th>{_e(k)}</th><td>{v}</td></tr>" for k, v in pairs if v not in (None, "", []))


def render(rec: dict, auto_print: bool = False) -> str:
    b = record.lookup(rec.get("beneficiary_id")) or {}
    ctx = rec.get("context") or {}
    flagged = bool(rec.get("referral_required") or rec.get("danger_signs"))

    who = _rows([
        ("Name", _e(rec.get("beneficiary_name") or "New beneficiary")
         + (f' <span class="local">({_e(b["name_local"])})</span>' if b.get("name_local") else "")),
        ("Beneficiary ID", _e(rec.get("beneficiary_id") or "—")
         + ("" if rec.get("registered") else ' <span class="tag">not in register</span>')),
        ("Category", _e(TYPE_LABEL.get(rec.get("beneficiary_type"), rec.get("beneficiary_type")))),
        ("Village", _e(ctx.get("village") or b.get("village"))),
        ("Age / stage", _e(_age(rec, ctx))),
        ("LMP", _e(_fmt_d(ctx.get("lmp")))),
        ("Date of birth", _e(_fmt_d(ctx.get("dob")))),
        ("Delivery date", _e(_fmt_d(ctx.get("delivery_date")))),
        ("Birth weight", _e(f'{ctx["birth_weight_kg"]} kg') if ctx.get("birth_weight_kg") else None),
    ])

    temp = (f'{rec["temperature_c"]} °C' if rec.get("temperature_c") is not None
            else f'{rec["temperature_f"]} °F' if rec.get("temperature_f") is not None else None)
    bp = f'{rec["bp_systolic"]}/{rec.get("bp_diastolic") or "?"} mmHg' if rec.get("bp_systolic") else None
    ifa = rec.get("ifa_tablets_given")
    vitals = _rows([
        ("Weight", _e(f'{rec["weight_kg"]} kg') if rec.get("weight_kg") is not None else None),
        ("Blood pressure", _e(bp)),
        ("Haemoglobin", _e(f'{rec["hb_g_dl"]} g/dL') if rec.get("hb_g_dl") is not None else None),
        ("Temperature", _e(temp)),
        ("Breathing rate", _e(f'{rec["respiratory_rate"]} /min') if rec.get("respiratory_rate") is not None else None),
        ("IFA tablets given", _e(ifa) if ifa is not None else None),
        ("Vaccines given today", _e(", ".join(rec.get("vaccinations_given_today") or []))),
        ("Vaccines due", _e(", ".join(rec.get("vaccinations_due") or []))),
        ("Next visit", _e(_fmt_d(rec.get("next_visit_date")) or
                          (f'{_fmt_d(rec["next_visit_suggested"])} (suggested)' if rec.get("next_visit_suggested") else None))),
        ("Notes", _e(rec.get("notes"))),
    ]) or '<tr><td class="muted">No measurements recorded</td></tr>'

    signs = rec.get("danger_signs") or []
    danger_html = ""
    if signs:
        items = "".join(
            f'<li><b>{_e(d.get("label_en") or d.get("id"))}</b>'
            + (f' <span class="local">· {_e(d["label_hi"])}</span>' if d.get("label_hi") else "")
            + '<div class="small">'
            + (f'Evidence: {_e(d["evidence"])}' if d.get("evidence") else "")
            + (f' · Guideline: {_e(d["guideline"])}' if d.get("guideline") else "")
            + (f'<br>Said: “{_e(d["utterance"])}”' if d.get("utterance") else "")
            + "</div></li>" for d in signs)
        danger_html = f"""<section class="danger"><h2>Danger signs: refer to PHC now</h2><ul>{items}</ul></section>"""

    fu = rec.get("followups") or []
    fu_html = "".join(
        f'<tr class="{_e(f.get("priority"))}"><td><span class="pri">{_e(PRIORITY_LABEL.get(f.get("priority"), f.get("priority")))}</span></td>'
        f'<td>{_e(f.get("action"))}</td><td>{_e(_fmt_d(f.get("due")))}</td></tr>' for f in fu
    ) or '<tr><td colspan="3" class="muted">No follow-ups</td></tr>'

    notes = [w for w in (rec.get("warnings") or []) + (rec.get("checks") or []) if isinstance(w, str)]
    notes_html = (f'<section><h2>Checks raised during the visit</h2><ul class="small">'
                  + "".join(f"<li>{_e(w)}</li>" for w in notes) + "</ul></section>") if notes else ""

    tr = transcript(rec)
    tr_html = ""
    if tr:
        tr_html = ('<section class="convo"><h2>Visit conversation</h2><ol>'
                   + "".join(f'<li class="{l["who"]}"><span class="who">{"ASHA" if l["who"] == "asha" else "Sakhi"}</span>'
                             f'{_e(l["text"])}</li>' for l in tr) + "</ol></section>")

    vaccines_hist = ", ".join(f'{v["name"]} ({_fmt_d(v.get("date"))})' for v in b.get("vaccinations_given") or [])
    hist_html = f'<p class="small"><b>Vaccines on register before this visit:</b> {_e(vaccines_hist)}</p>' if vaccines_hist else ""

    title = f'Visit report · {rec.get("beneficiary_name") or "New beneficiary"} · {_fmt_d(rec.get("visit_date"))}'
    status = ('<span class="status red">REFERRAL REQUIRED</span>' if flagged else '<span class="status ok">No danger signs</span>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<style>
  :root {{ --ink:#1d1a17; --muted:#6b645c; --line:#e3ddd4; --red:#b3261e; --redbg:#fdecea; --ok:#1d7a4c; --accent:#b4462b; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:#f4f1ec; color:var(--ink); font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans","Noto Sans Devanagari",sans-serif; }}
  .bar {{ position:sticky; top:0; display:flex; gap:8px; justify-content:flex-end; padding:10px 16px; background:#fff; border-bottom:1px solid var(--line); }}
  .bar button {{ font:inherit; padding:9px 14px; border-radius:10px; border:1px solid var(--line); background:#fff; cursor:pointer; }}
  .bar .primary {{ background:var(--accent); color:#fff; border-color:var(--accent); font-weight:600; }}
  .page {{ max-width:794px; margin:16px auto; background:#fff; padding:32px 36px; border-radius:6px; box-shadow:0 1px 4px rgba(0,0,0,.08); }}
  header {{ display:flex; justify-content:space-between; align-items:flex-start; gap:16px; border-bottom:2px solid var(--accent); padding-bottom:12px; }}
  header h1 {{ margin:0; font-size:20px; }} header .brand {{ color:var(--accent); font-weight:700; letter-spacing:.02em; }}
  .meta {{ text-align:right; font-size:12px; color:var(--muted); }}
  .status {{ display:inline-block; margin-top:6px; padding:3px 10px; border-radius:999px; font-weight:700; font-size:12px; }}
  .status.red {{ background:var(--redbg); color:var(--red); }} .status.ok {{ background:#e6f4ec; color:var(--ok); }}
  h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:20px 0 8px; }}
  .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:0 28px; }}
  table {{ width:100%; border-collapse:collapse; }}
  th, td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ width:42%; font-weight:500; color:var(--muted); }}
  .fu th {{ width:auto; }} .fu td:first-child {{ width:80px; }}
  .pri {{ font-size:11px; font-weight:700; }} tr.urgent .pri {{ color:var(--red); }} tr.high .pri {{ color:#a86b00; }}
  .danger {{ background:var(--redbg); border:1px solid #f3b8b3; border-radius:8px; padding:4px 16px 10px; margin-top:18px; }}
  .danger h2 {{ color:var(--red); }} .danger ul {{ margin:0; padding-left:18px; }} .danger li {{ margin:6px 0; }}
  .local {{ color:var(--muted); }} .small {{ font-size:12px; }} .muted {{ color:var(--muted); }}
  .tag {{ font-size:11px; background:#fff4d6; color:#7a5600; padding:1px 6px; border-radius:6px; }}
  .convo ol {{ list-style:none; margin:0; padding:0; font-size:12.5px; }}
  .convo li {{ padding:4px 0; border-bottom:1px dashed var(--line); display:flex; gap:10px; break-inside:avoid; }}
  .convo .who {{ flex:0 0 46px; font-weight:700; font-size:11px; padding-top:1px; }}
  .convo .asha .who {{ color:var(--accent); }} .convo .sakhi .who {{ color:var(--ok); }}
  footer {{ margin-top:24px; padding-top:10px; border-top:1px solid var(--line); font-size:11px; color:var(--muted); display:flex; justify-content:space-between; gap:12px; }}
  .sign {{ margin-top:28px; display:flex; gap:40px; font-size:12px; color:var(--muted); }} .sign div {{ flex:1; border-top:1px solid var(--ink); padding-top:4px; }}
  @media (max-width:640px) {{ .page {{ margin:0; padding:20px 16px; border-radius:0; }} .grid {{ grid-template-columns:1fr; }} header {{ flex-direction:column; }} .meta {{ text-align:left; }} }}
  @media print {{
    @page {{ size:A4; margin:14mm; }}
    body {{ background:#fff; }} .bar {{ display:none; }}
    .page {{ margin:0; padding:0; max-width:none; box-shadow:none; }}
    * {{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
    section {{ break-inside:avoid; }} .convo {{ break-inside:auto; }}
  }}
</style></head>
<body>
<div class="bar"><button onclick="history.length > 1 ? history.back() : window.close()">← Back</button>
<button class="primary" onclick="window.print()">⬇ Save as PDF</button></div>
<main class="page">
  <header>
    <div><div class="brand">Sakhi · ASHA visit report</div><h1>{_e(rec.get("visit_type") or "Home")} visit</h1>{status}</div>
    <div class="meta">Record <b>{_e(rec.get("record_id"))}</b><br>Visit date {_e(_fmt_d(rec.get("visit_date")))}<br>
      Saved {_e(_fmt_dt(rec.get("saved_at")))}<br>ASHA {_e(rec.get("asha_id"))}</div>
  </header>
  {danger_html}
  <div class="grid">
    <section><h2>Beneficiary</h2><table>{who}</table>{hist_html}</section>
    <section><h2>Visit findings</h2><table>{vitals}</table></section>
  </div>
  <section><h2>Follow-up actions</h2><table class="fu">{fu_html}</table></section>
  {notes_html}
  {tr_html}
  <div class="sign"><div>ASHA signature</div><div>ANM / MO signature</div></div>
  <footer><span>Recorded by voice with Sakhi. Synthetic demo data, not a medical record.</span><span>Generated {_e(datetime.now().strftime("%d %b %Y, %H:%M"))}</span></footer>
</main>
{"<script>window.addEventListener('load', () => setTimeout(() => window.print(), 300));</script>" if auto_print else ""}
</body></html>"""
