#!/usr/bin/env bash
# ============================================================
#  ROUT — uninstaller
#  Menghapus blok hook dari ~/.zshrc dan (opsional) folder rout
#  Pakai:  bash uninstall.sh [opsi]
# ============================================================
set -eu

DEST="$HOME/.termux/rout"
KEEP=0
YES=0

usage() {
  cat <<'USAGE'
ROUT uninstaller

Pakai:
  bash uninstall.sh [opsi]

Opsi:
  --dest DIR     folder terpasang (default: ~/.termux/rout)
  --keep-files   hapus hook saja, jangan hapus folder
  --yes          jangan minta konfirmasi
  -h, --help     tampilkan bantuan ini
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dest)       DEST="$2"; shift 2 ;;
    --keep-files) KEEP=1; shift ;;
    --yes)        YES=1; shift ;;
    -h|--help)    usage; exit 0 ;;
    *) echo "Argumen tidak dikenal: $1" >&2; usage; exit 1 ;;
  esac
done

ZSHRC="${ZDOTDIR:-$HOME}/.zshrc"

if [ "$YES" != "1" ]; then
  echo "Yang akan dihapus:"
  echo "  - blok hook ROUT di $ZSHRC"
  if [ "$KEEP" != "1" ]; then
    echo "  - folder $DEST"
  fi
  printf "Lanjut? [y/N] "
  read -r ans || ans=""
  case "$ans" in
    y|Y|ya|YA|yes|YES) ;;
    *) echo "Dibatalkan."; exit 0 ;;
  esac
fi

# --- hapus hook dari .zshrc ---
if [ -f "$ZSHRC" ]; then
  cp "$ZSHRC" "$ZSHRC.bak-$(date +%Y%m%d-%H%M%S)"
  python3 - "$ZSHRC" <<'PY'
import re, sys

path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    lines = f.read().splitlines(True)

start_re = re.compile(r"^#\s*=+\s*ROUT", re.I)
end_re = re.compile(r"^#\s*(=+\s*$|.*end ROUT.*$)", re.I)

out, inside, found = [], False, False
for ln in lines:
    s = ln.strip()
    if not inside and start_re.match(s):
        inside = True
        found = True
        continue
    if inside:
        if end_re.match(s):
            inside = False
        continue
    out.append(ln)

with open(path, "w", encoding="utf-8") as f:
    f.write("".join(out))
if found:
    print("  [ok] hook dihapus dari %s" % path)
else:
    print("  [--] tidak ada blok hook di %s" % path)
PY
else
  echo "  [--] $ZSHRC tidak ada, dilewati"
fi

# --- hapus penyesuaian .zshrc lain (instant prompt) ---
if [ -f "$ZSHRC" ]; then
  python3 - "$ZSHRC" <<'PY'
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    lines = f.read().splitlines(True)

out, removed = [], False
for ln in lines:
    s = ln.strip()
    if s.startswith("# ROUT: matikan instant prompt"):
        removed = True
        continue
    if s == "POWERLEVEL9K_INSTANT_PROMPT=off":
        removed = True
        continue
    out.append(ln)

with open(path, "w", encoding="utf-8") as f:
    f.write("".join(out))
if removed:
    print("  [ok] POWERLEVEL9K_INSTANT_PROMPT=off dihapus dari %s" % path)
else:
    print("  [--] tidak ada penyesuaian instant prompt di %s" % path)
PY
fi

# --- hapus ~/.hushlogin (kalau kosong, buatan installer) ---
if [ -f "$HOME/.hushlogin" ] && [ ! -s "$HOME/.hushlogin" ]; then
  rm -f "$HOME/.hushlogin"
  echo "  [ok] ~/.hushlogin dihapus"
fi

# --- hapus folder (backup ikut terhapus karena ada di dalamnya) ---
if [ "$KEEP" != "1" ] && [ -d "$DEST" ]; then
  if [ -d "$DEST/backup" ]; then
    n=$(ls -1 "$DEST/backup" 2>/dev/null | wc -l | tr -d ' ')
    echo "  [!!] $n backup config (berisi hash PIN) ikut dihapus"
  fi
  rm -rf "$DEST"
  echo "  [ok] folder $DEST dihapus"
fi

echo
echo "Selesai. Buka sesi Termux baru untuk menerapkan."
