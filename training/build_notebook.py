"""Generate the standalone, Drive-only Colab notebook from reviewable cells."""

import json
from pathlib import Path
from textwrap import dedent


def notebook() -> dict:
    cells = []

    def add(kind: str, source: str) -> None:
        cell = {
            "cell_type": kind,
            "id": f"lct-{len(cells):02d}",
            "metadata": {},
            "source": dedent(source).strip().splitlines(keepends=True),
        }
        if kind == "code":
            cell.update(execution_count=None, outputs=[])
        cells.append(cell)

    add(
        "markdown",
        """
        # LCT: дообучение на A100 через Google Drive

        GitHub и привязка аккаунта не нужны. Загрузите подготовленную папку **lct**
        в «Мой диск» аккаунта с A100. Откройте этот файл через **Файл → Загрузить блокнот**.
        Выберите GPU A100 и запускайте ячейки по порядку до раздела «Остановка».

        Обучаем **LoRA выбора макетов** для Qwen2.5-7B-Instruct. Содержание презентации
        планирует базовая модель, вёрстку выполняет приложение на компьютере.
        Это текстовая модель: изображения в неё не передаются.

        Данные автоматически извлечены из ваших PPTX. Правильный ответ — исходный
        exemplar среди предложенных кандидатов. Это **слабая разметка**, а не
        экспертная оценка лучшего дизайна. Три перестановки кандидатов не являются
        тремя независимыми примерами. VK WorkSpace целиком оставлен для проверки;
        совпадающие группы содержимого исключены из проверки.

        На GPU этот код ещё не проверен. Notebook сравнивает базовую модель,
        адаптер и эвристический алгоритм. Он не объявляет обучение улучшением автоматически.
        Источники: [Qwen](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct),
        [PEFT](https://huggingface.co/docs/peft/main/en/developer_guides/quantization),
        [vLLM LoRA](https://docs.vllm.ai/en/latest/features/lora/).
    """,
    )
    add(
        "markdown",
        """
        ## 1. Подключить Drive и проверить комплект
        Разрешите доступ к Drive аккаунта с A100. Ожидаемый путь: `/content/drive/MyDrive/lct`.
        Ячейка проверяет файлы до установки зависимостей и расходования времени на обучение.
    """,
    )
    add(
        "code",
        """
        from google.colab import drive
        drive.mount('/content/drive')
        from pathlib import Path
        import hashlib, json, os, shutil, subprocess, sys, time
        ROOT = Path('/content/drive/MyDrive/lct')
        assert (ROOT / 'data/manifest.json').is_file(), 'Загрузите папку lct целиком в Мой диск'
        manifest = json.loads((ROOT / 'data/manifest.json').read_text())
        for relative, expected in manifest['files'].items():
            path = (ROOT / relative).resolve()
            assert path.is_relative_to(ROOT.resolve()) and path.is_file(), relative
            assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, 'Файл изменён: ' + relative
        print('Обучение:', manifest['train_rows'], 'строк;', manifest['train_unique_groups'], 'групп')
        print('Проверка:', manifest['validation_rows'], 'строк; шаблон:', manifest['validation_template'])
        subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv'], check=True)
    """,
    )
    add(
        "markdown",
        """
        ## 2. Отдельное окружение обучения
        Установка занимает время и скачивает библиотеки. Модель будет скачана автоматически
        в локальный диск Colab. Веса адаптера и отчёты сохраняются на Drive.
    """,
    )
    add(
        "code",
        """
        TRAIN_ENV = Path('/content/lct-train-venv')
        if not (TRAIN_ENV / 'bin/python').exists():
            subprocess.run([sys.executable, '-m', 'venv', str(TRAIN_ENV)], check=True)
        TRAIN_PY = str(TRAIN_ENV / 'bin/python')
        subprocess.run([TRAIN_PY, '-m', 'pip', 'install', '-r', str(ROOT / 'scripts/requirements-a100.txt')], check=True)
        os.environ['HF_HOME'] = '/content/lct-model-cache'
        os.environ['TOKENIZERS_PARALLELISM'] = 'false'
        MODEL = 'Qwen/Qwen2.5-7B-Instruct'
        revision_code = 'from huggingface_hub import model_info; print(model_info(' + repr(MODEL) + ').sha)'
        REVISION = subprocess.check_output([TRAIN_PY, '-c', revision_code], text=True).strip()
        print('Версия базовой модели:', REVISION)
    """,
    )
    add(
        "markdown",
        """
        ## 3. Обучить и сравнить
        Запускайте эту ячейку только для нового обучения. Каждое выполнение создаёт
        отдельный каталог запуска, существующие веса не перезаписываются.
        Loss считается только по ответу assistant. Длинные примеры вызывают ошибку,
        а не обрезаются молча. Длительность на вашей A100 ещё не измерена.

        После каждой эпохи на Drive копируется адаптер. Это резервная копия весов,
        не полное состояние оптимизатора для точного продолжения обучения.
    """,
    )
    add(
        "code",
        """
        EPOCHS = 2
        LOCAL_SCRIPTS = Path('/content/lct-scripts')
        shutil.copytree(ROOT / 'scripts', LOCAL_SCRIPTS, dirs_exist_ok=True)
        subprocess.run([TRAIN_PY, str(LOCAL_SCRIPTS / 'train_layout.py'),
            '--drive-root', str(ROOT), '--revision', REVISION,
            '--epochs', str(EPOCHS), '--max-length', '3072'], check=True)
    """,
    )
    add(
        "markdown",
        """
        ## 4. Где лежат веса и результаты
        `adapter_model.safetensors` — обученные дополнительные веса. Их нужно загружать
        вместе с той же базовой моделью; это не самостоятельная модель.
        При повторном подключении Colab выполните раздел 1 и начните с этой ячейки,
        если обучать ещё раз не требуется.
    """,
    )
    add(
        "code",
        """
        latest = json.loads((ROOT / 'weights/latest.json').read_text())
        ADAPTER_DRIVE = (ROOT / latest['adapter_dir']).resolve()
        RUN_DIR = (ROOT / latest['run_dir']).resolve()
        assert ADAPTER_DRIVE.is_relative_to(ROOT.resolve()) and RUN_DIR.is_relative_to(ROOT.resolve())
        run = json.loads((RUN_DIR / 'run.json').read_text())
        assert run['status'] == 'completed', 'Последний запуск не завершён'
        metrics = json.loads((RUN_DIR / 'metrics.json').read_text())
        print('Адаптер:', ADAPTER_DRIVE)
        print('Архив для скачивания:', ADAPTER_DRIVE.with_suffix('.zip'))
        print('Базовая модель:', metrics['baseline']['weak_label_accuracy'])
        print('После обучения:', metrics['adapted']['weak_label_accuracy'])
        print('Эвристический алгоритм:', metrics['heuristic_weak_label_accuracy'])
        print('Корректный JSON:', metrics['adapted']['valid_json_rate'])
        print('Рекомендован визуальный пробный прогон:', metrics['recommended_for_visual_trial'])
        print('Это точность по слабой разметке, не оценка качества дизайна.')
    """,
    )
    add(
        "markdown",
        """
        ## 5. Загрузить сохранённые веса и поднять API
        Обучение выполнялось в отдельном процессе, поэтому GPU освобождается после
        его завершения. Для сервера создаётся отдельный venv без конфликтов версий.
        API предоставляет базовую модель и адаптер `lct-layout`.
        Придумайте ключ длиной от 24 символов; используйте его также в приложении.
    """,
    )
    add(
        "code",
        """
        import getpass
        if 'server' in globals() and server.poll() is None:
            raise RuntimeError('Сначала выполните ячейку остановки сервера')
        SERVE_ENV = Path('/content/lct-serve-venv')
        if not (SERVE_ENV / 'bin/python').exists():
            subprocess.run([sys.executable, '-m', 'venv', str(SERVE_ENV)], check=True)
        SERVE_PY = str(SERVE_ENV / 'bin/python')
        subprocess.run([SERVE_PY, '-m', 'pip', 'install', 'vllm'], check=True)
        (RUN_DIR / 'serving-requirements.freeze.txt').write_text(
            subprocess.check_output([SERVE_PY, '-m', 'pip', 'freeze'], text=True))
        ADAPTER_LOCAL = Path('/content/lct-adapters') / run['run_id']
        shutil.copytree(ADAPTER_DRIVE, ADAPTER_LOCAL, dirs_exist_ok=True)
        assert (ADAPTER_LOCAL / 'adapter_model.safetensors').is_file()
        API_KEY = getpass.getpass('Ключ API: ')
        assert len(API_KEY) >= 24, 'Минимум 24 символа'
        serve_env = dict(os.environ, VLLM_API_KEY=API_KEY, HF_HOME='/content/lct-model-cache')
        server_log = open('/content/lct-vllm.log', 'w')
        server = subprocess.Popen([str(SERVE_ENV / 'bin/vllm'), 'serve', run['base_model'],
            '--revision', run['base_revision'], '--host', '127.0.0.1', '--port', '8001',
            '--dtype', 'bfloat16', '--max-model-len', '32768', '--max-num-seqs', '1',
            '--gpu-memory-utilization', '0.85', '--enable-lora', '--max-lora-rank', '16',
            '--lora-modules', 'lct-layout=' + str(ADAPTER_LOCAL)],
            env=serve_env, stdout=server_log, stderr=subprocess.STDOUT)
        print('Сервер загружает базовую модель и сохранённый адаптер. PID:', server.pid)
    """,
    )
    add(
        "code",
        """
        import urllib.request, urllib.error
        LOCAL = 'http://127.0.0.1:8001/v1'
        headers = {'Authorization': 'Bearer ' + API_KEY, 'Content-Type': 'application/json'}
        for attempt in range(180):
            if server.poll() is not None:
                raise RuntimeError('Сервер остановился. Лог: /content/lct-vllm.log')
            try:
                with urllib.request.urlopen(urllib.request.Request(LOCAL + '/models', headers=headers), timeout=5) as response:
                    available = {m['id'] for m in json.load(response)['data']}
                assert 'lct-layout' in available and run['base_model'] in available
                print('Доступны:', sorted(available))
                break
            except (urllib.error.URLError, TimeoutError):
                if attempt % 6 == 0: print('Ожидаем сервер…')
                time.sleep(5)
        else:
            raise TimeoutError('Сервер не запустился; проверьте лог')
        sample = json.loads((ROOT / 'data/validation.jsonl').read_text().splitlines()[0])
        payload = {'model': 'lct-layout', 'messages': sample['messages'][:-1], 'temperature': 0,
                   'max_tokens': 48, 'response_format': {'type': 'json_object'}}
        request = urllib.request.Request(LOCAL + '/chat/completions',
            data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=120) as response:
            answer = json.load(response)['choices'][0]['message']['content']
        choice = json.loads(answer)['choice']
        assert choice in {c['label'] for c in json.loads(sample['messages'][1]['content'])['candidates']}
        print('Сохранённые веса загружены; пробный ответ:', answer)
    """,
    )
    add(
        "markdown",
        """
        ## 6. Адрес для приложения на компьютере
        Временный туннель нужен для интерактивного тестирования с локальным приложением.
        Colab должен оставаться запущенным. Адрес меняется при новом туннеле.
        Ячейка проверяет запрос с ключом и отказ без ключа.
    """,
    )
    add(
        "code",
        r"""
        import re
        if 'tunnel' in globals() and tunnel.poll() is None:
            raise RuntimeError('Туннель уже запущен; сначала остановите его')
        binary = Path('/content/cloudflared')
        if not binary.exists():
            urllib.request.urlretrieve('https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64', binary)
            binary.chmod(0o755)
        tunnel_log = open('/content/lct-tunnel.log', 'w')
        tunnel = subprocess.Popen([str(binary), 'tunnel', '--url', 'http://127.0.0.1:8001', '--no-autoupdate'],
            stdout=tunnel_log, stderr=subprocess.STDOUT)
        try:
            for attempt in range(60):
                if tunnel.poll() is not None: raise RuntimeError('Туннель остановился')
                match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', Path('/content/lct-tunnel.log').read_text())
                if match:
                    PUBLIC = match.group()
                    try:
                        with urllib.request.urlopen(urllib.request.Request(PUBLIC + '/v1/models', headers=headers), timeout=10) as response:
                            assert 'lct-layout' in {m['id'] for m in json.load(response)['data']}
                        try:
                            with urllib.request.urlopen(PUBLIC + '/v1/models', timeout=10):
                                raise RuntimeError('API доступен без ключа')
                        except urllib.error.HTTPError as error:
                            if error.code not in (401, 403): raise
                        break
                    except (urllib.error.URLError, TimeoutError):
                        pass
                time.sleep(2)
            else:
                raise TimeoutError('Туннель не готов; проверьте /content/lct-tunnel.log')
        except Exception:
            tunnel.terminate()
            raise
        print('INFERENCE_BASE_URL =', PUBLIC + '/v1')
        print('INFERENCE_MODEL =', run['base_model'])
        print('INFERENCE_LAYOUT_MODEL = lct-layout')
        print('INFERENCE_VISION = 0')
    """,
    )
    add(
        "markdown",
        r"""
        ## 7. Подключить приложение
        В PowerShell на компьютере остановите прежний сервер и задайте переменные.
        Скопируйте адрес из предыдущей ячейки и свой ключ. Установка GitHub в Colab не нужна.

        ```powershell
        Set-Location 'C:\Users\Serg\Desktop\лцт\lct_vk'
        $env:INFERENCE_BASE_URL='https://АДРЕС-ТУННЕЛЯ/v1'
        $env:INFERENCE_MODEL='Qwen/Qwen2.5-7B-Instruct'
        $env:INFERENCE_API_KEY='ВАШ-КЛЮЧ'
        $env:INFERENCE_VISION='0'
        $env:INFERENCE_MAX_RETRIES='1'
        # Включайте адаптер после просмотра metrics.json и визуального сравнения.
        $env:INFERENCE_LAYOUT_MODEL='lct-layout'
        .\.venv\Scripts\python.exe -m slide_agent --workspace slide-workspace/dataset serve
        ```

        Откройте http://127.0.0.1:8000 и снимите «Без модели». В `deck_plan.final.json`
        у слайда появится `pattern_selection.selector.mode=adapter` или причина отката.
        Для сравнения с прежним выбором макетов удалите `INFERENCE_LAYOUT_MODEL` и
        перезапустите приложение. Базовая модель и анализатор при этом остаются теми же.

        Если результат обучения хуже, сохраните веса как эксперимент и используйте
        прежний подбор макетов. Не загружайте адаптер выбора макетов как модель планирования текста.
    """,
    )
    add(
        "markdown",
        """
        ## Остановка — выполнять после работы
        Эта ячейка закрывает сервер и туннель. Чтобы закончить GPU-сессию, также отключите
        среду Colab. Сохранённые на Drive веса останутся.
    """,
    )
    add(
        "code",
        """
        for name in ('tunnel', 'server'):
            process = globals().get(name)
            if process is not None and process.poll() is None:
                process.terminate()
                try: process.wait(timeout=20)
                except subprocess.TimeoutExpired: process.kill()
        print('Сервер и туннель остановлены. Веса сохранены на Drive.')
    """,
    )
    return {
        "cells": cells,
        "metadata": {
            "accelerator": "GPU",
            "colab": {"name": "BrandDeck_A100_Training_Drive.ipynb"},
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


if __name__ == "__main__":
    target = (
        Path(__file__).resolve().parents[1]
        / "examples/BrandDeck_A100_Training_Drive.ipynb"
    )
    target.write_text(
        json.dumps(notebook(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(target)
