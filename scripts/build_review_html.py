"""Build a self-contained HTML review page from the two review CSVs (no network, no key).

    python scripts/build_review_html.py

Reads data/eval/gold_review_sheet.csv, evaluation/results/h3_scored/review_sheet.csv and the
source text; writes docs/reviews/h3_review.html. The page keeps the reviewer's answers in the
browser (localStorage) and exports them as CSV. Nothing is sent anywhere.
"""

import csv
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data/eval/gold_review_sheet.csv"
SPOT = ROOT / "evaluation/results/h3_scored/review_sheet.csv"
SRC = ROOT / "data/eval/source_text"
OUT = ROOT / "docs/reviews/h3_review.html"
CONTEXT = 700  # characters of source text shown around the quote


def esc(s):
    return html.escape(s or "").replace("\n", "<br>")


def context(doc_id, quote):
    f = SRC / f"{doc_id}.txt"
    if not f.is_file() or not quote:
        return ""
    text = f.read_text()
    flat = re.sub(r"\s+", " ", text)
    q = re.sub(r"\s+", " ", quote).strip()
    i = flat.lower().find(q.lower()[:80])
    if i < 0:  # fall back to the scorer's normalisation (smart quotes, dashes, soft hyphens); text shows lower-case
        import sys
        sys.path.insert(0, str(ROOT))
        from evaluation.score import norm
        flat, q = norm(text), norm(quote)
        i = flat.find(q[:80])
    if i < 0:
        return "(quote not found verbatim in the source text; check by hand)"
    a, b = max(0, i - CONTEXT // 3), min(len(flat), i + len(q) + CONTEXT // 2)
    pre, hit, post = flat[a:i], flat[i:i + len(q)], flat[i + len(q):b]
    return f"...{esc(pre)}<mark>{esc(hit)}</mark>{esc(post)}..."


def gold_card(r):
    docs = re.findall(r"DOC-\d+", r["source (doc / section / page)"])
    quotes = [q for q in r["evidence_quotes"].split(" || ") if q]
    ctx = "".join(f"<details><summary>Source context ({esc(docs[min(i, len(docs)-1)] if docs else '')})</summary>"
                  f"<p class=src>{context(docs[min(i, len(docs)-1)], q) if docs else ''}</p></details>"
                  for i, q in enumerate(quotes))
    facts = "".join(f"<li>{esc(x)}</li>" for x in r["gold_facts"].split(" || ") if x)
    qid = r["qid"]
    return f"""<section class=card data-id="g:{qid}" data-type="{esc(r['type'])}">
<h3>{qid} <span class=tag>{esc(r['type'])}</span> <span class=tag>{esc(r['expected_behaviour'])}</span></h3>
<p><b>Question:</b> {esc(r['question'])}</p>
<p><b>Gold answer:</b> {esc(r['gold_answer'])}</p>
<p><b>Gold facts:</b></p><ul>{facts}</ul>
<p><b>Evidence quote(s):</b> {esc(' || '.join(quotes))}</p>
<p><b>Source:</b> {esc(r['source (doc / section / page)'])}</p>{ctx}
<div class=ans>Gold OK?
<label><input type=radio name="g:{qid}" value=Y> Y</label>
<label><input type=radio name="g:{qid}" value=N> N</label>
<label><input type=radio name="g:{qid}" value=fix> fix</label>
<input class=note placeholder="notes or corrected text" data-note="g:{qid}"></div></section>"""


def spot_card(r, n):
    key = f"s:{r['qid']}|{r['variant']}|{r['repeat']}"
    return f"""<section class=card data-id="{key}">
<h3>{r['qid']} <span class=tag>{esc(r['variant'])} r{r['repeat']}</span> <span class=tag>{esc(r['type'])}</span>
<span class=tag>expected: {esc(r['expected_behaviour'])}</span> <span class="tag {r['scorer_said'].lower()}">scorer: {r['scorer_said']}</span></h3>
<p class=why>Flagged: {esc(r['why_flagged'])}</p>
<p><b>Question:</b> {esc(r['question'])}</p>
<p><b>Gold answer:</b> {esc(r['gold_answer'])}</p>
<p><b>Must not:</b> {esc(r['must_not'])}</p>
<p class=model><b>Model answer:</b><br>{esc(r['model_answer'])}</p>
<div class=ans>Your verdict on the scorer:
<label><input type=radio name="{key}" value=agree> agree</label>
<label><input type=radio name="{key}" value=scorer_wrong> scorer wrong</label>
<label><input type=radio name="{key}" value=unsure> unsure</label>
<input class=note placeholder="notes" data-note="{key}"></div></section>"""


PAGE = """<!doctype html><html lang=en><meta charset=utf-8><title>H3 review</title>
<style>
body{font:15px/1.5 system-ui,sans-serif;max-width:900px;margin:0 auto;padding:1rem 1rem 6rem;color:#222}
h1{margin-bottom:.2rem}nav{position:sticky;top:0;background:#fff;border-bottom:1px solid #ccc;padding:.5rem 0;z-index:5;
display:flex;gap:1rem;align-items:center;flex-wrap:wrap}
.card{border:1px solid #ccc;border-radius:6px;padding:.6rem 1rem;margin:.8rem 0}.card.done{border-color:#2a7;background:#f4fbf7}
.tag{font-size:12px;background:#eef;border-radius:4px;padding:1px 6px;margin-left:4px;font-weight:400}
.tag.pass{background:#cfc}.tag.fail{background:#fcc}.why{color:#a50;font-size:13px}
.model{background:#f7f7f7;padding:.5rem;border-left:3px solid #999}.src{background:#fffbe6;padding:.5rem;font-size:13px}
mark{background:#ffe066}.ans{margin-top:.5rem;padding-top:.4rem;border-top:1px dashed #ccc}.ans label{margin-right:1rem}
.note{width:55%;margin-left:.5rem}button{padding:.3rem .8rem}.how{background:#f0f6ff;padding:.6rem 1rem;border-radius:6px}
</style>
<h1>H3 review</h1>
<p>Generated from the 20 Nov 2026 baseline run. Answers are saved in this browser only; use <b>Export CSV</b> and send the file back.</p>
<div class=how><b>Part A: gold answers (80).</b> For each item, check the gold answer follows from the evidence quote and says what the source says. Mark <b>Y</b>, <b>N</b> or <b>fix</b> (write the correction in the note).<br>
<b>Part B: scorer spot checks (30).</b> Read the model answer against the gold answer. Does the scorer's verdict (PASS/FAIL) look right? Mark <b>agree</b>, <b>scorer wrong</b> or <b>unsure</b>.<br>
Do not edit <code>eval_v1.jsonl</code>: it is frozen. Corrections go into a new <code>eval_v1.1</code>.</div>
<nav><span id=count></span><label>Show: <select id=filter><option value=all>all</option><option value=todo>not yet reviewed</option></select></label>
<label>Type: <select id=type><option value=all>all</option>@@types@@</select></label>
<button id=exp>Export CSV</button><button id=clr>Clear all</button></nav>
<h2>Part A: gold answers</h2>@@gold@@
<h2>Part B: scorer spot checks</h2>@@spot@@
<script>
const KEY='h3_review_v1';let S=JSON.parse(localStorage.getItem(KEY)||'{}');
const cards=[...document.querySelectorAll('.card')];
function save(){localStorage.setItem(KEY,JSON.stringify(S));paint()}
function paint(){let d=0;cards.forEach(c=>{const id=c.dataset.id,v=(S[id]||{}).v;c.classList.toggle('done',!!v);d+=!!v;
 const f=document.getElementById('filter').value,t=document.getElementById('type').value;
 c.style.display=((f==='todo'&&v)||(t!=='all'&&c.dataset.type&&c.dataset.type!==t))?'none':''});
 document.getElementById('count').textContent=d+' of '+cards.length+' done'}
cards.forEach(c=>{const id=c.dataset.id,s=S[id]||{};
 c.querySelectorAll('input[type=radio]').forEach(r=>{r.checked=(s.v===r.value);r.onchange=()=>{S[id]=Object.assign(S[id]||{},{v:r.value});save()}});
 const n=c.querySelector('.note');n.value=s.n||'';n.oninput=()=>{S[id]=Object.assign(S[id]||{},{n:n.value});save()}});
document.getElementById('filter').onchange=paint;document.getElementById('type').onchange=paint;
document.getElementById('clr').onclick=()=>{if(confirm('Delete all answers?')){S={};localStorage.removeItem(KEY);location.reload()}};
document.getElementById('exp').onclick=()=>{const q=s=>'"'+String(s||'').replace(/"/g,'""')+'"';
 const rows=['id,verdict,notes'].concat(Object.entries(S).map(([k,v])=>[q(k),q(v.v),q(v.n)].join(',')));
 const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([rows.join('\\n')],{type:'text/csv'}));a.download='h3_review_answers.csv';a.click()};
paint();
</script></html>"""


def main():
    gold = list(csv.DictReader(GOLD.open()))
    spot = list(csv.DictReader(SPOT.open()))
    types = sorted({r["type"] for r in gold})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    page = PAGE
    for key, val in {
        "types": "".join(f"<option>{esc(t)}</option>" for t in types),
        "gold": "".join(gold_card(r) for r in gold),
        "spot": "".join(spot_card(r, i) for i, r in enumerate(spot)),
    }.items():
        page = page.replace(f"@@{key}@@", val)
    OUT.write_text(page, encoding="utf-8")
    print(f"wrote {OUT} ({len(gold)} gold items, {len(spot)} spot checks)")


if __name__ == "__main__":
    main()
