#!/usr/bin/env bash
# ============================================================
#  ROUT — installer layar kunci Termux (PIN)
#  Pakai:  bash install.sh [opsi]
#
#  Skrip ini "pintu masuk". Installer UI-nya ditulis dengan Python,
#  jadi kalau python belum ada,dependensi (python & zsh) dipasang
#  lebih dulu di sini — TANYA SATU KALI, untuk semua yang kurang.
#  Setelah itu dikerjakan install-ui.py.
# ============================================================
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"

have() { command -v "$1" >/dev/null 2>&1; }
have_python() { have python3 || have python; }

manual() {
  printf '\n'
  printf '  Pasang manual dulu:\n\n'
  printf '     pkg install %s\n\n' "$1"
  printf '  Setelah itu jalankan lagi:  bash install.sh\n\n'
  printf '  Tekan Enter untuk keluar.'
  read -r _ || true
  exit 1
}

# --- apa saja yang belum ada? -------------------------------------------------
MISSING=""
have_python || MISSING="$MISSING python"
have zsh      || MISSING="$MISSING zsh"

if [ -n "$MISSING" ]; then
  # set -u: pola ${MISSING# } menghasilkan string tanpa spasi pertama.
  PKGS="${MISSING# }"
  printf '\033[2J\033[H'
  printf '\n\n\n'
  printf '                     R O U T\n'
  printf '                 I N S T A L L E R\n\n\n'
  printf '      !  Belum terpasang: %s\n\n' "$PKGS"

  if ! have pkg; then
    printf '      pkg tidak ditemukan — pastikan ini dijalankan di Termux.\n\n'
    manual "$PKGS"
  fi
  if [ ! -t 0 ] || [ ! -t 1 ]; then
    manual "$PKGS"
  fi

  printf '      Pasang otomatis sekarang? [Y/n]: '
  read -rsn1 ans || ans=""
  printf '\n'
  case "$ans" in
    n|N|no|NO|tidak|TIDAK)
      manual "$PKGS"
      ;;
    *)
      printf '\n      Memasang %s ...\n\n' "$PKGS"
      if ! pkg install -y $PKGS; then
        printf '\n      Gagal memasang. Periksa koneksi lalu coba lagi.\n\n'
        printf '  Tekan Enter untuk keluar.'
        read -r _ || true
        exit 1
      fi
      ;;
  esac

  # Kalau ternyata masih ada yang kurang, install-ui.py akan menanyakan
  # LAGI — dan itu melanggar janji "tanya sekali". Tandai saja supaya
  # install-ui.py langsung memberi tahu, tanpa bertanya.
  STILL=""
  have_python || STILL="$STILL python"
  have zsh      || STILL="$STILL zsh"
  if [ -n "$STILL" ]; then
    printf '\n      Masih kurang: %s\n' "${STILL# }"
  fi
  export ROUT_DEPS_ASKED=1
fi

have_python || {
  echo "ERROR: python belum terpasang." >&2
  echo "Jalankan dulu: pkg install python" >&2
  exit 1
}

PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "ERROR: python belum terpasang." >&2
  echo "Jalankan dulu: pkg install python" >&2
  exit 1
fi

export ROUT_DEPS_ASKED="${ROUT_DEPS_ASKED:-0}"
exec "$PY" "$DIR/install-ui.py" "$@"