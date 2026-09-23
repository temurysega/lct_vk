"""Replace the failed step 5 with this cell. Do not repeat training.

After a Colab reset, first run steps 1, 2, 4 from the notebook.
Only temporary runtime scripts are patched: the trained dataset stays immutable.
"""

from pathlib import Path
import hashlib
import json
import shutil


DISABLE_HELPER = '''def disable_layout_adapter(url: str) -> None:
    with urllib.request.urlopen(url + "/lora-adapters", timeout=30) as response:
        loaded = json.load(response)
    if (not isinstance(loaded, list) or len(loaded) != 1
            or not isinstance(loaded[0], dict) or loaded[0].get("id") != 0):
        raise ValueError(f"Expected exactly one loaded layout adapter with id=0: {loaded!r}")
    post_json(url + "/lora-adapters", [{"id": 0, "scale": 0.0}], timeout=30)
    with urllib.request.urlopen(url + "/lora-adapters", timeout=30) as response:
        loaded = json.load(response)
    if (not isinstance(loaded, list) or len(loaded) != 1
            or not isinstance(loaded[0], dict) or loaded[0].get("id") != 0
            or loaded[0].get("scale") != 0):
        raise ValueError(f"Layout adapter did not confirm disabled default: {loaded!r}")


'''

OLD_CHECK = '''                with urllib.request.urlopen(url + "/lora-adapters") as response:
                    loaded = json.load(response)
                if not loaded or loaded[0]["id"] != 0 or loaded[0]["scale"] != 0:
                    raise ValueError(
                        "The layout adapter must be loaded and disabled by default"
                    )'''

DEPLOYMENT_README = """# BrandDeck CPU export

Base: Qwen2.5-1.5B-Instruct Q4_K_M; separate F16 GGUF layout LoRA.
Unpack cpu_bundle.zip into the application's models directory (models/cpu/).
Use the repository's compose.cpu.yaml and deployment instructions.
Load the layout adapter with --lora-scaled /models/layout-lora-f16.gguf:0.
Planning uses scale 0; layout selection uses scale 1 only if validation approved it.
Keep deployment.env, bundle.json, cpu_report.json and test_report.json with the weights.
Test is a final held-out evaluation; it must not change the validation decision.
The reported weak-label accuracy is not a visual-quality score.
"""


def patch_checker(source: str) -> str:
    if "def disable_layout_adapter(" in source and "disable_layout_adapter(url)" in source:
        return source
    if source.count(OLD_CHECK) != 1 or source.count("def evaluate(") != 1:
        raise ValueError("Unexpected checker version; no patch applied")
    patched = source.replace(OLD_CHECK, "                disable_layout_adapter(url)", 1)
    patched = patched.replace("def evaluate(", DISABLE_HELPER + "def evaluate(", 1)
    compile(patched, "check_gguf.py", "exec")
    return patched


def patch_exporter(source: str) -> str:
    marker = '    portable.mkdir(exist_ok=True)\n'
    addition = '''    repair_note = Path(__file__).with_name("export_repair.json")
    if repair_note.is_file():
        shutil.copy2(repair_note, portable / "export_repair.json")
'''
    if addition in source:
        return source
    if source.count(marker) != 1:
        raise ValueError("Unexpected exporter version; no patch applied")
    patched = source.replace(marker, marker + addition, 1)
    compile(patched, "export_cpu.py", "exec")
    return patched


def prepare_repair(root: Path, local_scripts: Path) -> dict:
    root, local_scripts = root.resolve(), local_scripts.resolve()
    if local_scripts == root or local_scripts.is_relative_to(root):
        raise ValueError("LOCAL_SCRIPTS must be outside the immutable Drive bundle")
    manifest_path = root / "data/manifest.json"
    original_manifest = manifest_path.read_bytes()
    manifest = json.loads(original_manifest)
    for relative, expected in manifest["files"].items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Original package checksum mismatch: {relative}")
    # Read the originals afresh: rerunning this repair is deterministic.
    changes = {}
    for name, patch in (("check_gguf.py", patch_checker), ("export_cpu.py", patch_exporter)):
        path = root / "scripts" / name
        original = path.read_text(encoding="utf-8")
        modified = patch(original)
        changes[name] = (original, modified)
    local_scripts.mkdir(parents=True, exist_ok=True)
    shutil.copytree(root / "scripts", local_scripts, dirs_exist_ok=True)
    audit = {"fix": "explicitly disable LoRA default before CPU evaluation",
             "dataset_manifest_sha256": hashlib.sha256(original_manifest).hexdigest(),
             "files": {}}
    for name, (original, modified) in changes.items():
        destination = local_scripts / name
        backup = destination.with_suffix(".py.before-expanded-fix")
        if not backup.exists():
            shutil.copy2(destination, backup)
        destination.write_text(modified, encoding="utf-8")
        audit["files"][name] = {
            "original_sha256": hashlib.sha256((root / "scripts" / name).read_bytes()).hexdigest(),
            "runtime_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        }
    (local_scripts / "export_repair.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    # This supplied bundle lacks README.md; the exporter would otherwise fail
    # while packaging after the lengthy evaluation. No manifest file is changed.
    if not (root / "CPU_README.md").exists() and not (root / "README.md").exists():
        (root / "CPU_README.md").write_text(DEPLOYMENT_README, encoding="utf-8")
    assert manifest_path.read_bytes() == original_manifest
    return audit


def resume_export(namespace: dict) -> None:
    required = ("ROOT", "LOCAL_SCRIPTS", "EXPORT_PY", "LLAMA", "run_visible")
    missing = [key for key in required if key not in namespace]
    if missing:
        raise RuntimeError("Run notebook setup steps 1, 2, 4 first; skip training. Missing: " + ", ".join(missing))
    root, local = Path(namespace["ROOT"]), Path(namespace["LOCAL_SCRIPTS"])
    audit = prepare_repair(root, local)
    print("Export repair installed. Training data, manifest and weights are unchanged.")
    print(json.dumps(audit, indent=2))
    namespace["run_visible"]([
        namespace["EXPORT_PY"], "-u", str(local / "export_cpu.py"),
        "--drive-root", str(root), "--llama-root", str(namespace["LLAMA"]),
    ])
    exported = json.loads((root / "exports/latest.json").read_text())
    test_path = root / Path(exported["archive"]).parent / "cpu/test_report.json"
    if test_path.is_file():
        final_test = json.loads(test_path.read_text())
        print("Final held-out test; do not use for parameter selection:")
        for kind in ("baseline", "adapted"):
            print(kind, {k: v for k, v in final_test[kind].items() if k != "predictions"})
        print("Validation decision:", final_test["frozen_enable_layout_adapter"])
    print("Ready:", root / exported["archive"])


if __name__ == "__main__":
    resume_export(globals())
