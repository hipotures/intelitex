from __future__ import annotations

import json
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .util import PipelineError, atomic_json, digest, normalized, read_json
from .review_context import EvidenceReader
from .application.review import (
    ReviewConflict as ApplicationReviewConflict,
    ReviewRepository as ApplicationReviewRepository,
    ensure_review_state as application_ensure_review_state,
    review_summary as application_review_summary,
)


CATEGORY_LABELS = {
    "people": "People",
    "place": "Places",
    "technology": "Technology",
    "organization": "Organizations",
    "science": "Science",
    "ship": "Ships",
    "status": "Status & social",
    "jargon": "Jargon",
    "name": "Names",
    "other": "Other",
}


def _analysis_observations(path: Path, terms: list[dict]) -> dict[str, list[dict]]:
    """Load read-only Pass-1 observations and attach them to the terms they name.

    Existing projects created before the review UI stored observations only in
    book_memory.json.  Hydrating them here upgrades terms.review.json in place
    without rerunning analysis.
    """
    memory_path = path.with_name("book_memory.json")
    if not memory_path.is_file():
        return {}
    try:
        memory = read_json(memory_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    facts = memory.get("observations", [])
    if not isinstance(facts, list):
        return {}

    names: dict[str, set[str]] = {}
    for term in terms:
        tid = term.get("id")
        if not isinstance(tid, str):
            continue
        for value in [term.get("source", ""), *(term.get("aliases") or [])]:
            if isinstance(value, str) and normalized(value):
                names.setdefault(normalized(value), set()).add(tid)

    attached: dict[str, list[dict]] = {}
    seen: dict[str, set[str]] = {}
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        about = fact.get("about", [])
        if not isinstance(about, list):
            continue
        tids: set[str] = set()
        for value in about:
            if isinstance(value, str):
                tids.update(names.get(normalized(value), ()))
        if not tids:
            continue
        key = json.dumps(fact, ensure_ascii=False, sort_keys=True)
        for tid in tids:
            if key in seen.setdefault(tid, set()):
                continue
            seen[tid].add(key)
            attached.setdefault(tid, []).append(fact)
    return attached


def ensure_review_state(path: Path) -> dict:
    """Add review-app state and Pass-1 facts without touching lexical choices."""
    review = read_json(path)
    changed = False
    terms = review.get("terms")
    if not isinstance(terms, list):
        raise PipelineError("terms.review.json has no valid terms array.")

    need_observations = any("observations" not in term for term in terms)
    observations = _analysis_observations(path, terms) if need_observations else {}
    for term in terms:
        if "reviewed" not in term:
            term["reviewed"] = False
            changed = True
        if "user_notes" not in term:
            term["user_notes"] = ""
            changed = True
        if "observations" not in term:
            term["observations"] = observations.get(term.get("id", ""), [])
            changed = True
    if changed:
        atomic_json(path, review)
    return review


def review_summary(review: dict) -> dict:
    terms = review.get("terms", [])
    categories: dict[str, int] = {}
    reviewed = 0
    uncertain = 0
    noted = 0
    for term in terms:
        category = term.get("category", "other")
        categories[category] = categories.get(category, 0) + 1
        reviewed += bool(term.get("reviewed"))
        noted += bool(str(term.get("user_notes", "")).strip())
        confidences = [n.get("confidence") for n in term.get("meaning_notes", [])]
        confidences.extend(c.get("confidence") for c in term.get("candidates", []))
        confidences.extend(o.get("confidence") for o in term.get("observations", []) if isinstance(o, dict))
        if any(c in {"medium", "low"} for c in confidences):
            uncertain += 1
    return {
        "total": len(terms),
        "reviewed": reviewed,
        "unreviewed": len(terms) - reviewed,
        "uncertain": uncertain,
        "noted": noted,
        "categories": categories,
        "confirmed": review.get("confirmed") is True,
    }


class ReviewConflict(PipelineError):
    """The JSON changed since the browser loaded it; never overwrite it blindly."""


class ReviewRepository:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.evidence_reader = EvidenceReader(path.parent)

    def load(self) -> dict:
        with self.lock:
            review = ensure_review_state(self.path)
            return {**review, "_revision": digest(review)}

    @staticmethod
    def _check_revision(review: dict, expected_revision: str | None) -> None:
        if expected_revision is not None and expected_revision != digest(review):
            raise ReviewConflict("Review data changed in another tab or process. Reload before saving; no changes were written.")

    @staticmethod
    def _check_choice(term: dict) -> None:
        if str(term.get("custom", "")).strip():
            return
        selected = term.get("select")
        if type(selected) is not int or not any(
            c.get("number") == selected and str(c.get("text", "")).strip()
            for c in term.get("candidates", [])
        ):
            raise PipelineError(f"No valid selected/custom form for {term['id']}.")

    def evidence(self, term_id: str) -> dict:
        with self.lock:
            review = ensure_review_state(self.path)
            term = next((t for t in review["terms"] if t.get("id") == term_id), None)
            if term is None:
                raise PipelineError(f"Unknown terminology ID: {term_id}.")
            return self.evidence_reader.for_term(term)

    def review_terms(self, term_ids: list[str], expected_revision: str) -> dict:
        """Approve an explicit pending selection in one all-or-nothing file write."""
        if (not isinstance(term_ids, list) or not term_ids or len(term_ids) > 10_000
                or not all(isinstance(tid, str) for tid in term_ids)
                or len(set(term_ids)) != len(term_ids)):
            raise PipelineError("term_ids must be a nonempty list of unique term IDs.")
        if not isinstance(expected_revision, str) or not expected_revision:
            raise PipelineError("Bulk review requires the loaded review revision.")
        with self.lock:
            review = ensure_review_state(self.path)
            self._check_revision(review, expected_revision)
            indexed = {t["id"]: t for t in review["terms"]}
            if any(tid not in indexed for tid in term_ids):
                raise PipelineError("Bulk review contains an unknown term ID.")
            targets = [indexed[tid] for tid in term_ids if not indexed[tid].get("reviewed")]
            for term in targets:
                self._check_choice(term)
            if targets:
                # Retain a restore point, including annotations and custom choices.
                atomic_json(self.path.parent / "history" / f"review_before_bulk_{digest(review)[:16]}.json", review)
                for term in targets:
                    term["reviewed"] = True
                    term["review_method"] = "bulk"
                atomic_json(self.path, review)
            return {"terms": targets, "changed_count": len(targets),
                    "summary": review_summary(review), "revision": digest(review)}

    def patch_term(self, term_id: str, patch: dict, expected_revision: str | None = None) -> dict:
        allowed = {"select", "custom", "reviewed", "user_notes"}
        unknown = set(patch) - allowed
        if unknown:
            raise PipelineError(f"Unsupported review field(s): {', '.join(sorted(unknown))}.")
        with self.lock:
            review = ensure_review_state(self.path)
            self._check_revision(review, expected_revision)
            term = next((t for t in review["terms"] if t.get("id") == term_id), None)
            if term is None:
                raise PipelineError(f"Unknown terminology ID: {term_id}.")

            old_select = term.get("select")
            old_custom = term.get("custom", "")
            changed_choice = False

            if "select" in patch:
                value = patch["select"]
                if type(value) is not int or not 1 <= value <= len(term.get("candidates", [])):
                    raise PipelineError(f"Invalid candidate number for {term_id}.")
                term["select"] = value
                changed_choice |= value != old_select

            if "custom" in patch:
                value = patch["custom"]
                if not isinstance(value, str):
                    raise PipelineError("custom must be a string.")
                # Keep exact user spelling except for accidental CRs; approval strips edges.
                value = value.replace("\r", "")
                term["custom"] = value
                changed_choice |= value != old_custom

            if "user_notes" in patch:
                value = patch["user_notes"]
                if not isinstance(value, str):
                    raise PipelineError("user_notes must be a string.")
                if len(value) > 100_000:
                    raise PipelineError("user_notes is too long.")
                term["user_notes"] = value.replace("\r", "")

            if "reviewed" in patch:
                if type(patch["reviewed"]) is not bool:
                    raise PipelineError("reviewed must be boolean.")
                term["reviewed"] = patch["reviewed"]
                term["review_method"] = "individual"
                if term["reviewed"]:
                    self._check_choice(term)
            elif changed_choice:
                # A changed lexical decision must be explicitly reviewed again.
                term["reviewed"] = False

            # Notes are annotations, not lexical decisions. Only lexical edits
            # reopen a globally confirmed glossary.
            if changed_choice or patch.get("reviewed") is False:
                review["confirmed"] = False
            atomic_json(self.path, review)
            return {"term": term, "summary": review_summary(review), "revision": digest(review)}

    def set_confirmed(self, confirmed: bool, expected_revision: str | None = None) -> dict:
        with self.lock:
            review = ensure_review_state(self.path)
            self._check_revision(review, expected_revision)
            if confirmed:
                for term in review["terms"]:
                    self._check_choice(term)
                pending = [t["id"] for t in review["terms"] if not t.get("reviewed")]
                if pending:
                    raise PipelineError(
                        f"Cannot confirm: {len(pending)} term(s) are still unreviewed. "
                        "Review them or use approve --accept-defaults intentionally."
                    )
            review["confirmed"] = bool(confirmed)
            atomic_json(self.path, review)
            return {"summary": review_summary(review), "revision": digest(review)}


# Compatibility names now point at the application implementation.  Existing
# imports keep working while HTTP handlers and direct callers share one policy.
ReviewConflict = ApplicationReviewConflict
ReviewRepository = ApplicationReviewRepository
ensure_review_state = application_ensure_review_state
review_summary = application_review_summary


HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Intelitex · Terminology Review</title>
<script>
try {
  const savedTheme = localStorage.getItem('intelitex-review-theme');
  document.documentElement.dataset.theme = savedTheme === 'dark' ? 'dark' : 'light';
} catch (_) {
  document.documentElement.dataset.theme = 'light';
}
</script>
<style>
:root{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color-scheme:light;color:#171717;background:#f7f7f8;--page:#f7f7f8;--text:#171717;--line:#dddde2;--muted:#67676f;--panel:#fff;--accent:#202124;--accent-text:#fff;--soft:#f0f0f3;--ok:#176b3a;--ok-soft:#eaf5ee;--ok-line:#b9d5c4;--warn:#8a5a00;--info:#315b8a;--header:rgba(255,255,255,.98);--control:#fff;--progress-track:#e7e7eb;--row-line:#eee;--row-hover:#f7f7f9;--row-active:#ededf1;--snapshot:#fafafd;--snapshot-line:#e2e2e7;--inner-line:#e5e5ea;--option-selected:#fafafa;--mark:#fff1a8;--shadow:rgba(0,0,0,.03);--error:#a32424}
:root[data-theme="dark"]{color-scheme:dark;color:#ededf0;background:#15161a;--page:#15161a;--text:#ededf0;--line:#3b3d45;--muted:#a7a9b2;--panel:#202126;--accent:#f0f0f2;--accent-text:#17181b;--soft:#30323a;--ok:#6fd49a;--ok-soft:#183a28;--ok-line:#397454;--warn:#e6b968;--info:#82b8ef;--header:rgba(27,28,33,.98);--control:#27292f;--progress-track:#383a42;--row-line:#30323a;--row-hover:#272930;--row-active:#333640;--snapshot:#25272e;--snapshot-line:#3b3e48;--inner-line:#393b44;--option-selected:#2c2e35;--mark:#66581e;--shadow:rgba(0,0,0,.22);--error:#ff8b8b}
*{box-sizing:border-box}html,body{height:100%}body{margin:0;height:100vh;display:flex;flex-direction:column;overflow:hidden}button,input,textarea{font:inherit}button{cursor:pointer}
body{color:var(--text);background:var(--page)}
header{flex:0 0 auto;z-index:10;background:var(--header);border-bottom:1px solid var(--line);padding:14px 20px 12px}
.topline{display:flex;gap:18px;align-items:center;flex-wrap:wrap}.topline h1{font-size:21px;margin:0}.stats{font-size:13px;color:var(--muted)}
.theme-switch{display:inline-flex;margin-left:auto;border:1px solid var(--line);border-radius:8px;padding:2px;background:var(--soft)}.theme-switch button{border:0;border-radius:6px;padding:5px 9px;background:transparent;color:var(--muted);font-size:12px}.theme-switch button.active{background:var(--panel);color:var(--text);box-shadow:0 1px 2px var(--shadow)}
.progress{height:5px;background:var(--progress-track);border-radius:999px;margin:10px 0 12px;overflow:hidden}.progress>div{height:100%;background:var(--accent);width:0;transition:width .2s}
.controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.search{min-width:250px;flex:1;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--control);color:var(--text)}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:9px}.chip,.filter{border:1px solid var(--line);background:var(--control);color:var(--text);border-radius:999px;padding:5px 9px;font-size:12px}.chip.active,.filter.active{background:var(--accent);color:var(--accent-text);border-color:var(--accent)}
.chip.complete,.filter.complete{color:var(--ok);border-color:var(--ok-line)}.chip.complete.active,.filter.complete.active{color:var(--ok);background:var(--ok-soft);border-color:var(--ok);box-shadow:inset 0 0 0 1px var(--ok)}.chip.zero{color:var(--muted)}.viewline{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:9px}.viewline .hint{margin:0}.viewline button{margin-left:auto}.viewnotice{font-size:12px;color:var(--ok)}.viewnotice.error{color:var(--error)}.scope-count{font-variant-numeric:tabular-nums}.bulk:disabled{opacity:.45;cursor:not-allowed}
.layout{flex:1;min-height:0;display:grid;grid-template-columns:minmax(330px,38%) minmax(430px,1fr)}
.list{min-height:0;overflow:auto;border-right:1px solid var(--line);background:var(--panel)}.termrow{padding:10px 14px;border-bottom:1px solid var(--row-line);cursor:pointer;display:grid;grid-template-columns:1fr auto;gap:8px}.termrow:hover{background:var(--row-hover)}.termrow.active{background:var(--row-active)}.termname{font-weight:650}.termmicro{font-size:12px;color:var(--muted);margin-top:3px}.mark{font-size:12px;white-space:nowrap}.reviewed{color:var(--ok)}.pending{color:var(--muted)}.uncertain{color:var(--warn)}.hasnote{color:var(--info);font-weight:650}
.detail{min-height:0;overflow:hidden;padding:22px 28px}.card{max-width:980px;height:100%;margin:0 auto;background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:0 1px 3px var(--shadow);display:flex;flex-direction:column;overflow:hidden}.cardbody{flex:1;min-height:0;overflow:auto;padding:22px}
.detailhead{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.detail h2{margin:0;font-size:25px}.badges{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}.badge{font-size:11px;text-transform:uppercase;letter-spacing:.04em;border-radius:999px;background:var(--soft);padding:5px 7px;color:var(--muted)}.badge.low,.badge.medium{color:var(--warn)}
.snapshot{margin-top:18px;border:1px solid var(--snapshot-line);background:var(--snapshot);border-radius:10px;padding:12px}.snapshot-title{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-bottom:9px}.glance-grid{display:flex;gap:8px;flex-wrap:wrap}.glance{display:flex;align-items:center;gap:7px;border:1px solid var(--line);border-radius:9px;background:var(--control);padding:7px 10px;min-height:38px}.glance-icon{font-size:19px;line-height:1}.glance-label{font-weight:650;font-size:13px}.glance-sub{font-size:11px;color:var(--muted);margin-top:1px}.facts{margin-top:10px;border-top:1px solid var(--inner-line);padding-top:7px}.fact{display:grid;grid-template-columns:24px 1fr auto;gap:7px;align-items:start;padding:4px 0;font-size:13px}.fact-icon{font-size:16px;text-align:center}.fact-conf{font-size:10px;text-transform:uppercase;color:var(--muted);padding-top:2px}
.section{margin-top:20px}.section h3{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:0 0 8px}.note{line-height:1.48}.aliases{color:var(--muted)}
.option{display:block;border:1px solid var(--line);border-radius:9px;padding:10px 12px;margin:7px 0;background:var(--control)}.option.selected{border-color:var(--muted);background:var(--option-selected)}.optionline{display:flex;gap:9px;align-items:flex-start}.optiontext{font-weight:650}.reason{font-size:12px;color:var(--muted);margin:4px 0 0 26px;line-height:1.4}.confidence{font-size:11px;color:var(--muted);margin-left:auto;text-transform:uppercase}
.customrow{display:flex;gap:8px;align-items:center;margin-top:9px}.customrow input{flex:1;border:1px solid var(--line);border-radius:8px;padding:9px 10px;background:var(--control);color:var(--text)}.smallbtn{border:1px solid var(--line);background:var(--control);color:var(--text);border-radius:8px;padding:8px 10px;font-size:12px}
.reviewnotes textarea{width:100%;min-height:92px;resize:vertical;border:1px solid var(--line);border-radius:9px;padding:10px 11px;line-height:1.45;background:var(--control);color:var(--text)}.hint{font-size:11px;color:var(--muted);margin-top:5px}
.evidence details{border-top:1px solid var(--row-line);padding:9px 0}.evidence summary{cursor:pointer;font-size:13px;color:var(--text)}.excerpt{white-space:pre-wrap;line-height:1.5;margin:8px 0;color:var(--text)}.excerpt mark{background:var(--mark);color:var(--text)}.meta{font-size:11px;color:var(--muted)}
.actions{flex:0 0 auto;padding:12px 22px;background:var(--panel);border-top:1px solid var(--line);display:flex;gap:8px;align-items:center;min-height:58px}.primary{border:0;background:var(--accent);color:var(--accent-text);border-radius:8px;padding:9px 13px;font-weight:650}.primary{display:inline-flex;align-items:center;justify-content:center;gap:8px;white-space:nowrap}.primary:disabled{opacity:.4;cursor:not-allowed}.state{font-size:12px;color:var(--muted);margin-left:auto}.confirm{border:1px solid var(--muted);background:var(--control);color:var(--text);border-radius:8px;padding:8px 11px;font-size:12px}.confirm.ready{border-color:var(--ok);color:var(--ok)}
.empty{padding:40px;color:var(--muted);text-align:center}.kbd{display:inline-flex;align-items:center;align-self:center;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10px;line-height:1;border:1px solid currentColor;border-radius:4px;padding:3px 5px;color:inherit;opacity:.75;vertical-align:middle}
.bilingual{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:18px;margin-top:10px}.language-label{font-size:11px;font-weight:650;text-transform:uppercase;color:var(--muted)}.bilingual>div{min-width:0;overflow-wrap:anywhere}.bilingual>div+div{border-left:1px solid var(--line);padding-left:18px}.translation-placeholder{font-size:13px;color:var(--muted);line-height:1.5;margin-top:9px}.context-warning{color:var(--warn);font-size:12px;margin:6px 0;line-height:1.4}
@media(max-width:1100px){.bilingual{grid-template-columns:1fr}.bilingual>div+div{border-left:0;padding:10px 0 0;border-top:1px solid var(--line)}}
@media(max-width:900px){body{height:auto;min-height:100%;overflow:auto}header{position:static}.layout{display:block}.list{max-height:38vh;border-right:0;border-bottom:1px solid var(--line)}.detail{overflow:visible;padding:16px}.card{height:auto;min-height:70vh}.cardbody{overflow:visible;padding:16px}.actions{position:sticky;bottom:0;padding:12px 16px}.search{min-width:180px}}
</style>
</head>
<body>
<header>
  <div class="topline"><h1>Terminology Review</h1><div class="stats" id="stats">Loading…</div><div class="theme-switch" role="group" aria-label="Color theme"><button type="button" data-theme-choice="light">Light</button><button type="button" data-theme-choice="dark">Dark</button></div><button class="confirm" id="confirmBtn">Confirm glossary</button></div>
  <div class="progress"><div id="progressBar"></div></div>
  <div class="controls">
    <input class="search" id="search" placeholder="Search source, alias, translation, meaning, facts, notes…" autocomplete="off">
    <button class="filter active" data-filter="all">All</button>
    <button class="filter" data-filter="unreviewed">Unreviewed</button>
    <button class="filter" data-filter="reviewed">Reviewed</button>
    <button class="filter" data-filter="uncertain">Uncertain</button>
    <button class="filter" data-filter="notes" id="notesFilter">Notes</button>
  </div>
  <div class="chips" id="categories"></div>
  <div class="viewline"><span class="hint" id="viewStats"></span><span class="viewnotice" id="notice" role="status" aria-live="polite"></span><button class="smallbtn bulk" id="bulkBtn">Review remaining in this view</button></div>
</header>
<div class="layout">
  <div class="list" id="termList"></div>
  <main class="detail" id="detail"><div class="empty">Select a term.</div></main>
</div>
<script src="/assets/review_filters.js"></script>
<script>
const labels = __CATEGORY_LABELS__;
let review = null, selectedId = null, category = 'all', statusFilter = 'all', search = '', saveTimer = null;
let writeQueue = Promise.resolve(), actionBusy = false, evidenceToken = 0, searchTimer = null;
const F = ReviewFilters;
const el = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const termConfidence = F.confidence, isUncertain = F.isUncertain, hasNotes = F.hasNotes, selectedText = F.selectedText;
function setTheme(theme){
  const value=theme==='dark'?'dark':'light';
  document.documentElement.dataset.theme=value;
  document.querySelectorAll('[data-theme-choice]').forEach(button=>{
    const active=button.dataset.themeChoice===value;
    button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));
  });
  try{localStorage.setItem('intelitex-review-theme',value)}catch(_){}
}
document.querySelectorAll('[data-theme-choice]').forEach(button=>button.onclick=()=>setTheme(button.dataset.themeChoice));
setTheme(document.documentElement.dataset.theme);
function scopeOptions(){return {category, status:statusFilter, search}}
function filteredTerms(){return F.scope(review.terms,scopeOptions())}
function notice(text='',error=false){el('notice').textContent=text;el('notice').classList.toggle('error',error)}
function showError(error){notice(error.message||String(error),true);let st=el('saveState');if(st)st.textContent='Not saved — see message above'}
async function action(fn){if(actionBusy)return;actionBusy=true;try{await fn()}catch(error){showError(error)}finally{actionBusy=false;updateActionState()}}
function summary(){let s=F.stats(review.terms);return{total:s.total,done:s.reviewed,uncertain:review.terms.filter(isUncertain).length,noted:review.terms.filter(hasNotes).length}}
function renderHeader(){
  const s=summary();el('stats').textContent=`${s.done}/${s.total} reviewed · ${s.total-s.done} remaining · ${s.uncertain} uncertain · ${s.noted} with notes${review.confirmed?' · confirmed':''}`;
  el('progressBar').style.width=`${s.total?100*s.done/s.total:0}%`;
  const confirm=el('confirmBtn');confirm.disabled=s.done!==s.total;confirm.classList.toggle('ready',s.done===s.total);confirm.textContent=review.confirmed?'Glossary confirmed':'Confirm glossary';
  document.querySelectorAll('[data-filter]').forEach(button=>{
    const status=button.dataset.filter, scope=F.scope(review.terms,{category,status,search}), stat=F.stats(scope);
    button.textContent=`${F.filterLabels[status]} ${stat.total}`;
    button.classList.toggle('active',status===statusFilter);
    button.classList.toggle('complete',stat.complete);
    button.setAttribute('aria-pressed',String(status===statusFilter));
    button.title=`${stat.total} matching terms in ${category==='all'?'all categories':(labels[category]||category)}; ${stat.remaining} still unreviewed.`;
  });
  const categories=['all',...Array.from(new Set(review.terms.map(t=>t.category||'other'))).sort((a,b)=>(labels[a]||a).localeCompare(labels[b]||b))];
  el('categories').innerHTML=categories.map(c=>{
    const stat=F.categoryStats(review.terms,c,statusFilter,search),label=c==='all'?'All':(labels[c]||c);
    const tooltip=`${F.filterLabels[statusFilter]} / ${label}: ${stat.total} matching term${stat.total===1?'':'s'}; ${stat.reviewed} reviewed; ${stat.remaining} remaining.${stat.complete?' This scope is fully reviewed.':''}`;
    return `<button class="chip ${category===c?'active':''} ${stat.complete?'complete':''} ${!stat.total&&!stat.complete?'zero':''}" data-cat="${esc(c)}" aria-pressed="${category===c}" title="${esc(tooltip)}">${stat.complete?'✓ ':''}${esc(label)} <span class="scope-count">${stat.total}</span></button>`;
  }).join('');
  document.querySelectorAll('[data-cat]').forEach(b=>b.onclick=()=>action(async()=>{await flushDraft();category=b.dataset.cat;notice();renderAll()}));
  const v=F.stats(filteredTerms());el('viewStats').textContent=`This view: ${v.total} term${v.total===1?'':'s'} · ${v.reviewed} reviewed · ${v.remaining} remaining. Category numbers = matching terms. Green = all matching terms reviewed.`;
  el('bulkBtn').textContent=`Review remaining in this view (${v.remaining})`;
  updateActionState();
}
function updateActionState(){
  if(!review)return;
  el('bulkBtn').disabled=actionBusy||!filteredTerms().some(t=>!t.reviewed);
  const b=el('saveNext'),t=review.terms.find(t=>t.id===selectedId);
  if(b)b.disabled=actionBusy||(!t)||Boolean(t.reviewed&&!nextUnreviewedId(t.id));
}
function renderList(keepSelection=false){let items=filteredTerms(); if(!items.length){if(!keepSelection)selectedId=null;let complete=F.categoryStats(review.terms,category,statusFilter,search).complete;el('termList').innerHTML=`<div class="empty">${complete?'All terms in this scope are reviewed.':'No matching terms.'}</div>`;return}
  if(!keepSelection&&(!selectedId || !items.some(t=>t.id===selectedId))) selectedId=items[0].id;
  el('termList').innerHTML=items.map(t=>`<div class="termrow ${t.id===selectedId?'active':''}" data-id="${esc(t.id)}"><div><div class="termname">${esc(t.source)}</div><div class="termmicro">${esc(labels[t.category]||t.category)} · ${esc(selectedText(t)||'—')}</div></div><div class="mark ${t.reviewed?'reviewed':'pending'}">${t.reviewed?'✓ reviewed':'○ pending'}${isUncertain(t)?' · <span class="uncertain">?</span>':''}${hasNotes(t)?' · <span class="hasnote">✎</span>':''}</div></div>`).join('');
  document.querySelectorAll('.termrow').forEach(r=>r.onclick=()=>action(async()=>{await flushDraft(); selectedId=r.dataset.id;notice();renderList(); renderDetail()}));
  let active=el('termList').querySelector('.termrow.active'); if(active) active.scrollIntoView({block:'nearest'});
}
function highlight(text,t){
  const names=[t.source,...(t.aliases||[])].filter(Boolean).sort((a,b)=>b.length-a.length);
  if(!names.length)return esc(text);
  const pattern=names.map(n=>n.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|');
  const source=String(text||'');let out='',last=0;
  for(const match of source.matchAll(new RegExp(pattern,'gi'))){out+=esc(source.slice(last,match.index))+`<mark>${esc(match[0])}</mark>`;last=match.index+match[0].length}
  return out+esc(source.slice(last));
}
function evidenceRow(e,t,i){
  const entry=e.source_text!==undefined?e:{...e,source_text:e.excerpt,source_kind:'stored_excerpt'};
  const state={not_translated:'Not translated yet. Polish text will appear after P5 completes for this unit.',partial:'This source block is only partially translated. No approximate alignment is shown.',error:'This translation cannot be displayed safely; check the artifact warning.'};
  const warning=entry.status==='stale'?'<div class="context-warning">Earlier translation — this unit is marked stale after a terminology change.</div>':'';
  const missing=state[entry.status]||'Loading saved translation…';
  return `<details ${i===0?'open':''}><summary>${esc(entry.chapter_id)} · ${esc(entry.block_id)}</summary>${warning}${entry.message?`<div class="context-warning">${esc(entry.message)}</div>`:''}<div class="bilingual"><div><div class="language-label">English · ${entry.source_kind==='full_block'?'full source block':'stored excerpt'}</div><div class="excerpt">${highlight(entry.source_text,t)}</div></div><div><div class="language-label">Polish${entry.stage?' · '+esc(entry.stage):''}</div>${entry.polish_text!==null&&entry.polish_text!==undefined?`<div class="excerpt">${esc(entry.polish_text)}</div>`:`<div class="translation-placeholder">${esc(missing)}</div>`}</div></div>${entry.unit_ids?.length?`<div class="meta">Translation unit: ${entry.unit_ids.map(esc).join(', ')}</div>`:''}</details>`;
}
async function loadEvidence(t,token){
  try{const data=await api(`/api/terms/${encodeURIComponent(t.id)}/evidence`);
    if(token!==evidenceToken||selectedId!==t.id)return;
    el('evidenceWarnings').innerHTML=(data.warnings||[]).map(w=>`<div class="context-warning">${esc(w)}</div>`).join('');
    el('evidenceBody').innerHTML=data.entries.map((e,i)=>evidenceRow(e,t,i)).join('')||'<div class="note">No evidence excerpts.</div>';
  }catch(error){if(token===evidenceToken&&selectedId===t.id)el('evidenceWarnings').textContent=`Unable to load saved translations: ${error.message}`}
}
function factIcon(kind){return {gender:'◇',reference:'↔',continuity:'⟳',technical:'⚙',register:'Aa'}[kind]||'•'}
function genderInfo(t){let facts=(t.observations||[]).filter(o=>o.kind==='gender');if(!facts.length)return null;let text=facts.map(x=>x.statement||'').join(' ').toLowerCase();let uncertain=/\b(unknown|uncertain|not established|not known|cannot infer|insufficient)\b/.test(text);let female=/\b(female|woman|girl)\b/.test(text);let male=/\b(male|man|boy)\b/.test(text);if(!uncertain&&female&&!male)return{icon:'♀',label:'Female',detail:facts[0].statement||''};if(!uncertain&&male&&!female)return{icon:'♂',label:'Male',detail:facts[0].statement||''};if(female&&male)return{icon:'⚠',label:'Gender conflict',detail:facts.map(x=>x.statement).join(' · ')};return{icon:'◇',label:'Gender noted',detail:facts.map(x=>x.statement).join(' · ')}}
function entityKind(t){let text=[...(t.meaning_notes||[]).map(n=>n.text),...(t.observations||[]).filter(o=>o.kind==='reference').map(o=>o.statement)].join(' ').toLowerCase();if(/\b(alien|nonhuman)\b/.test(text))return{icon:'👽',label:'Alien'};if(/\b(artificial intelligence|\bai\b|machine intelligence)\b/.test(text))return{icon:'◈',label:'AI / machine'};if(/\bhuman\b/.test(text))return{icon:'👤',label:'Human'};if(/\bspecies\b/.test(text))return{icon:'👽',label:'Species'};if(/\b(spaceborne|entity)\b/.test(text))return{icon:'✦',label:'Entity'};return{icon:'👤',label:'Character / entity'}}
function renderSnapshot(t){if(t.category!=='people')return'';let kind=entityKind(t),gender=genderInfo(t),aliases=(t.aliases||[]);let cells=[`<div class="glance" title="Entity classification from Pass 1"><span class="glance-icon">${kind.icon}</span><div><div class="glance-label">${esc(kind.label)}</div><div class="glance-sub">entity type</div></div></div>`];if(gender)cells.push(`<div class="glance" title="${esc(gender.detail)}"><span class="glance-icon">${gender.icon}</span><div><div class="glance-label">${esc(gender.label)}</div><div class="glance-sub">Pass-1 gender evidence</div></div></div>`);if(aliases.length)cells.push(`<div class="glance"><span class="glance-icon">≡</span><div><div class="glance-label">${aliases.length} alias${aliases.length===1?'':'es'}</div><div class="glance-sub">${esc(aliases.slice(0,2).join(' · '))}</div></div></div>`);let facts=(t.observations||[]).slice().sort((a,b)=>({gender:0,reference:1,continuity:2,technical:3,register:4}[a.kind]??9)-({gender:0,reference:1,continuity:2,technical:3,register:4}[b.kind]??9));let factRows=facts.length?`<div class="facts">${facts.map(f=>`<div class="fact"><span class="fact-icon">${factIcon(f.kind)}</span><span>${esc(f.statement||'')}</span><span class="fact-conf">${esc(f.confidence||'')}</span></div>`).join('')}</div>`:'';return `<div class="snapshot"><div class="snapshot-title">At a glance</div><div class="glance-grid">${cells.join('')}</div>${factRows}</div>`}
function renderDetail(){const token=++evidenceToken;let t=review.terms.find(x=>x.id===selectedId); if(!t){el('detail').innerHTML='<div class="empty">Select a term.</div>';return}
  let conf=termConfidence(t), options=(t.candidates||[]).map(c=>`<label class="option ${!(t.custom||'').trim()&&t.select===c.number?'selected':''}"><div class="optionline"><input type="radio" name="candidate" value="${c.number}" ${!(t.custom||'').trim()&&t.select===c.number?'checked':''}><span class="optiontext">${esc(c.text)}</span><span class="confidence">${esc(c.confidence||'')}</span></div><div class="reason">${esc((c.reasons||[]).join(' · '))}</div></label>`).join('');
  let notes=(t.meaning_notes||[]).map(n=>`<div class="note">${esc(n.text)} <span class="confidence">${esc(n.confidence||'')}</span></div>`).join('')||'<div class="note">—</div>';
  let evidence=(t.evidence||[]).map((e,i)=>evidenceRow(e,t,i)).join('')||'<div class="note">No evidence excerpts.</div>';
  el('detail').innerHTML=`<div class="card"><div class="cardbody"><div class="detailhead"><div><h2>${esc(t.source)}</h2><div class="termmicro">${esc(t.id)}</div></div><div class="badges"><span class="badge">${esc(labels[t.category]||t.category)}</span><span class="badge ${esc(conf)}">${esc(conf)}</span>${t.reviewed?'<span class="badge reviewed">reviewed</span>':''}</div></div>
  ${renderSnapshot(t)}
  <div class="section"><h3>Meaning</h3>${notes}</div><div class="section"><h3>Aliases</h3><div class="aliases">${(t.aliases||[]).length?(t.aliases||[]).map(esc).join(' · '):'—'}</div></div>
  <div class="section"><h3>Translation</h3>${options}<div class="customrow"><input id="custom" value="${esc(t.custom||'')}" placeholder="Custom Polish form"><button class="smallbtn" id="keepSource">Keep source</button><button class="smallbtn" id="clearCustom">Use candidate</button></div></div>
  <div class="section reviewnotes"><h3>Reviewer notes</h3><textarea id="userNotes" placeholder="Your notes about this entity or terminology decision…">${esc(t.user_notes||'')}</textarea><div class="hint">Autosaved. Notes do not block approval or change the translation automatically. Use Notes after translation to review the English/Polish context.</div></div>
  <div class="section evidence"><h3>Evidence ${(t.evidence||[]).length} · English / Polish</h3><div id="evidenceWarnings" class="context-warning"></div><div id="evidenceBody">${evidence}</div></div></div>
  <div class="actions"><button class="smallbtn" id="prevBtn">← Previous</button><button class="primary" id="saveNext">Review & next <span class="kbd">Ctrl+Enter</span></button><button class="smallbtn" id="nextBtn">Next →</button><span class="state" id="saveState">${t.reviewed?'Reviewed':'Not reviewed'}</span></div></div>`;
  document.querySelectorAll('input[name=candidate]').forEach(r=>r.onchange=()=>action(async()=>{await flushDraft();await patch(t.id,{select:Number(r.value),custom:''});renderAll()}));
  el('custom').oninput=scheduleDraft;el('userNotes').oninput=scheduleDraft;
  el('keepSource').onclick=()=>action(async()=>{await flushDraft();await patch(t.id,{custom:t.source});renderAll()});
  el('clearCustom').onclick=()=>action(async()=>{await flushDraft();await patch(t.id,{custom:''});renderAll()});
  el('saveNext').onclick=()=>action(reviewAndNext);el('prevBtn').onclick=()=>action(()=>navigate(-1));el('nextBtn').onclick=()=>action(()=>navigate(1));
  updateActionState();loadEvidence(t,token);
}
async function api(url,opts={}){let r=await fetch(url,{headers:{'Content-Type':'application/json'},...opts}); let data=await r.json().catch(()=>({})); if(!r.ok) throw new Error(data.error||`${r.status} ${r.statusText}`); return data}
function patch(id,body){
  const task=writeQueue.then(async()=>{
    let st=el('saveState');if(st)st.textContent='Saving…';
    const data=await api(`/api/terms/${encodeURIComponent(id)}`,{method:'PATCH',body:JSON.stringify({...body,_revision:review._revision})});
    let idx=review.terms.findIndex(t=>t.id===id);review.terms[idx]=data.term;review.confirmed=data.summary.confirmed;review._revision=data.revision;
    if(st&&id===selectedId)st.textContent='Saved';renderHeader();renderList(true);return data.term;
  });
  writeQueue=task.catch(()=>{});return task;
}
function scheduleDraft(){clearTimeout(saveTimer);saveTimer=setTimeout(()=>saveDraft().catch(showError),550)}
async function saveDraft(){
  clearTimeout(saveTimer);saveTimer=null;
  const t=review.terms.find(x=>x.id===selectedId),custom=el('custom'),notes=el('userNotes');if(!t)return;
  const body={};if(custom&&custom.value!==(t.custom||''))body.custom=custom.value;if(notes&&notes.value!==(t.user_notes||''))body.user_notes=notes.value;
  if(Object.keys(body).length)await patch(t.id,body);
}
async function flushDraft(){clearTimeout(saveTimer);saveTimer=null;await writeQueue;await saveDraft()}
function nextUnreviewedId(id){return F.nextUnreviewed(review.terms,id,scopeOptions())}
async function reviewAndNext(){
  const t=review.terms.find(x=>x.id===selectedId);if(!t)return;
  await flushDraft();await patch(t.id,{reviewed:true});const next=nextUnreviewedId(t.id);
  if(next){if(statusFilter==='reviewed')statusFilter='unreviewed';selectedId=next;notice()}
  else{notice('All matching terms reviewed.');selectedId=t.id}
  renderAll();
}
async function navigate(delta){await flushDraft();let items=filteredTerms(),i=items.findIndex(t=>t.id===selectedId);if(i<0)return;selectedId=items[Math.max(0,Math.min(items.length-1,i+delta))].id;notice();renderList();renderDetail()}
function renderAll(){renderHeader();renderList();renderDetail()}
async function load(){review=await api('/api/review');renderAll()}
function applySearch(){
  if(actionBusy){searchTimer=setTimeout(applySearch,60);return}
  action(async()=>{await flushDraft();search=el('search').value;notice();renderAll()});
}
el('search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(applySearch,180)};
document.querySelectorAll('.filter').forEach(b=>b.onclick=()=>action(async()=>{await flushDraft();statusFilter=b.dataset.filter;notice();renderAll()}));
el('bulkBtn').onclick=()=>action(async()=>{
  await flushDraft();const pending=filteredTerms().filter(t=>!t.reviewed);if(!pending.length)return;
  const scope=`${F.filterLabels[statusFilter]} / ${category==='all'?'All categories':labels[category]||category}${search?' / search: '+search:''}`;
  if(!window.confirm(`Review ${pending.length} remaining term(s) in this view?\n\n${scope}\n\nKeep every current candidate/custom form and all notes. This does NOT confirm the entire glossary or call a model. A backup will be saved.`))return;
  const result=await api('/api/review/bulk',{method:'POST',body:JSON.stringify({term_ids:pending.map(t=>t.id),revision:review._revision})});
  const updated=new Map(result.terms.map(t=>[t.id,t]));review.terms=review.terms.map(t=>updated.get(t.id)||t);review.confirmed=result.summary.confirmed;review._revision=result.revision;
  notice(`${result.changed_count} terms reviewed. Current choices and notes kept.`);renderAll();
});
el('confirmBtn').onclick=()=>action(async()=>{
  await flushDraft();if(el('confirmBtn').disabled)return;
  const result=await api('/api/confirm',{method:'POST',body:JSON.stringify({confirmed:true,revision:review._revision})});
  review.confirmed=result.summary.confirmed;review._revision=result.revision;renderHeader();notice('Glossary confirmed. Stop this server and run approve, then translate.');
});
document.addEventListener('keydown',e=>{
  if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();action(reviewAndNext);return}
  if(['INPUT','TEXTAREA'].includes(document.activeElement.tagName))return;
  if(e.key==='j'||e.key==='ArrowDown'){e.preventDefault();action(()=>navigate(1))}
  if(e.key==='k'||e.key==='ArrowUp'){e.preventDefault();action(()=>navigate(-1))}
  if(/^[1-9]$/.test(e.key))action(async()=>{let t=review.terms.find(x=>x.id===selectedId),n=Number(e.key);if(t&&t.candidates.some(c=>c.number===n)){await flushDraft();await patch(t.id,{select:n,custom:''});renderAll()}});
});
window.addEventListener('beforeunload',e=>{const t=review?.terms.find(t=>t.id===selectedId);if(saveTimer||actionBusy||(t&&((el('custom')&&el('custom').value!==(t.custom||''))||(el('userNotes')&&el('userNotes').value!==(t.user_notes||''))))){e.preventDefault();e.returnValue=''}});
load().catch(e=>{document.body.innerHTML=`<pre style="padding:2rem">${esc(e.stack||e.message)}</pre>`});
</script>
</body></html>
'''


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, repository: ReviewRepository):
        self.repository = repository
        super().__init__(address, ReviewHandler)


class ReviewHandler(BaseHTTPRequestHandler):
    server: ReviewServer

    def log_message(self, fmt, *args):
        # Keep the terminal usable; errors are returned to the browser as JSON.
        return

    def _json(self, payload: dict, status: int = HTTPStatus.OK):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000:
                raise PipelineError("Request body is too large.")
            raw = self.rfile.read(length)
            body = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(body, dict):
                raise PipelineError("JSON request body must be an object.")
            return body
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise PipelineError(f"Invalid JSON request: {exc}") from exc

    def _error(self, exc: Exception, status=HTTPStatus.BAD_REQUEST):
        self._json({"error": str(exc)}, HTTPStatus.CONFLICT if isinstance(exc, ReviewConflict) else status)

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                document = HTML.replace("__CATEGORY_LABELS__", json.dumps(CATEGORY_LABELS, ensure_ascii=False))
                raw = document.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            elif parsed.path == "/assets/review_filters.js":
                raw = Path(__file__).with_name("review_filters.js").read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/javascript; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            elif parsed.path.startswith("/api/terms/") and parsed.path.endswith("/evidence"):
                term_id = parsed.path[len("/api/terms/"):-len("/evidence")]
                self._json(self.server.repository.evidence(term_id))
            elif parsed.path == "/api/review":
                self._json(self.server.repository.load())
            else:
                self._error(PipelineError("Not found."), HTTPStatus.NOT_FOUND)
        except (PipelineError, OSError) as exc:
            self._error(exc)

    def do_PATCH(self):
        parsed = urlparse(self.path)
        try:
            prefix = "/api/terms/"
            if not parsed.path.startswith(prefix):
                self._error(PipelineError("Not found."), HTTPStatus.NOT_FOUND)
                return
            term_id = parsed.path[len(prefix):]
            body = self._body()
            revision = body.pop("_revision", None)
            self._json(self.server.repository.patch_term(term_id, body, revision))
        except (PipelineError, OSError) as exc:
            self._error(exc)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/review/bulk":
                body = self._body()
                self._json(self.server.repository.review_terms(body.get("term_ids"), body.get("revision")))
                return
            if parsed.path != "/api/confirm":
                self._error(PipelineError("Not found."), HTTPStatus.NOT_FOUND)
                return
            body = self._body()
            if type(body.get("confirmed")) is not bool:
                raise PipelineError("confirmed must be boolean.")
            self._json(self.server.repository.set_confirmed(body["confirmed"], body.get("revision")))
        except (PipelineError, OSError) as exc:
            self._error(exc)


def run_review_server(path: Path | object, bind: str, port: int, open_browser: bool, ui) -> None:
    repository = path if hasattr(path, "load") and hasattr(path, "path") else ReviewRepository(path)
    path = repository.path
    if not path.is_file():
        raise PipelineError(f"Review file does not exist: {path}")
    if not 0 <= port <= 65535:
        raise PipelineError("Review port must be between 0 and 65535.")
    repository.load()  # validate/migrate before binding a socket
    try:
        server = ReviewServer((bind, port), repository)
    except OSError as exc:
        raise PipelineError(f"Cannot start review server on {bind}:{port}: {exc}") from exc
    actual_port = server.server_address[1]
    display_host = "127.0.0.1" if bind in {"0.0.0.0", "::"} else bind
    url = f"http://{display_host}:{actual_port}/"
    ui.message(f"Terminology review: {url}\nSource of truth: {path}\nPress Ctrl-C to stop the review server. Changes are saved atomically as you work.")
    if bind not in {"127.0.0.1", "localhost", "::1"}:
        ui.message("WARNING: review UI is listening beyond loopback. It has no authentication; use only on a trusted network.")
    if open_browser:
        webbrowser.open(url, new=2)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
