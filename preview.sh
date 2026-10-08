#!/usr/bin/env bash
# ============================================================
#  ROUT — simulasi animasi installer
#
#  Menjalankan UI installer sungguhan di folder sementara.
#  Aman: ~/.zshrc dan ~/.termux/rout/config.conf asli tidak disentuh.
#
#  Pakai:
#    bash preview.sh              # simulasi langkah instalasi
#    bash preview.sh --slow 0.4   # lebih cepat
#    bash preview.sh --pin        # sekaligus-set PIN + kode pemulihan
#    bash preview.sh --no-pin     # lewati langkah PIN & pemulihan
#    bash preview.sh --demo-dep   # fase dependensi (tanya sekali)
#    bash preview.sh --demo-manual  # jawaban 'N' / pkg tidak ada
#    bash preview.sh --demo-asked   # deps sudah ditanyakan install.sh
# ============================================================
set -eu
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "python belum ada. Jalankan: pkg install python" >&2
  exit 1
fi
exec "$PY" "$DIR/preview.py" "$@"