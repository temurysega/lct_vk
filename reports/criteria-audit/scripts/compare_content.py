"""Independent PDF literal comparison; not semantic or model verification."""
from pathlib import Path
from collections import Counter
import json
import re
import pymupdf

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'reports/criteria-audit'

def norm(text):
    return re.sub(r'\W+', '', text.casefold())

rows = []
for base in ['with-renderer/artifacts', 'additional-unseen/artifacts']:
    for path in sorted((OUT / base).glob('*/*/coverage_report.json')):
        coverage = json.loads(path.read_text())
        manifest = json.loads((path.parent / 'manifest.json').read_text())
        pdf = next(path.parent.glob('*.pdf'))
        with pymupdf.open(pdf) as doc:
            text = norm('\n'.join(page.get_text() for page in doc))
            fonts = Counter(f[3] for p in doc for f in p.get_fonts())
            pages = len(doc)
        units = coverage['units']
        rows.append({
            'deck': str(path.parent.relative_to(OUT)),
            'source_sha256': coverage['source_sha256'],
            'units': len(units), 'pdf_pages': pages,
            'missing_from_plan': [u['source_id'] for u in units if not u['in_plan']],
            'missing_from_pptx': [u['source_id'] for u in units if not u['in_pptx']],
            'not_literally_found_in_pdf': [u for u in units if norm(u['text']) not in text],
            'pdf_fonts': dict(fonts), 'planner_mode': manifest['planner_mode'],
            'method': 'PPTX/plan fields from product coverage; PDF text independently normalized, no semantic verification',
        })
(OUT/'evidence/content-comparison-all.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
print(json.dumps({'decks': len(rows), 'source_hashes': sorted({r['source_sha256'] for r in rows}), 'pdf_unmatched': {r['deck']: len(r['not_literally_found_in_pdf']) for r in rows}}, ensure_ascii=False))
