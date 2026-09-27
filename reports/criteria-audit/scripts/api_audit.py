"""Read-only product audit. Run from repo root against an isolated server."""
from pathlib import Path
import hashlib
import json
import os
import secrets
import shutil
import time
import httpx

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.getenv("AUDIT_OUTPUT", str(ROOT / "reports/criteria-audit"))).resolve()
BASE = os.getenv("AUDIT_BASE_URL", "http://127.0.0.1:8765")

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))

def main():
    for name in ['evidence','artifacts','sources']:
        (OUT/name).mkdir(parents=True,exist_ok=True)
    client = httpx.Client(base_url=BASE, timeout=240)
    events = []
    def request(label, method, url, **kwargs):
        started = time.perf_counter()
        r = client.request(method, url, **kwargs)
        try:
            body = r.json()
        except ValueError:
            body = {"bytes":len(r.content), "content_type":r.headers.get("content-type")}
        events.append({"label":label,"method":method,"url":url,"status":r.status_code,"seconds":round(time.perf_counter()-started,3),"body":body})
        save(OUT/"evidence/api-events.json", events)
        return r
    request("health", "GET", "/health")
    request("config", "GET", "/api/config")
    request("unauthorized templates", "GET", "/v1/templates")
    r = client.post("/api/auth/register", json={"username":"audit_"+secrets.token_hex(5),"password":secrets.token_urlsafe(24),"position":"criteria audit"})
    r.raise_for_status()  # Never persist passwords or session cookies.
    request("empty template", "POST", "/v1/templates/analyze", files={"file":("empty.pptx",b"")})
    request("corrupt template", "POST", "/v1/templates/analyze", files={"file":("broken.pptx",b"not a powerpoint file")})
    request("wrong extension", "POST", "/v1/templates/analyze", files={"file":("bad.exe",b"invalid")})
    templates = [
        ("vk-tech", ROOT.parent / "VK Tech шаблон.pptx"),
        ("vk-workspace", ROOT.parent / "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx"),
        ("vk-education", ROOT.parent / "Шаблон презентации VK Education.pptx"),
        ("unseen-brutalism", ROOT / "external-fixtures/expansion-20260922/templates/pptmaster-brutalism_field_guide.pptx"),
    ]
    if os.getenv('AUDIT_TEMPLATE_FILTER') == 'unseen-onocom':
        templates=[('unseen-onocom',ROOT/'external-fixtures/expansion-20260922/templates/onocom-powerpoint-template.pptx')]
    content = (ROOT / "examples/acceptance_content.md").read_text()
    shutil.copy2(ROOT/"examples/acceptance_content.md",OUT/"sources/audit-content.md")
    results=[]
    for slug, template in templates:
        started = time.perf_counter()
        r=request(slug+" analysis","POST","/v1/templates/analyze",data={"offline":"true"},files={"file":(template.name,template.read_bytes())})
        r.raise_for_status()
        analyzed=r.json(); tid=analyzed["template_id"]
        folder=OUT/"artifacts"/slug; folder.mkdir(parents=True,exist_ok=True)
        save(folder/"input.json",{"path":str(template),"sha256":hashlib.sha256(template.read_bytes()).hexdigest(),"content":"sources/audit-content.md","slide_count":10,"mode":"source","offline":True})
        analysis_path=Path(analyzed["path"])
        for p in analysis_path.glob("*.json"):
            shutil.copy2(p,folder/("analysis-"+p.name))
        analysis_seconds=round(time.perf_counter()-started,3)
        if slug=="vk-tech":
            again=request("cached analysis","POST","/v1/templates/analyze",data={"offline":"true"},files={"file":(template.name,template.read_bytes())}).json()
            save(OUT/"evidence/cache-reuse.json",{"same_template_id":again["template_id"]==tid,"design_hash":hashlib.sha256((analysis_path/"design_system.json").read_bytes()).hexdigest()})
            request("no content","POST","/v1/presentations/jobs",data={"template_id":tid})
            request("invalid count","POST","/v1/presentations/jobs",data={"template_id":tid,"content":content,"slide_count":2})
            request("unknown template","POST","/v1/presentations/jobs",data={"template_id":"does-not-exist","content":content})
            request("brief without model","POST","/v1/presentations/generate",data={"template_id":tid,"content":"Питч новой функции поиска по регламентам. Пилот на 150 сотрудников, 6 недель, команда 3 человека.","mode":"brief","purpose":"feature","slide_count":10})
        started=time.perf_counter()
        image_files=[("image_files",(p.name,p.read_bytes(),"image/png")) for p in sorted((ROOT/"examples/acceptance_images").glob("*.png"))]
        r=request(slug+" submit","POST","/v1/presentations/jobs",data={"template_id":tid,"content":content,"slide_count":10,"offline":"true","variants":"true","export_all":"true","mode":"source"},files=image_files)
        r.raise_for_status(); job_id=r.json()["job_id"]
        stages=[]
        while time.perf_counter()-started<600:
            job=client.get(f"/v1/jobs/{job_id}").json()
            stage={k:job.get(k) for k in ["status","stage","progress"]}
            if not stages or stages[-1]!=stage: stages.append(stage)
            if job["status"] not in {"queued","running"}: break
            time.sleep(0.3)
        save(folder/"job.json",job);save(folder/"job-stages.json",stages)
        batch=job.get("batch",{})
        for variant in batch.get("variants",[]):
            variant_slug=variant["variant"]["id"]
            variant_dir=folder/variant_slug;variant_dir.mkdir(exist_ok=True)
            run_dir=Path(variant["presentation_dir"])
            for p in run_dir.glob("*.json"): shutil.copy2(p,variant_dir/p.name)
            pid=variant["presentation_id"]
            for fmt in ["pptx","pdf","html"]:
                d=request(f"{slug}/{variant_slug} download {fmt}","GET",f"/v1/presentations/{pid}/download?format={fmt}")
                if d.status_code==200: (variant_dir/f"{slug}-{variant_slug}.{fmt}").write_bytes(d.content)
            request(f"{slug}/{variant_slug} preview","GET",f"/v1/presentations/{pid}/preview/1")
        request(slug+" batch job download","GET",f"/v1/jobs/{job_id}/download")
        result={"template":slug,"analysis_seconds":analysis_seconds,"generation_seconds":round(time.perf_counter()-started,3),"job_status":job["status"],"template_id":tid,"batch_id":batch.get("batch_id"),"variants":[{"variant":v["variant"]["id"],"status":v["status"],"planner_mode":v["planner_mode"],"elapsed_seconds":v["elapsed_seconds"],"qa_status":v["qa"]["status"],"qa_issues":len(v["qa"]["issues"]),"export_status":v["exports"]["status"]} for v in batch.get("variants",[])]}
        results.append(result);save(OUT/"evidence/api-summary.json",results)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    request("templates persisted","GET","/v1/templates")
    request("history persisted","GET","/v1/batches")

if __name__=="__main__": main()
