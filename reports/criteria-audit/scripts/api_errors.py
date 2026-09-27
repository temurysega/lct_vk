"""Additional negative tests against an isolated application workspace."""
import httpx,json,secrets,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'reports/criteria-audit/evidence'
c=httpx.Client(base_url='http://127.0.0.1:8765',timeout=60)
r=c.post('/api/auth/register',json={'username':'errors_'+secrets.token_hex(4),'password':secrets.token_urlsafe(24),'position':'Audit'});r.raise_for_status();uid=r.json()['id']
template=ROOT.parent/'VK Tech шаблон.pptx'
r=c.post('/v1/templates/analyze',files={'file':(template.name,template.read_bytes())},data={'offline':'true'});r.raise_for_status();tid=r.json()['template_id'];workspace=Path(r.json()['path']).parent.parent
records=[]
for name,filename,data in [('unsupported content','data.exe',b'invalid'),('empty content','data.md',b''),('PDF materials','official-task.pdf',(ROOT.parent/'4. VK Tech.pdf').read_bytes()),('malformed JSON','data.json',b'{ invalid }')]:
 before=set((workspace/'jobs').glob('*'))
 r=c.post('/v1/presentations/jobs',data={'template_id':tid,'offline':'true','mode':'source','slide_count':5},files={'content_file':(filename,data)})
 result={'case':name,'http_status':r.status_code,'response':r.json()}
 if r.status_code==202:
  for _ in range(100):
   job=c.get('/v1/jobs/'+r.json()['job_id']).json()
   if job['status'] in ['failed','completed']:break
   time.sleep(.1)
  result['terminal_job']=job
 added=set((workspace/'jobs').glob('*'))-before
 result['created_job_records']=[json.loads((p/'job.json').read_text()) for p in added]
 records.append(result)
source_manifest=json.loads((ROOT/'reports/criteria-audit/artifacts/vk-tech/balanced/manifest.json').read_text())
foreign_id=source_manifest['presentation_id']
r=c.get(f'/v1/presentations/{foreign_id}/download')
records.append({'case':'cross-account download','http_status':r.status_code,'response':r.json()})
r=c.post('/v1/presentations/generate',data={'template_id':tid,'offline':'true','mode':'source','slide_count':5,'content':(ROOT/'examples/acceptance_content.md').read_text()})
records.append({'case':'synchronous source generation without exports','http_status':r.status_code,'status':r.json().get('status'),'planner_mode':r.json().get('planner_mode'),'presentation_id':r.json().get('presentation_id')})
(OUT/'api-negative.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
print(json.dumps([{'case':i['case'],'http_status':i['http_status'],'status':i.get('status') or i.get('terminal_job',{}).get('status')} for i in records],ensure_ascii=False))
