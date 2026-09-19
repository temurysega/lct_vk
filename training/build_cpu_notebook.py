"""Build the standalone Colab training/export notebook for the 4 GB CPU VPS."""

import json
from pathlib import Path
from textwrap import dedent

from training.build_notebook import ENVIRONMENT_SETUP


def notebook() -> dict:
    cells = []

    def add(kind, source):
        cell = {
            "cell_type": kind,
            "id": f"cpu-{len(cells):02d}",
            "metadata": {},
            "source": dedent(source).strip().splitlines(keepends=True),
        }
        if kind == "code":
            cell.update(execution_count=None, outputs=[])
        cells.append(cell)

    add(
        "markdown",
        """
    # LCT: обучение в Colab → автономный VPS без GPU

    Загрузите папку **lct_cpu** целиком в «Мой диск» аккаунта с Colab A100.
    GitHub-аккаунт не нужен. Выберите A100 и выполняйте ячейки по порядку.
    Обучаем **Qwen2.5-1.5B-Instruct** выбирать макеты; базовая модель составляет
    текстовый план. Старый адаптер 7B несовместим: нужно новое обучение.
    Данные — слабая разметка из PPTX, не экспертная оценка дизайна.

    В результате получите базовую модель Q4_K_M и отдельный LoRA в GGUF.
    На VPS обе загружаются одним llama.cpp; LoRA применяется только к выбору
    макетов. После скачивания архива Colab можно полностью выключить.
    Полный запуск обучения и экспорта требует свободного места на Drive (от 8 ГБ)
    и локальном диске Colab (от 15 ГБ). Время зависит от среды.
    """,
    )
    add("markdown", "## 1. Drive и проверка файлов")
    add(
        "code",
        """
    from google.colab import drive
    drive.mount('/content/drive')
    from pathlib import Path
    import hashlib, json, os, shutil, subprocess, sys
    ROOT = Path('/content/drive/MyDrive/lct_cpu')
    manifest = json.loads((ROOT / 'data/manifest.json').read_text())
    assert manifest.get('profile') == 'cpu', 'Нужен новый комплект lct_cpu'
    for relative, expected in manifest['files'].items():
        path = (ROOT / relative).resolve()
        assert path.is_relative_to(ROOT.resolve()) and path.is_file(), relative
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, relative
    PROFILE = json.loads((ROOT / 'scripts/cpu_profile.json').read_text())
    print('Строк:', manifest['train_rows'], 'проверка:', manifest['validation_rows'])
    subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv'], check=True)
    """,
    )
    add(
        "markdown",
        """
    ## 2. Окружение Python 3.12
    uv устанавливает отдельный Python и pip, обходя ошибку системного `venv`.
    Вывод установки отображается в ячейке. Ядро Colab перезапускать не нужно.
    """,
    )
    add(
        "code",
        ENVIRONMENT_SETUP
        + dedent("""
    TRAIN_ENV, TRAIN_PY = ensure_python312(globals().get('TRAIN_ENV', '/content/lct-cpu-train-py312'))
    run_visible([TRAIN_PY, '-m', 'pip', 'install', '-r', str(ROOT / 'scripts/requirements-a100.txt')])
    os.environ['HF_HOME'] = '/content/lct-model-cache'
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    LOCAL_SCRIPTS = Path('/content/lct-cpu-scripts')
    shutil.copytree(ROOT / 'scripts', LOCAL_SCRIPTS, dirs_exist_ok=True)
    """),
    )
    add(
        "markdown",
        """
    ## 3. Обучение с полными чекпоинтами
    `RESUME='auto'` продолжает прерванный запуск; завершённый не повторяет.
    Для нового эксперимента используйте `'none'`, для конкретного — имя из `runs/`.
    Чекпоинт после первого шага, каждых 10 шагов и каждой эпохи включает веса,
    оптимизатор, scheduler, RNG и Trainer. На Drive хранятся две целые копии
    с контрольными суммами. После обрыва повторите ячейки 1–3 с прежними параметрами.
    """,
    )
    add(
        "code",
        """
    RESUME = 'auto'
    EPOCHS = 2
    MAX_LENGTH = 3072
    run_visible([TRAIN_PY, '-u', str(LOCAL_SCRIPTS / 'train_layout.py'),
        '--drive-root', str(ROOT), '--model', PROFILE['base_model'],
        '--revision', PROFILE['base_revision'], '--epochs', str(EPOCHS),
        '--max-length', str(MAX_LENGTH), '--save-steps', '10',
        '--keep-checkpoints', '2', '--resume', RESUME])
    latest = json.loads((ROOT / 'weights/latest.json').read_text())
    metrics = json.loads((ROOT / latest['adapter_dir'] / 'metrics.json').read_text())
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    """,
    )
    add(
        "markdown",
        """
    ## 4. Сборка инструментов экспорта
    Конвертер имеет отдельное окружение, поэтому его зависимости не меняют обучение.
    Версия llama.cpp совпадает с Docker-образом VPS. Сборка использует только CPU.
    """,
    )
    add(
        "code",
        """
    EXPORT_ENV, EXPORT_PY = ensure_python312(globals().get('EXPORT_ENV', '/content/lct-cpu-export-py312'))
    run_visible(['apt-get', 'update', '-qq'])
    run_visible(['apt-get', 'install', '-y', '-qq', 'git', 'cmake', 'build-essential', 'libcurl4-openssl-dev'])
    LLAMA = Path('/content/lct-llama-cpu')
    LLAMA.mkdir(exist_ok=True)
    if not (LLAMA / '.git').exists():
        run_visible(['git', '-C', str(LLAMA), 'init'])
        run_visible(['git', '-C', str(LLAMA), 'remote', 'add', 'origin', 'https://github.com/ggml-org/llama.cpp.git'])
    run_visible(['git', '-C', str(LLAMA), 'fetch', '--depth', '1', 'origin', PROFILE['llama_cpp_revision']])
    run_visible(['git', '-C', str(LLAMA), 'checkout', '--detach', PROFILE['llama_cpp_revision']])
    run_visible([EXPORT_PY, '-m', 'pip', 'install', '-r', str(LLAMA / 'requirements/requirements-convert_hf_to_gguf.txt'), 'psutil>=5.9'])
    run_visible(['cmake', '-S', str(LLAMA), '-B', str(LLAMA / 'build'),
        '-DCMAKE_BUILD_TYPE=Release', '-DGGML_CUDA=OFF', '-DLLAMA_BUILD_TESTS=OFF'])
    run_visible(['cmake', '--build', str(LLAMA / 'build'), '--config', 'Release',
        '--target', 'llama-quantize', 'llama-server', '--parallel', '2'])
    """,
    )
    add(
        "markdown",
        """
    ## 5. GGUF, проверка на CPU и сохранение на Drive
    Экспортируются база Q4_K_M и адаптер F16 GGUF. Проверяются реальная загрузка
    на CPU, память, JSON-план и весь validation после квантования.
    LoRA автоматически включается в приложении, только если превосходит базу
    и эвристику на этой проверке и выдаёт корректные ответы. Иначе веса сохраняются,
    но выбор макетов остаётся эвристическим. Это не замена визуальной проверке PPTX.
    Во время экспорта Colab остаётся включённым. При сбое повторите эту ячейку;
    после сброса среды восстановите 1, 2 и 4 — обучение повторять не требуется.
    """,
    )
    add(
        "code",
        """
    run_visible([EXPORT_PY, '-u', str(LOCAL_SCRIPTS / 'export_cpu.py'),
        '--drive-root', str(ROOT), '--llama-root', str(LLAMA)])
    """,
    )
    add(
        "markdown",
        """
    ## 6. Скачать готовый комплект
    Архив `cpu_bundle.zip` содержит обе модели, конфигурацию и отчёт проверки.
    Распакуйте в `lct_vk/models/` на VPS; получится `models/cpu/`.
    Инструкция запуска — `training/CPU_README.md` в репозитории и README внутри архива.
    После скачивания можно завершить сеанс Colab: VPS работает самостоятельно.
    """,
    )
    add(
        "code",
        """
    from google.colab import files
    exported = json.loads((ROOT / 'exports/latest.json').read_text())
    archive = (ROOT / exported['archive']).resolve()
    assert archive.is_relative_to(ROOT.resolve())
    sha = hashlib.sha256()
    with archive.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            sha.update(block)
    assert sha.hexdigest() == exported['sha256'], 'Архив на Drive повреждён'
    print('LoRA включён:', exported['enable_layout_adapter'])
    print('Архив:', archive)
    files.download(str(archive))
    """,
    )
    return {
        "cells": cells,
        "metadata": {
            "accelerator": "GPU",
            "colab": {"gpuType": "A100"},
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


if __name__ == "__main__":
    destination = (
        Path(__file__).resolve().parents[1]
        / "examples/BrandDeck_CPU_Training_Export.ipynb"
    )
    destination.write_text(
        json.dumps(notebook(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(destination)
