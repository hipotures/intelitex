"""Bounded Astra isolated/session comparison; never writes to the source project."""
from __future__ import annotations

import argparse
import copy
import difflib
import json
import re
import tarfile
from collections import Counter
from pathlib import Path

from bookpipe.catalog import load_catalog, pricing_snapshot
from bookpipe.codex_transport import BASE_INSTRUCTIONS
from bookpipe.util import PipelineError, digest, read_json
from experiments import codex_cache_v3_session as session
from experiments.codex_session_cache_probe import private_json

PASS_CONFIG = {str(n): {"model": "gpt-6-astra", "effort": "low" if n in (2,4) else "medium"} for n in range(2,6)}
VARIANTS = ("baseline-cache-v2", "single-thread-cache-v3")


def prepare(project: Path, scratch: Path):
    frozen = session.prepare(project, scratch)
    repository = Path(__file__).resolve().parents[1]
    frozen["protected_files"].update({str(p): digest(p.read_bytes()) for root in (repository/"bookpipe",repository/"prompts",repository/"catalog")
                                    for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts})
    catalog, path = load_catalog(project)
    pricing = pricing_snapshot(catalog,path,"codex","gpt-6-astra")
    if not pricing["rate"]:
        raise PipelineError("Astra pricing missing; refusing live experiment.")
    for index, name in enumerate(VARIANTS):
        variant = scratch/name
        variant.mkdir(mode=0o700)
        data = copy.deepcopy(frozen)
        data.update(pricing=pricing,pass_config=PASS_CONFIG,execution_mode="independent" if index==0 else "session")
        if index==0:
            data.update(base=BASE_INSTRUCTIONS.read_text().strip(),developer=data["shared_developer"])
        private_json(variant/"frozen.json",data)
        private_json(variant/"manifest.json",{"frozen_sha256":digest(data),"canonical_p2_input_sha256":digest(data["p2_inputs"]),
                       "pass_config":PASS_CONFIG,"developer_sha256":digest(data["developer"]),"schema_sha256":digest(session.v2.encode_value(data["schema"]))})
    private_json(scratch/"frozen.json",frozen)
    manifest=read_json(scratch/"manifest.json")
    manifest.update(frozen_sha256=digest(frozen),execution_order=list(VARIANTS),pass_config=PASS_CONFIG)
    private_json(scratch/"manifest.json",manifest)
    verify_start(scratch)
    return manifest


def verify_start(scratch):
    variants=[read_json(scratch/n/"frozen.json") for n in VARIANTS]
    if any(digest(v)!=read_json(scratch/n/"manifest.json")["frozen_sha256"] for v,n in zip(variants,VARIANTS)):
        raise PipelineError("Frozen variant changed.")
    if any(session.v2.encode_value(v["p2_inputs"]).encode()!=session.v2.encode_value(variants[0]["p2_inputs"]).encode() for v in variants):
        raise PipelineError("Starting semantic input differs.")
    if variants[0]["p2_wire"]!=variants[1]["p2_wire"] or variants[0]["schema"]!=variants[1]["schema"]:
        raise PipelineError("Starting wire/schema differs.")
    if any(v["pass_config"]!=PASS_CONFIG for v in variants):
        raise PipelineError("Unexpected Astra model/effort split.")
    return True


def run(scratch):
    verify_start(scratch)
    for name in VARIANTS:
        summary=session.run(scratch/name,timeout=1800)
        if not all(summary["checks"].values()):
            raise PipelineError(f"Variant {name} failed: inspect preserved evidence; do not restart.")
    return compare(scratch)


def compare(scratch):
    summaries=[read_json(scratch/n/"summary.json") for n in VARIANTS]
    frozen=read_json(scratch/"frozen.json")
    outputs=[{n:read_json(scratch/name/f"P{n}.accepted.json") for n in range(2,6)} for name in VARIANTS]
    directory=scratch/"comparison";directory.mkdir(exist_ok=True)
    source=frozen["p2_inputs"]["SOURCE_BLOCKS"]
    maps=[{n:{r["id"]:r["text"] for r in out[n]["translations"]} for n in (3,5)} for out in outputs]
    flags=[set(c["block_id"] for c in out[4]["corrections"]) for out in outputs]
    union=flags[0]|flags[1]
    similarities={b["id"]:difflib.SequenceMatcher(None,maps[0][5][b["id"]],maps[1][5][b["id"]],autojunk=False).ratio() for b in source}
    choices={}
    def add(ids,category,count):
        for bid in list(ids)[:count]:choices.setdefault(bid,[]).append(category)
    add([b for b in sorted(similarities,key=lambda b:(similarities[b],b)) if len(maps[0][5][b])+len(maps[1][5][b])>100],"largest difference",5)
    add([b["id"] for b in sorted(source,key=lambda b:(-sum(b["text"].count(q) for q in ("‘","“",'"')),b["id"])) if any(q in b["text"] for q in ("‘","“",'"'))],"dialogue",4)
    terms=re.compile(r'\d|quantum|weapon|gravity|ship|missile|sensor|acceler|light|field|orbit',re.I)
    add([b["id"] for b in sorted(source,key=lambda b:(-len(terms.findall(b["text"])),b["id"])) if terms.search(b["text"]) and len(b["text"])>80],"technical/semantic",4)
    add(sorted(union),"P4 touched",5)
    add([b for b in sorted(similarities,key=lambda b:(-similarities[b],b)) if len(maps[0][5][b])+len(maps[1][5][b])>100],"similarity control",4)
    # Preserve source order and include every corrected block separately for audit-bias analysis.
    records=[]
    for b in source:
        bid=b["id"]
        if bid not in choices and bid not in union:continue
        records.append({"id":bid,"sample_categories":choices.get(bid,[]),"english":b["text"],"similarity":similarities[bid],
                        "A":{"P3":maps[0][3][bid],"P4":[c for c in outputs[0][4]["corrections"] if c["block_id"]==bid],"P5":maps[0][5][bid]},
                        "B":{"P3":maps[1][3][bid],"P4":[c for c in outputs[1][4]["corrections"] if c["block_id"]==bid],"P5":maps[1][5][bid]}})
    private_json(directory/"samples.json",records)
    text=[]
    for record in records:
        text += [f"\n## {record['id']} ({', '.join(record['sample_categories']) or 'P4 union'})", "English: "+record["english"]]
        for variant in ("A","B"):
            text += [variant+" P3: "+record[variant]["P3"],variant+" P4: "+json.dumps(record[variant]["P4"],ensure_ascii=False),variant+" P5: "+record[variant]["P5"]]
    (directory/"samples.md").write_text('\n\n'.join(text))
    stats=[]
    for out in outputs:
        stat={str(n):session.sanity(n,out[n]) for n in range(2,6)}
        stat["2"].update(issue_types=dict(Counter(i["type"] for i in out[2]["issues"])),confidence=dict(Counter(i["confidence"] for i in out[2]["issues"])))
        stat["4"]["corrected_blocks"]=sorted(set(c["block_id"] for c in out[4]["corrections"]))
        draft={t["id"]:t["text"] for t in out[3]["translations"]}
        changed=[t["id"] for t in out[5]["translations"] if t["text"]!=draft[t["id"]]]
        stat["5"]["draft_to_final_changed_blocks"]=changed
        stat["5"]["unflagged_edited_blocks"]=sorted(set(changed)-set(stat["4"]["corrected_blocks"]))
        for n in (3,5):stat[str(n)]["exact_coverage_order"]=[t["id"] for t in out[n]["translations"]]==[b["id"] for b in source]
        stats.append(stat)
    diff={}
    for key,left in summaries[0]["totals"].items():
        right=summaries[1]["totals"].get(key)
        diff[key]={"A":left,"B":right,"difference":right-left if right is not None and left is not None else None,
                   "difference_percent":(right-left)/left*100 if left and right is not None else None}
    result={"repository_head":frozen["repository_head"],"scratch":str(scratch),"pass_config":PASS_CONFIG,"execution_order":list(VARIANTS),
            "variants":dict(zip(VARIANTS,summaries)),"comparison":diff,"quality_statistics":dict(zip(VARIANTS,stats)),
            "differing_final_blocks":sum(maps[0][5][b["id"]]!=maps[1][5][b["id"]] for b in source),
            "P4_union_blocks":sorted(union),"sample_count":sum(bool(r["sample_categories"]) for r in records),
            "production_unchanged":all(Path(p).is_file() and digest(Path(p).read_bytes())==h for p,h in frozen["protected_files"].items()),
            "limitations":["One ordered pair; load, sampling, effort transitions and output lengths can affect timing.",
                           "P5 sees P2/sentences in B; P4 ledger is authoritative.","Manual sample review is not a whole-chapter quality score."]}
    private_json(scratch/"summary.json",result)
    return result


SECRET_NAMES={"auth.json","credentials.json",".env","id_rsa","id_ed25519"}
def safe_package(scratch):
    for p in scratch.rglob("*"):
        if p.is_symlink():raise PipelineError("Archive refuses symlinks.")
        if p.is_file() and p.name.lower() in SECRET_NAMES:raise PipelineError("Authentication material remains; refusing archive.")
        if p.is_file():
            data=p.read_bytes()
            if re.search(rb'(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}|Bearer [A-Za-z0-9._-]{30,}|eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,})',data):
                raise PipelineError("Possible credential bytes found; review locally before packaging.")
    required=["summary.md","summary.json","comparison/quality-review.md","comparison/quality-review.json"]
    if any(not (scratch/p).is_file() for p in required):raise PipelineError("Reports incomplete.")
    archive=scratch.with_suffix('.tgz')
    with tarfile.open(archive,'w:gz') as tar:tar.add(scratch,arcname=scratch.name)
    with tarfile.open(archive) as tar:
        names=tar.getnames()
        if any(Path(n).name.lower() in SECRET_NAMES for n in names):raise PipelineError("Archive credential check failed.")
        if any(f"{scratch.name}/{p}" not in names for p in required):raise PipelineError("Archive report check failed.")
        for name in VARIANTS:
            if f"{scratch.name}/{name}/summary.json" not in names:raise PipelineError("Variant missing from archive.")
    return {"archive":str(archive),"bytes":archive.stat().st_size,"sha256":digest(archive.read_bytes()),"members":len(names)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('prepare','run','compare','package'))
    parser.add_argument('--scratch',type=Path,required=True)
    parser.add_argument('--project',type=Path,default=Path('/home/user/translations/salvation-03'))
    args=parser.parse_args();scratch=args.scratch.resolve()
    result=prepare(args.project,scratch) if args.command=='prepare' else run(scratch) if args.command=='run' else compare(scratch) if args.command=='compare' else safe_package(scratch)
    print(json.dumps({k:v for k,v in result.items() if k in ('archive','bytes','sha256','members','scratch','production_unchanged')},ensure_ascii=False))

if __name__=='__main__':main()
