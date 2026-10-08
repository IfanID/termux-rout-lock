#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
#  ROUT — simulasi animasi installer
#
#  Menjalankan UI installer yang SUNGGUHAN (install-ui.py, alternate
#  screen, spinner, keypad sama persis) tapi seluruh pekerjaannya
#  diarahkan ke folder sementara. ~/.zshrc dan config.conf asli TIDAK
#  akan disentuh.
# ============================================================

import importlib.util
import os
import sys
import tempfile
import time

# Bantuan `--help`. Harus string sungguhan, bukan komentar — komentar tidak
# pernah jadi docstring.
USAGE = """ROUT — simulasi animasi installer

Menjalankan UI installer yang SUNGGUHAN (spinner, keypad, alternate screen
sama persis) di dalam folder sementara. ~/.zshrc dan config.conf asli
tidak akan disentuh.

Pakai:
  bash preview.sh                # simulasi langkah instalasi
  bash preview.sh --slow 0.4     # lebih cepat
  bash preview.sh --pin          # sekaligus-atur PIN + kode pemulihan
  bash preview.sh --no-pin       # lewati langkah PIN & pemulihan
  bash preview.sh --demo-dep     # fase dependensi (hanya tanya sekali)
  bash preview.sh --demo-manual  # jawaban 'N' atau pkg tidak ada
  bash preview.sh --demo-asked   # dependensi sudah ditanyakan install.sh
"""

HERE = os.path.dirname(os.path.realpath(__file__))
SRC = os.path.join(HERE, "install-ui.py")

spec = importlib.util.spec_from_file_location("rout_install_ui", SRC)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def parse(argv):
    slow, dep, manual_demo, asked_demo = 1.2, False, False, False
    pin = None                      # None = pakai nilai bawaan
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--slow":
            i += 1
            slow = float(argv[i])
        elif a == "--pin":
            pin = True
        elif a == "--no-pin":
            pin = False
        elif a == "--demo-dep":
            dep = True
        elif a == "--demo-manual":
            manual_demo = True
        elif a == "--demo-asked":
            asked_demo = True
        elif a in ("-h", "--help"):
            print(USAGE)
            return None
        else:
            sys.stderr.write("argumen tidak dikenal: %s\n" % a)
            return None
        i += 1
    return slow, dep, manual_demo, asked_demo, pin


# --------------------------------------------------------------------------- #
# Fase dependensi
# --------------------------------------------------------------------------- #
PKG_OUTPUT = [
    "Fetching https://packages.termux.dev/",
    "  3.4 MiB/12.1 MiB",
    "Downloading python",
    "  done",
    "Downloading zsh",
    "  done",
    "Setting up python",
    "Setting up zsh",
]


def _ask(ui, body, seconds=None, auto=None):
    """Tampilkan layar. Kalau `auto` diisi, jawaban itu dipakai otomatis."""
    ui.show(body)
    if auto is not None:
        time.sleep(0.7)
        return auto
    return ui.key(None)


def demo_dep(ui, slow, auto=None, manual=False, asked=False):
    """Cek -> tanya SATU KALI -> pasang (atau batal).

    `manual=True`  : zsh belum ada tapi pkg juga tidak (di luar Termux)
    `asked=True`   : install.sh sudah menanyakan & gagal -> JANGAN tanya lagi
    """
    sp = m.Spinner(0.08)
    missing = ["python", "zsh"]

    # --- 1. cek requirement (spinner) ---
    t0 = time.time()
    while time.time() - t0 < max(0.8, slow * 0.6):
        ui.show(m.screen_checking(sp.frame()))
        time.sleep(0.08)

    # --- 2. sudah pernah ditanyakan? langsung jelaskan, jangan tanya ---
    if asked:
        ui.show(m.screen_dep_asked_again(["zsh"]))
        m.wait_key(ui)
        return

    if manual:
        ui.show(m.screen_manual(["python", "zsh"], notermux=True))
        m.wait_key(ui)
        return

    # --- 3. satu pertanyaan untuk semua yang kurang ---
    answer = _ask(ui, m.screen_ask(missing), auto=auto)
    text = (answer or "").lower()
    if not ("y" in text or "\r" in text or "\n" in text):
        ui.show(m.screen_manual(missing))
        m.wait_key(ui)
        return

    # --- 4. memasang, dengan spinner + keluaran pkg yang berganti ---
    last = ""
    step = 0
    t0 = time.time()
    dur = max(3.0, slow * 7)
    while time.time() - t0 < dur:
        ui.show(m.screen_installing(missing, last, sp.frame()))
        if step < len(PKG_OUTPUT):
            last = PKG_OUTPUT[step]
            step += 1
        elif step < len(PKG_OUTPUT) + 3:
            last = "..."
            step += 1
        else:
            last = "done"
        time.sleep(slow * 0.5)

    ui.show(m.screen_checking(sp.frame()))
    time.sleep(0.5)
    ui.show(m.screen_done("~/.termux/rout  (simulasi)", True, False))
    m.wait_key(ui)


def footer(text):
    """Cetak catatan penutup, dibungkus ke lebar terminal.

    Footer ini dicetak dengan print biasa (bukan lewat kursor absolut),
    jadi kalau panjangnya melebihi lebar terminal, terminal akan
    membungkusnya sendiri dan/tmp penampaknya berantakan.
    """
    try:
        # Pakai term_size() milik installer: ia menanyakan tty langsung dan
        # mengabaikan COLUMNS yang bisa tertinggal berisi ukuran jendela lama.
        width = max(30, m.term_size()[0] - 2)
    except Exception:
        width = 58
    for para in text.splitlines():
        if not para.strip():
            print("")
            continue
        rest = para.strip()
        while rest:
            if len(rest) <= width:
                print(rest)
                break
            # Cari spasi terakhir yang muat. Mulai dari indeks 1 supaya
            # spasi di depan tidak terpilih -- kalau terpilih, potongannya
            # cuma 1 karakter dan sisa teksnya tidak pernah berkurang,
            # sehingga loop ini berjalan selamanya.
            cut = rest.rfind(" ", 1, width)
            if cut <= 1:
                cut = width          # tidak ada spasi: potong keras
            print(rest[:cut])
            rest = rest[cut:].strip()


def main(argv):
    opts = parse(argv[1:])
    if opts is None:
        return 1
    slow, dep, manual_demo, asked_demo, pin = opts

    # --- lambatkan setiap langkah supaya animasinya sempat terlihat -----
    for name in [k for k in dir(m.Installer) if k.startswith("step_")]:
        orig = getattr(m.Installer, name)

        def wrap(fn, delay):
            def inner(self):
                time.sleep(delay)
                return fn(self)
            return inner

        setattr(m.Installer, name, wrap(orig, slow))

    # --- perlambat tiap berkas supaya animasi per-file terlihat -------
    orig_file = m.Installer._file

    def _file(self, name, status):
        orig_file(self, name, status)
        if status == "running":
            time.sleep(slow)

    m.Installer._file = _file

    # --- arahkan ke folder sementara ----------------------------------
    work = tempfile.mkdtemp(prefix="rout-simulasi-")
    home = os.path.join(work, "home")
    os.makedirs(home)
    dest = os.path.join(work, "rout")
    zshrc = os.path.join(home, ".zshrc")
    with open(zshrc, "w", encoding="utf-8") as f:
        f.write("# .zshrc contoh (simulasi)\nalias ll='ls -l'\n")

    # Installer membaca ZDOTDIR dari environment.
    os.environ["ZDOTDIR"] = home
    os.environ["HOME"] = home

    if dep or manual_demo or asked_demo:
        ui = m.Ui()
        if not ui.enter():
            print("Simulasi butuh terminal sungguhan (TTY).", file=sys.stderr)
            return 1
        try:
            if asked_demo:
                demo_dep(ui, slow, asked=True)
            elif manual_demo:
                demo_dep(ui, slow, manual=True)
            else:
                #Dijawab otomatis supaya alurnya tidak menggantung.
                demo_dep(ui, slow, auto="y")
        finally:
            ui.leave()
            print("")
            footer("[simulasi] selesai. Folder sementara: %s" % work)
            footer("[simulasi] ~/.zshrc & config.conf asli tidak disentuh.")
        return 0

    args = ["install-ui.py", "--dest", dest, "--zshrc", zshrc]
    if pin is False:
        args.append("--no-setup")
    elif pin is None:
        # Bawaan simulasi: lewati PIN supaya alur install cepat dilihat;
        # pakai --pin untuk melihat layar set PIN + kode pemulihan.
        args.append("--no-setup")
    rc = m.main(args)
    print("")
    footer("[simulasi] Folder sementara: %s" % work)
    footer("[simulasi] ~/.zshrc & config.conf asli tidak disentuh.")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))