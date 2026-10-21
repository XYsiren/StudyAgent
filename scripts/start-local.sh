#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
project_dir="$PWD"
export PATH="$project_dir/data/tools/node-v22.14.0-linux-x64/bin:$PATH"
export NO_PROXY="127.0.0.1,localhost,${NO_PROXY:-}"
export no_proxy="$NO_PROXY"
log_dir="$project_dir/data/local/logs"
mkdir -p "$log_dir"

if ! curl --noproxy '*' -fsS http://127.0.0.1:11435/api/tags >/dev/null 2>&1; then
  CUDA_VISIBLE_DEVICES=1 OLLAMA_VULKAN=false OLLAMA_HOST=127.0.0.1:11435 \
    nohup "$project_dir/data/ollama-runtime/bin/ollama" serve >"$log_dir/ollama.log" 2>&1 </dev/null &
  echo $! >"$log_dir/ollama.pid"
fi

if ! curl --noproxy '*' -fsS http://127.0.0.1:8010/api/health >/dev/null 2>&1; then
  nohup "$project_dir/.venv/bin/python" -m uvicorn app.main:app --app-dir backend \
    --host 127.0.0.1 --port 8010 >"$log_dir/backend.log" 2>&1 </dev/null &
  echo $! >"$log_dir/backend.pid"
fi

if ! curl --noproxy '*' -fsS http://127.0.0.1:5173/ >/dev/null 2>&1; then
  nohup node "$project_dir/frontend/node_modules/vite/bin/vite.js" "$project_dir/frontend" \
    --host 127.0.0.1 --port 5173 --strictPort >"$log_dir/frontend.log" 2>&1 </dev/null &
  echo $! >"$log_dir/frontend.pid"
fi

for attempt in {1..30}; do
  if curl --noproxy '*' -fsS http://127.0.0.1:8010/api/ready >/dev/null 2>&1 && \
     curl --noproxy '*' -fsS http://127.0.0.1:5173/ >/dev/null 2>&1; then
    echo "Frontend: http://127.0.0.1:5173"
    echo "API docs: http://127.0.0.1:8010/docs"
    echo "Logs: $log_dir"
    if [[ "${1:-}" == "--foreground" ]]; then
      wait
    fi
    exit 0
  fi
  sleep 1
done
echo "Startup incomplete. Check logs in $log_dir" >&2
exit 1
