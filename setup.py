"""Build the bundled web client before setuptools collects package files."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.egg_info import egg_info

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"


def _frontend_is_current() -> bool:
    index = FRONTEND / "dist" / "index.html"
    if not index.is_file() or not any((FRONTEND / "dist" / "assets").glob("*.js")):
        return False
    built_at = index.stat().st_mtime_ns
    inputs = (
        FRONTEND / "src",
        FRONTEND / "public",
        FRONTEND / "index.html",
        FRONTEND / "package.json",
        FRONTEND / "package-lock.json",
        FRONTEND / "vite.config.ts",
        FRONTEND / "tsconfig.json",
        FRONTEND / "tsconfig.app.json",
    )
    for source in inputs:
        files = source.rglob("*") if source.is_dir() else (source,)
        if any(path.is_file() and path.stat().st_mtime_ns > built_at for path in files):
            return False
    return True


def _build_frontend() -> None:
    if _frontend_is_current():
        return
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if npm is None:
        raise RuntimeError(
            "Building BrandDeck requires npm to bundle the frontend; "
            "install Node.js 22 or provide a current frontend/dist build."
        )
    if not (FRONTEND / "node_modules").is_dir():
        subprocess.run([npm, "ci"], cwd=FRONTEND, check=True)
    subprocess.run([npm, "run", "build"], cwd=FRONTEND, check=True)
    if not _frontend_is_current():
        raise RuntimeError("Frontend build did not produce a current dist/index.html")


class BuildPyWithFrontend(build_py):
    def run(self) -> None:
        _build_frontend()
        super().run()


class EggInfoWithFrontend(egg_info):
    def run(self) -> None:
        _build_frontend()
        super().run()


setup(cmdclass={"build_py": BuildPyWithFrontend, "egg_info": EggInfoWithFrontend})
