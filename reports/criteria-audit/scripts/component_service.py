"""Reproduce the controlled service probe; no brief, model, or semantic claims."""
import json
import os
from pathlib import Path
import shutil
import tempfile
from slide_agent.service import _generate_from_plan

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT/'reports/criteria-audit'
meta = json.loads((OUT/'artifacts/vk-education/balanced/manifest.json').read_text())
plan = json.loads((OUT/'artifacts/component-visuals/vk-education/controlled-plan.json').read_text())
workspace = Path(os.getenv('AUDIT_TMP') or tempfile.mkdtemp(prefix='branddeck-component-audit-'))/'component-service'
source = '\n'.join(s['title'] for s in plan['slides'])
result = _generate_from_plan(plan=plan, template_dir=Path(meta['template_dir']), source_text=source, source_label='controlled-component-probe', workspace_path=workspace, qa_retries=2, export_formats=('pdf','html'))
dest = Path(os.getenv('AUDIT_COMPONENT_OUTPUT') or str(OUT/'artifacts/component-service/vk-education'))
dest.mkdir(parents=True, exist_ok=True)
run = Path(result['presentation_dir'])
for file in run.glob('*.json'):
    shutil.copy2(file, dest/file.name)
shutil.copy2(result['output'], dest/'component-service.pptx')
for fmt, value in result.get('exports',{}).get('artifacts',{}).items():
    file = Path(value['path'] if isinstance(value,dict) else value)
    shutil.copy2(file, dest/f'component-service.{fmt}')
(dest/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({'status':result['status'], 'qa':result['qa']['status'], 'seconds':result['elapsed_seconds']},ensure_ascii=False))
