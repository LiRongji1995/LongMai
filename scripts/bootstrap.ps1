$ErrorActionPreference = 'Stop'
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw 'Python launcher not found; install Python 3.12.' }
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e '.[dev]'
