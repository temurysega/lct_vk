# Serve Qwen3.5-9B GGUF through llama.cpp b11053 on Windows.
# The same weights run on a GPU (CUDA build) or on a CPU-only machine.
#   powershell -File deploy/llama/start-llama.ps1            # GPU
#   powershell -File deploy/llama/start-llama.ps1 -Device cpu -Threads 8
#   powershell -File deploy/llama/start-llama.ps1 -Vision  # + image projector
# -Vision also loads models/gpu/mmproj-F16.gguf for the visual slide audit.
# On an 8 GB card the projector fills the GPU memory (7.8 GB) and slows text
# generation two to three times, so deck generation runs on the text-only
# server and the audit on a -Vision one. -ProjectorOnCpu keeps the projector
# in RAM: generation stays fast, one slide image takes about 33 s.
param(
    [ValidateSet("gpu", "cpu")] [string] $Device = "gpu",
    [int] $Threads = 0,
    [int] $Port = 8080,
    [string] $Model = "models/gpu/Qwen3.5-9B-Q4_K_M.gguf",
    [switch] $Vision,
    [switch] $ProjectorOnCpu
)
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "../..")
$config = Get-Content (Join-Path $PSScriptRoot "qwen3.5-9b.json") -Raw | ConvertFrom-Json
$modelPath = Join-Path $root $Model
if (-not (Test-Path $modelPath)) {
    throw "Model not found: $modelPath. Run: python deploy/llama/setup_local.py"
}
$bin = if ($Device -eq "gpu") { "external-fixtures/llama-cuda/bin" } else { "external-fixtures/llama-cpu/bin" }
$server = Join-Path $root "$bin/llama-server.exe"
if (-not (Test-Path $server)) {
    throw "llama-server not found: $server. Run: python deploy/llama/setup_local.py --device $Device"
}
$arguments = @(
    "--model", $modelPath,
    "--alias", $config.served_alias,
    "--host", "127.0.0.1",
    "--port", $Port,
    "--ctx-size", $config.context_tokens,
    "--parallel", $config.parallel_slots,
    "--jinja",
    "--no-ui"
)
if ($Device -eq "gpu") {
    $arguments += @("--n-gpu-layers", "99")
} else {
    $arguments += @("--n-gpu-layers", "0")
}
$projector = Join-Path $root $config.mmproj_path
if ($Vision) {
    if (-not (Test-Path $projector)) {
        throw "Image projector not found: $projector. Run: python deploy/llama/setup_local.py"
    }
    $arguments += @("--mmproj", $projector)
    if ($ProjectorOnCpu -or $Device -eq "cpu") {
        $arguments += @("--no-mmproj-offload")
    }
}
if ($Threads -gt 0) {
    $arguments += @("--threads", $Threads, "--threads-batch", $Threads)
}
& $server @arguments
