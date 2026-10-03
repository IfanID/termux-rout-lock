#!/usr/bin/env bash
# ============================================================
#  ROUT — installer layar kunci Termux (PIN)
#  Pakai:  bash install.sh [opsi]
#
#  Skrip ini "pintu masuk". Kalau python3 belum ada, ia menawarkan
#  memasang dependency dulu (karena UI installer ditulis dengan
#  Python). Selanjutnya pekerjaan diserahkan ke install-ui.py.
# ============================================================
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"

# --- python belum ada: tanya & pasang (layar penuh sederhana) ---
if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
  if command -v pkg >/dev/null 2>&1 && [ -t 0 ] && [ -t 1 ]; then
    printf '\033[2J\033[H'
    printf '\n\n\n'
    printf '                     R O U T\n'
    printf '                 I N S T A L L E R\n\n\n'
    printf '      !  python3 (python) belum terpasang\n\n'
    printf '      Pasang otomatis sekarang? [Y/n]: '
    read -rsn1 ans || ans=""
    printf '\n'
    case "$ans" in
      n|N|no|NO|tidak|TIDAK)
        printf '\n      Dibatalkan. Pasang manual dulu:\n\n'
        printf '         pkg install python zsh\n\n'
        printf '      Tekan Enter untuk keluar.'
        read -r _ || true
        exit 1
        ;;
      *)
        printf '\n      Memasang python ...\n\n'
        if ! pkg install -y python; then
          printf '\n      Gagal memasang. Periksa koneksi lalu coba lagi.\n'
          printf '      Tekan Enter untuk keluar.'
          read -r _ || true
          exit 1
        fi
        ;;
    esac
  else
    printf 'ERROR: python belum terpasang.\n' >&2
    printf 'Jalankan dulu: pkg install python zsh\n' >&2
    exit 1
  fi
fi

PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  printf 'ERROR: python belum terpasang.\n' >&2
  printf 'Jalankan dulu: pkg install python\n' >&2
  exit 1
fi

exec "$PY" "$DIR/install-ui.py" "$@"
