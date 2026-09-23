# Paste this entire file into Colab in place of the failed step 5 code cell.
# If the runtime was reset, first run setup steps 1, 2 and 4, skipping training.
from pathlib import Path
import shutil

checker = Path(LOCAL_SCRIPTS) / "check_gguf.py"
source = checker.read_text(encoding="utf-8")
old = '                with urllib.request.urlopen(url + "/lora-adapters") as response:'
reset = '                post_json(url + "/lora-adapters", [{"id": 0, "scale": 0.0}])'
if reset in source or "disable_layout_adapter(url)" in source:
    print("LoRA startup fix is already installed.")
elif source.count(old) == 1:
    patched = source.replace(old, reset + "\n" + old, 1)
    compile(patched, str(checker), "exec")
    backup = checker.with_suffix(".py.before-lora-fix")
    if not backup.exists():
        shutil.copy2(checker, backup)
    checker.write_text(patched, encoding="utf-8")
    print("LoRA startup fixed; the disabled-state check is preserved.")
else:
    raise RuntimeError("Unexpected check_gguf.py version; no files were changed.")

run_visible([
    EXPORT_PY, "-u", str(Path(LOCAL_SCRIPTS) / "export_cpu.py"),
    "--drive-root", str(ROOT), "--llama-root", str(LLAMA),
])
