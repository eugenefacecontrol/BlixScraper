#!/bin/zsh
# Local, private ChatGPT tunnel. Runtime credentials stay in ignored data/.
set -eu
setopt NO_XTRACE
cd "${0:A:h}/.."
task_root="$PWD"
task_client="$task_root/data/tunnel/bin/tunnel-client"
task_key="$task_root/data/tunnel/runtime-key"
task_id_file="$task_root/data/tunnel/tunnel-id"
if [[ ! -x "$task_client" || ! -x "$task_root/.venv/bin/blix" ]]; then
  print -u2 'Install the official tunnel-client locally in data/tunnel/bin and run uv sync first.'
  exit 1
fi
if [[ ! -s "$task_id_file" ]]; then
  read 'task_tunnel_id?Tunnel ID: '
  [[ "$task_tunnel_id" == tunnel_* ]] || { print -u2 'Invalid tunnel ID'; exit 1; }
  umask 077
  print -r -- "$task_tunnel_id" > "$task_id_file"
fi
task_tunnel_id="$(<"$task_id_file")"
if [[ ! -s "$task_key" ]]; then
  print 'Ключ транспорта сохранится только в data/tunnel/runtime-key (права 600, исключён из Git).'
  umask 077
  "$task_root/.venv/bin/python" - "$task_key" <<'PY'
import getpass, os, sys
key = getpass.getpass('Runtime API key (ввод скрыт): ').strip()
if not key or any(c.isspace() for c in key):
    raise SystemExit('Пустой ключ или пробелы: ничего не сохранено')
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as f:
    f.write(key)
PY
fi
chmod 600 "$task_key"
"$task_client" runtimes connect --alias blix-mac --profile blix-mac \
  --profile-dir "$task_root/data/tunnel/profiles" \
  --tunnel-id "$task_tunnel_id" \
  --mcp-command "$task_root/scripts/run-stdio.sh" \
  --runtime-api-key "file:$task_key"
"$task_client" runtimes status blix-mac --json
print 'Проверьте поля process_running, healthy и ready. После успешного запуска выберите этот Tunnel в ChatGPT.'
