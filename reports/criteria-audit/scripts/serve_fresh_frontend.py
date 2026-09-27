"""Serve unchanged backend with the freshly built frontend from a temp directory."""
import os
from pathlib import Path
from fastapi.staticfiles import StaticFiles
import backend.main as main
import uvicorn

main.WEB_ROOT = Path(os.environ['AUDIT_FRONTEND_BUILD'])
for route in main.app.routes:
    if getattr(route,'path',None)=='/assets':
        route.app=StaticFiles(directory=main.WEB_ROOT/'assets')
uvicorn.run(main.app,host='127.0.0.1',port=int(os.getenv('AUDIT_PORT','8766')))
