"""Measure an actual llama.cpp CPU runtime and evaluate exported layout weights."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


def post_json(url: str, payload: dict, timeout: int = 240) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def evaluate(url: str, rows: list[dict], scale: float | None) -> dict:
    predictions = []
    started = time.perf_counter()
    for row in rows:
        payload = {
            "model": "lct-cpu",
            "messages": row["messages"][:-1],
            "max_tokens": 48,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        if scale is not None:
            payload["lora"] = [{"id": 0, "scale": scale}]
        response = post_json(url + "/v1/chat/completions", payload)
        raw = response["choices"][0]["message"]["content"]
        expected = json.loads(row["messages"][-1]["content"])["choice"]
        allowed = {
            c["label"] for c in json.loads(row["messages"][1]["content"])["candidates"]
        }
        try:
            answer = json.loads(raw)
            choice = answer.get("choice") if isinstance(answer, dict) else None
            valid = isinstance(choice, str) and choice in allowed
        except ValueError:
            choice, valid = None, False
        predictions.append(
            {
                "expected": expected,
                "choice": choice,
                "valid": valid,
                "correct": valid and choice == expected,
                "raw": raw,
                "usage": response.get("usage"),
                "timings": response.get("timings"),
            }
        )
    return {
        "examples": len(rows),
        "valid_json_rate": sum(p["valid"] for p in predictions) / len(rows),
        "weak_label_accuracy": sum(p["correct"] for p in predictions) / len(rows),
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "predictions": predictions,
    }


def check(
    server: Path,
    base: Path,
    adapter: Path | None,
    validation: Path,
    planner_probe: Path,
    output: Path,
    samples: int = 0,
) -> dict:
    import psutil

    rows = [
        json.loads(line)
        for line in validation.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if samples:
        rows = rows[:samples]
    if not rows:
        raise ValueError("CPU evaluation needs validation examples")
    output.parent.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    command = [
        str(server.resolve()),
        "--model",
        str(base.resolve()),
        "--alias",
        "lct-cpu",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--n-gpu-layers",
        "0",
        "--threads",
        "4",
        "--threads-batch",
        "4",
        "--ctx-size",
        "4096",
        "--parallel",
        "1",
        "--batch-size",
        "256",
        "--ubatch-size",
        "64",
        "--no-context-shift",
        "--cache-ram",
        "0",
    ]
    if adapter is not None:
        command += ["--lora", str(adapter.resolve()), "--lora-init-without-apply"]
    stopped = threading.Event()
    peak = [0]
    with output.with_suffix(".server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        def monitor():
            while not stopped.wait(0.2):
                try:
                    peak[0] = max(
                        peak[0], psutil.Process(process.pid).memory_info().rss
                    )
                except psutil.NoSuchProcess:
                    return

        worker = threading.Thread(target=monitor, daemon=True)
        worker.start()
        url = f"http://127.0.0.1:{port}"
        try:
            for _ in range(240):
                if process.poll() is not None:
                    raise RuntimeError(
                        f"CPU runtime exited; see {output.with_suffix('.server.log')}"
                    )
                try:
                    with urllib.request.urlopen(url + "/health", timeout=2) as response:
                        if response.status == 200:
                            break
                except (OSError, urllib.error.URLError):
                    time.sleep(1)
            else:
                raise TimeoutError("CPU runtime did not load within four minutes")
            if adapter is not None:
                with urllib.request.urlopen(url + "/lora-adapters") as response:
                    loaded = json.load(response)
                if not loaded or loaded[0]["id"] != 0 or loaded[0]["scale"] != 0:
                    raise ValueError(
                        "The layout adapter must be loaded and disabled by default"
                    )
            print("CPU: evaluating the quantized base", flush=True)
            baseline = evaluate(url, rows, 0.0 if adapter else None)
            adapted = evaluate(url, rows, 1.0) if adapter else None
            probe = json.loads(planner_probe.read_text(encoding="utf-8"))
            request = dict(probe["request"])
            if adapter:
                request["lora"] = [{"id": 0, "scale": 0.0}]
            begin = time.perf_counter()
            reply = post_json(url + "/v1/chat/completions", request)
            raw = reply["choices"][0]["message"]["content"]
            try:
                plan = json.loads(raw)
                slides = plan.get("slides", []) if isinstance(plan, dict) else []
                planner_valid = len(slides) == probe["expected_slides"] and all(
                    isinstance(s, dict) for s in slides
                )
            except ValueError:
                planner_valid = False
            heuristic = sum(
                r["heuristic_choice"]
                == json.loads(r["messages"][-1]["content"])["choice"]
                for r in rows
            ) / len(rows)
            report = {
                "runtime": "llama.cpp CPU, 4 threads, 4096 context, 1 slot",
                "baseline": baseline,
                "adapted": adapted,
                "heuristic_weak_label_accuracy": heuristic,
                "planner_probe": {
                    "valid_structure": planner_valid,
                    "raw": raw,
                    "elapsed_seconds": round(time.perf_counter() - begin, 2),
                },
                "peak_model_rss_bytes": peak[0],
                "within_model_memory_budget": peak[0] < 2304 * 1024**2,
                "enable_layout_adapter": bool(
                    adapted
                    and adapted["valid_json_rate"] == 1.0
                    and adapted["weak_label_accuracy"]
                    > max(baseline["weak_label_accuracy"], heuristic)
                ),
                "note": "Small weak-label holdout and a structural planner probe; visual quality and VPS end-to-end latency still require review.",
            }
            output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return report
        finally:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            stopped.set()
            worker.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("server", "base", "validation", "planner-probe", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--samples", type=int, default=0)
    args = parser.parse_args()
    report = check(
        args.server,
        args.base,
        args.adapter,
        args.validation,
        args.planner_probe,
        args.output,
        args.samples,
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k not in {"baseline", "adapted", "planner_probe"}
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
