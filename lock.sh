#!/data/data/com.termux/files/usr/bin/bash
# Termux Lock Screen — launcher
# Memanggil lock.py dengan python3 (Termux).

DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "Termux Lock: python3 tidak ditemukan. Jalankan: pkg install python" >&2
  exit 1
fi

exec "$PY" "$DIR/lock.py" "$@"
