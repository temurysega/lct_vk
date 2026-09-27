"""Acceptance rendering only; does not patch the application's export pipeline."""
from pathlib import Path
import hashlib
import json
import subprocess
import time
import fitz
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'reports/criteria-audit'

def previews(pdf):
    with fitz.open(pdf) as doc:
        thumbs=[]
        for i,page in enumerate(doc,1):
            png=pdf.parent/f'powerpoint-slide-{i:02d}.png'
            page.get_pixmap(matrix=fitz.Matrix(1200/page.rect.width,1200/page.rect.width),alpha=False).save(png)
            im=Image.open(png).convert('RGB');im.thumbnail((480,270))
            thumbs.append((i,im.copy()))
        sheet=Image.new('RGB',(960,300*((len(thumbs)+1)//2)),'#dadada');draw=ImageDraw.Draw(sheet)
        for j,(i,im) in enumerate(thumbs):
            x=(j%2)*480;y=(j//2)*300
            sheet.paste(im,(x,y+24));draw.text((x+8,y+5),f'{pdf.parent.parent.name}/{pdf.parent.name} | PowerPoint | slide {i}',fill='black')
        sheet.save(pdf.parent/'powerpoint-contact-sheet.jpg',quality=90)
        (pdf.parent/'powerpoint-text.txt').write_text('\n'.join(f'=== SLIDE {i} ===\n{p.get_text()}' for i,p in enumerate(doc,1)))

def main():
    records=[]
    for source in sorted((OUT/'artifacts').glob('*/*/*.pptx')):
        pdf=source.parent/'powerpoint.pdf';log=source.parent/'powerpoint-export.json'
        before=hashlib.sha256(source.read_bytes()).hexdigest();started=time.perf_counter()
        if not pdf.exists():
            command=['osascript',str(ROOT/'examples/export_powerpoint_macos.applescript'),str(source),str(pdf)]
            try:
                r=subprocess.run(command,capture_output=True,text=True,timeout=90)
                record={'renderer':'Microsoft PowerPoint for macOS','command':command,'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr,'seconds':round(time.perf_counter()-started,3),'pdf_exists':pdf.exists()}
            except subprocess.TimeoutExpired:
                record={'renderer':'Microsoft PowerPoint for macOS','status':'timeout','seconds':90}
            log.write_text(json.dumps(record,ensure_ascii=False,indent=2))
        else: record=json.loads(log.read_text()) if log.exists() else {'pdf_exists':True}
        record['source_unchanged']=before==hashlib.sha256(source.read_bytes()).hexdigest()
        if pdf.exists():
            previews(pdf)
            with fitz.open(pdf) as doc:record['page_count']=len(doc)
        records.append({'file':str(source.relative_to(OUT)),**record})
        (OUT/'evidence/powerpoint-exports.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
        print(source.parent,record.get('exit_code'),record.get('page_count'),flush=True)

if __name__=='__main__':main()
