#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
#  ROUT — installer layar penuh
#  Dipanggil oleh install.sh.
#
#  Menjalankan seluruh langkah pemasangan sambil menampilkan
#  UI penuh di terminal (alternate screen, seperti lockscreen).
#  Kalau dijalankan tanpa TTY (mis. di-pipe / otomasi), otomatis
#  memakai mode teks biasa agar tetap bisa dipakai.
# ============================================================

import os
import re
import sys
import time
import shutil
import select
import signal
import termios
import tty
import subprocess

VERSION = "1.0"
RESET = "\033[0m"

# --- tema (disamakan dengan lockscreen) ---
C_BG  = (16, 16, 26)
C_FG  = (226, 226, 238)
C_ACC = (255, 86, 120)
C_DIM = (130, 130, 150)
C_OK  = (80, 220, 140)
C_ERR = (255, 120, 120)

SPIN = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# (nama tampil, nama binary)
DEP_BINS = [("python", "python3"), ("zsh", "zsh")]
DEP_NAMES = [n for n, _b in DEP_BINS]


def which_dep(name):
    if name == "python":
        return shutil.which("python3") or shutil.which("python")
    return shutil.which(name)


def missing_deps():
    return [n for n in DEP_NAMES if not which_dep(n)]

# (kunci, label) — urutan langkah
STEPS = [
    ("cek",       "Cek lingkungan (python, zsh)"),
    ("salin",     "Salin program ke ~/.termux/rout"),
    ("config",    "Buat config.conf"),
    ("hook",      "Pasang hook ke ~/.zshrc"),
    ("instant",   "Matikan instant prompt p10k"),
    ("hushlogin", "Buat ~/.hushlogin"),
    ("pin",       "Set PIN"),
]

HOOK_MARK = "# ===== ROUT — Termux Lock Screen ====="
START_RE = re.compile(r"^#\s*=+\s*ROUT", re.I)
END_RE = re.compile(r"^#\s*(=+\s*$|.*end ROUT.*$)", re.I)


class InstallError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Warna / ukuran terminal
# --------------------------------------------------------------------------- #
def fg(rgb):
    return "\033[38;2;%d;%d;%dm" % rgb


def bg(rgb):
    return "\033[48;2;%d;%d;%dm" % rgb


def term_size():
    try:
        sz = shutil.get_terminal_size((80, 24))
        return sz.columns, sz.lines
    except Exception:
        return 80, 24


def tilde(path):
    home = os.path.expanduser("~")
    if home and path.startswith(home):
        return "~" + path[len(home):]
    return path


# --------------------------------------------------------------------------- #
# Kotak
# --------------------------------------------------------------------------- #
def box(content, width):
    """content: list (teks, warna) atau None. Kembalikan (baris, lebar)."""
    inner = max(1, width - 4)
    top = "╭" + "─" * (width - 2) + "╮"
    bot = "╰" + "─" * (width - 2) + "╯"
    rows = [fg(C_DIM) + top]
    for item in content:
        text, color = ("", C_FG) if item is None else item
        if len(text) > inner:
            text = text[:inner]
        pad = " " * (inner - len(text))
        rows.append(fg(C_DIM) + "│ " + fg(color) + text + pad +
                    fg(C_DIM) + " │")
    rows.append(fg(C_DIM) + bot)
    return rows, width


# --------------------------------------------------------------------------- #
# Layar (alternate screen + raw mode)
# --------------------------------------------------------------------------- #
class Ui:
    def __init__(self):
        self.saved = None
        self.active = False

    def enter(self):
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False
        self.saved = termios.tcgetattr(sys.stdin.fileno())
        tty.setraw(sys.stdin.fileno())
        os.write(sys.stdout.fileno(),
                 (bg(C_BG) + "\033[?1049h\033[?25l\033[2J").encode("utf-8"))
        self.active = True
        return True

    def leave(self):
        if not self.active:
            return
        try:
            os.write(sys.stdout.fileno(),
                     ("\033[?25h\033[?1049l" + RESET).encode("utf-8"))
        except OSError:
            pass
        if self.saved is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(),
                                  termios.TCSADRAIN, self.saved)
            except Exception:
                pass
        self.active = False

    def show(self, content):
        cols, _ = term_size()
        width = max(28, min(62, cols - 2))
        rows, w = box(content, width)
        self._draw(rows, w)

    def _draw(self, rows, width):
        cols, lines = term_size()
        left = max(0, (cols - width) // 2)
        top = max(1, (lines - len(rows)) // 2 + 1)
        pad = " " * left
        buf = [bg(C_BG), "\033[H"]
        for r in range(1, lines + 1):
            buf.append("\033[%d;1H" % r)
            buf.append("\033[K")
            idx = r - top
            if 0 <= idx < len(rows):
                buf.append(pad)
                buf.append(rows[idx])
        buf.append(RESET)
        try:
            os.write(sys.stdout.fileno(), "".join(buf).encode("utf-8"))
        except OSError:
            pass

    def key(self, timeout=None):
        try:
            ready, _, _ = select.select([sys.stdin.fileno()], [], [], timeout)
        except (OSError, ValueError):
            return None
        if not ready:
            return None
        try:
            data = os.read(sys.stdin.fileno(), 64)
        except OSError:
            return None
        return data.decode("utf-8", "replace")


# --------------------------------------------------------------------------- #
# Layar-layar
# --------------------------------------------------------------------------- #
def head_lines():
    return [
        ("", C_FG),
        ("R O U T", C_ACC),
        ("I N S T A L L E R", C_DIM),
        ("", C_FG),
    ]


def screen_ask(missing):
    body = head_lines()
    for name in missing:
        body.append(("  !  %-8s belum terpasang" % name, C_ERR))
    body += [
        ("", C_FG),
        ("  Pasang otomatis sekarang?", C_FG),
        ("", C_FG),
        ("     [ Y ]   Ya, pasang otomatis", C_OK),
        ("     [ N ]   Tidak, saya pasang manual", C_DIM),
        ("", C_FG),
        ("  Y = pasang sekarang  ·  N = batal & keluar", C_DIM),
        ("", C_FG),
    ]
    return body


def screen_installing(pkgs, last, frame):
    body = head_lines()
    body += [
        ("  %s  Memasang %s..." % (frame, " + ".join(pkgs)), C_ACC),
        ("", C_FG),
        ("  $ pkg install -y %s" % " ".join(pkgs), C_DIM),
        ("", C_FG),
        ("  %s" % (last or "menunggu..."), C_DIM),
        ("", C_FG),
    ]
    return body


def screen_dep_failed(missing):
    body = head_lines()
    body += [
        ("  !  Gagal memasang: %s" % ", ".join(missing), C_ERR),
        ("", C_FG),
        ("  Periksa koneksi internet lalu coba lagi.", C_DIM),
        ("", C_FG),
        ("     [ R ]  Coba lagi", C_OK),
        ("     [ Enter ]  Keluar", C_DIM),
        ("", C_FG),
    ]
    return body


def screen_manual(missing, notermux=False):
    body = head_lines()
    if notermux:
        body += [
            ("  !  pkg tidak ditemukan.", C_ERR),
            ("", C_FG),
            ("  Pastikan ini dijalankan di dalam Termux.", C_FG),
        ]
    else:
        body += [
            ("  Pasang manual dulu:", C_FG),
            ("", C_FG),
            ("     pkg install python zsh", C_ACC),
        ]
    body += [
        ("", C_FG),
        ("  Setelah itu jalankan lagi:  bash install.sh", C_DIM),
        ("", C_FG),
        ("  Tekan Enter untuk keluar.", C_DIM),
        ("", C_FG),
    ]
    return body


def screen_steps(statuses, note=""):
    body = head_lines()
    for key, label in STEPS:
        st = statuses.get(key, "pending")
        if st == "done":
            mark, color = "✓", C_OK
        elif st == "running":
            mark, color = "…", C_ACC
        elif st == "failed":
            mark, color = "!", C_ERR
        elif st == "skip":
            mark, color = "–", C_DIM
        else:
            mark, color = "○", C_DIM
        body.append(("  %s  %s" % (mark, label), color))
    body.append(("", C_FG))
    if note:
        body.append(("  %s" % note, C_ERR))
        body.append(("", C_FG))
    return body


def screen_done(dest, pin_set):
    lock = "aktif (PIN)" if pin_set else "DIMATIKAN — PIN belum diset"
    lcolor = C_OK if pin_set else C_ERR
    return [
        ("", C_FG),
        ("  ✓  Semua selesai", C_OK),
        ("", C_FG),
        ("  Terpasang di : %s" % tilde(dest), C_FG),
        ("  Lock         : %s" % lock, lcolor),
        ("", C_FG),
        ("  Langkah berikutnya:", C_FG),
        ("  1. Buka sesi Termux BARU", C_FG),
        ("  2. Layar kunci akan muncul", C_FG),
        ("", C_FG),
        ("  Perintah: lock-pin · lock-recovery · lock-status", C_DIM),
        ("", C_FG),
        ("        Tekan Enter untuk keluar", C_ACC),
        ("", C_FG),
    ]


# --------------------------------------------------------------------------- #
# Pemasangan dependency
# --------------------------------------------------------------------------- #
def install_deps(ui, missing):
    pkgs = list(missing)
    cmd = ["pkg", "install", "-y"] + pkgs
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL)
    except OSError:
        return False
    fd = proc.stdout.fileno()
    buf, last, i = "", "", 0
    while True:
        try:
            ready, _, _ = select.select([fd], [], [], 0.12)
        except (OSError, ValueError):
            break
        if ready:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                chunk = b""
            if not chunk:
                break
            buf += chunk.decode("utf-8", "replace")
            parts = re.split(r"[\r\n]", buf)
            buf = parts.pop()
            for line in parts:
                line = line.strip()
                if line:
                    last = line
        ui.show(screen_installing(pkgs, last, SPIN[i % len(SPIN)]))
        i += 1
    try:
        proc.wait()
    except Exception:
        pass
    return proc.returncode == 0


def read_pin_set(conf_path):
    try:
        with open(conf_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("pin_hash="):
                    return bool(line.split("=", 1)[1].strip())
    except OSError:
        pass
    return False


# --------------------------------------------------------------------------- #
# Installer
# --------------------------------------------------------------------------- #
class Installer:
    def __init__(self, src, dest, do_hook, do_setup):
        self.src = src
        self.dest = dest
        self.do_hook = do_hook
        self.do_setup = do_setup
        self.quiet = False
        self.log = []
        zdir = os.environ.get("ZDOTDIR") or os.path.expanduser("~")
        self.zshrc = os.path.join(zdir, ".zshrc")

    def _log(self, msg):
        self.log.append(msg)
        if not self.quiet:
            print(msg, flush=True)

    # --- langkah ---
    def step_cek(self):
        if not which_dep("python"):
            raise InstallError("python tidak ditemukan")
        if not which_dep("zsh"):
            raise InstallError("zsh tidak ditemukan")
        self._log("  [ok] lingkungan siap")

    def step_salin(self):
        os.makedirs(self.dest, exist_ok=True)
        same = os.path.realpath(self.src) == os.path.realpath(self.dest)
        if same:
            self._log("  [ok] sumber = tujuan (repo dipakai langsung), tidak menyalin file")
        else:
            for name in ("lock.py", "lock.sh", "uninstall.sh"):
                s = os.path.join(self.src, name)
                if os.path.exists(s):
                    shutil.copyfile(s, os.path.join(self.dest, name))
            self._log("  [ok] lock.py, lock.sh & uninstall.sh dipasang")
        for name in ("lock.py", "lock.sh", "uninstall.sh"):
            p = os.path.join(self.dest, name)
            if os.path.exists(p):
                try:
                    os.chmod(p, 0o700)
                except OSError:
                    pass

    def step_config(self):
        conf = os.path.join(self.dest, "config.conf")
        if os.path.exists(conf):
            self._log("  [ok] config.conf sudah ada (tidak ditimpa)")
            return
        template = os.path.join(self.src, "config.default.conf")
        if not os.path.exists(template):
            raise InstallError("config.default.conf tidak ditemukan")
        shutil.copyfile(template, conf)
        try:
            os.chmod(conf, 0o600)
        except OSError:
            pass
        self._log("  [ok] config.conf dibuat dari template")

    def step_hook(self):
        if os.path.exists(self.zshrc):
            backup = self.zshrc + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
            shutil.copyfile(self.zshrc, backup)
            with open(self.zshrc, encoding="utf-8") as f:
                if HOOK_MARK in f.read():
                    remove_hook(self.zshrc)
                    self._log("  [ok] hook lama diganti dengan versi terbaru")
        with open(self.zshrc, "a", encoding="utf-8") as f:
            f.write(hook_block(os.path.join(self.dest, "lock.sh")))
        self._log("  [ok] hook dipasang ke %s" % self.zshrc)

    def step_instant(self):
        if not os.path.exists(self.zshrc):
            self._log("  [--] %s tidak ada, instant prompt dilewati" % self.zshrc)
            return
        with open(self.zshrc, encoding="utf-8") as f:
            content = f.read()
        if re.search(r"^POWERLEVEL9K_INSTANT_PROMPT=off", content, re.M):
            self._log("  [--] instant prompt sudah off")
            return
        new = ("# ROUT: matikan instant prompt supaya layar kunci muncul sebelum prompt\n"
               "POWERLEVEL9K_INSTANT_PROMPT=off\n" + content)
        with open(self.zshrc, "w", encoding="utf-8") as f:
            f.write(new)
        self._log("  [ok] POWERLEVEL9K_INSTANT_PROMPT=off ditambahkan")

    def step_hushlogin(self):
        p = os.path.join(os.path.expanduser("~"), ".hushlogin")
        if os.path.exists(p):
            self._log("  [--] ~/.hushlogin sudah ada")
            return
        with open(p, "w", encoding="utf-8") as f:
            f.write("")
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
        self._log("  [ok] ~/.hushlogin dibuat (MOTD tampil setelah unlock)")


def remove_hook(path):
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines(True)
    out, inside, found = [], False, False
    for ln in lines:
        s = ln.strip()
        if not inside and START_RE.match(s):
            inside, found = True, True
            continue
        if inside:
            if END_RE.match(s):
                inside = False
            continue
        out.append(ln)
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(out))
    return found


HOOK_BLOCK = '''
# ===== ROUT — Termux Lock Screen =====
# PENTING: installer mematikan powerlevel10k instant prompt supaya stdin/stdout
# tidak dialihkan saat .zshrc; dengan begitu layar kunci bisa muncul SEBELUM
# prompt zsh (instan).
# Kunci saat: (1) sesi baru, (2) perintah foreground lama selesai (mis. keluar
# aplikasi TUI), (3) terminal diam di prompt TERMUX_LOCK_IDLE detik.
# Bypass sesi ini: TERMUX_NO_LOCK=1 zsh
TERMUX_LOCK_SH="__ROUT_LOCK_SH__"
if [[ -o interactive ]] \\
   && [[ -z "$TERMUX_NO_LOCK" ]] && [[ -z "$TERMUX_LOCK_ACTIVE" ]] \\
   && [[ -x "$TERMUX_LOCK_SH" ]]; then
  export TERMUX_LOCK_ACTIVE=1
  export TERMUX_LOCK_PARENT_PID=$PPID

  : ${TERMUX_LOCK_IDLE:=60}
  : ${TERMUX_LOCK_AFTER_CMD:=10}
  typeset -gi _termux_lock_last=$SECONDS
  typeset -gi _termux_lock_cmd_start=0
  typeset -gi _termux_lock_had_cmd=0
  _termux_lock_run() {
    "$TERMUX_LOCK_SH" run
    local rc=$?
    _termux_lock_last=$SECONDS
    (( rc == 3 )) && exit 0
    return 0
  }

  # (1) kunci SEBELUM prompt zsh muncul (instan)
  _termux_lock_run

  # Setelah berhasil membuka, tampilkan MOTD (welcome). MOTD bawaan dimatikan
  # oleh ~/.hushlogin supaya tidak muncul SEBELUM layar kunci.
  _termux_motd="${PREFIX:-/data/data/com.termux/files/usr}/etc/motd.sh"
  [ -r "$_termux_motd" ] && bash "$_termux_motd"

  # (2) kunci begitu perintah foreground yang berjalan lama selesai (mis. keluar aplikasi TUI)
  _termux_lock_preexec() {
    _termux_lock_cmd_start=$SECONDS
    _termux_lock_had_cmd=1
  }
  _termux_lock_precmd() {
    local dur=0
    if (( _termux_lock_had_cmd )); then
      dur=$(( SECONDS - _termux_lock_cmd_start ))
      _termux_lock_had_cmd=0
    fi
    _termux_lock_last=$SECONDS
    [[ -n "$TERMUX_NO_LOCK" ]] && return 0
    if (( dur >= TERMUX_LOCK_AFTER_CMD )); then
      _termux_lock_run
    fi
  }
  autoload -Uz add-zsh-hook
  add-zsh-hook preexec _termux_lock_preexec
  add-zsh-hook precmd  _termux_lock_precmd

  # (3) kunci otomatis saat diam di prompt
  if zmodload zsh/sched 2>/dev/null; then
    _termux_lock_check() {
      sched +5 _termux_lock_check
      [[ -n "$TERMUX_NO_LOCK" ]] && return 0
      if (( SECONDS - _termux_lock_last >= TERMUX_LOCK_IDLE )); then
        _termux_lock_run
      fi
    }
    _termux_lock_check
  fi
fi

alias lock='"$TERMUX_LOCK_SH" run'
alias lock-pin='"$TERMUX_LOCK_SH" set-pin'
alias lock-recovery='"$TERMUX_LOCK_SH" set-recovery'
alias lock-status='"$TERMUX_LOCK_SH" status'
# ===== end ROUT =====
'''


def hook_block(lock_sh):
    return HOOK_BLOCK.replace("__ROUT_LOCK_SH__", lock_sh)


# --------------------------------------------------------------------------- #
# Alur UI
# --------------------------------------------------------------------------- #
def wait_key(ui):
    while True:
        k = ui.key(None)
        if k is None:
            continue
        for ch in k:
            if ch in ("\r", "\n", "q", "\x1b", "\x03"):
                return
        # Enter lewat keypad
        if k.startswith("\x1b"):
            return


def show_manual(ui, missing, notermux=False):
    ui.show(screen_manual(missing, notermux))
    wait_key(ui)


def run_ui(inst):
    inst.quiet = True
    ui = Ui()
    if not ui.enter():
        return run_plain(inst)

    def on_sig(_signum, _frame):
        ui.leave()
        sys.exit(130)

    for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(s, on_sig)
        except Exception:
            pass

    try:
        missing = missing_deps()
        if missing:
            if shutil.which("pkg") is None:
                show_manual(ui, missing, notermux=True)
                return 1
            ui.show(screen_ask(missing))
            if not ask_yes(ui):
                show_manual(ui, missing)
                return 1
            ok = install_deps(ui, missing)
            while not ok:
                ui.show(screen_dep_failed(missing))
                if ask_retry(ui):
                    ok = install_deps(ui, missing)
                else:
                    return 1
            still = missing_deps()
            if still:
                ui.show(screen_dep_failed(still))
                wait_key(ui)
                return 1
        return run_steps(ui, inst)
    finally:
        ui.leave()


def ask_yes(ui):
    while True:
        k = ui.key(None)
        if k is None:
            continue
        c = k.lower()
        if "\x03" in c or "\x1b" in c:
            return False
        if "y" in c or "\r" in c or "\n" in c:
            return True
        if "n" in c or "q" in c:
            return False


def ask_retry(ui):
    while True:
        k = ui.key(None)
        if k is None:
            continue
        c = k.lower()
        if "r" in c:
            return True
        if "\r" in c or "\n" in c or "\x03" in c or "\x1b" in c or "q" in c:
            return False


def run_steps(ui, inst):
    statuses = {k: "pending" for k, _ in STEPS}

    def refresh(note=""):
        ui.show(screen_steps(statuses, note))

    refresh()
    for key, _label in STEPS:
        if key in ("hook", "instant") and not inst.do_hook:
            statuses[key] = "skip"
            refresh()
            continue
        if key == "pin" and not inst.do_setup:
            statuses[key] = "skip"
            refresh()
            continue
        statuses[key] = "running"
        refresh()
        if key == "pin":
            ui.leave()
            bash = shutil.which("bash") or "bash"
            try:
                rc = subprocess.call([bash, os.path.join(inst.dest, "lock.sh"),
                                      "set-pin"])
            except OSError:
                rc = 1
            ui.enter()
            statuses[key] = "done" if rc == 0 else "failed"
        else:
            try:
                getattr(inst, "step_" + key)()
                statuses[key] = "done"
            except InstallError as e:
                statuses[key] = "failed"
                refresh("Gagal: %s" % e)
                wait_key(ui)
                return 1
            except Exception as e:  # noqa: BLE001
                statuses[key] = "failed"
                refresh("Gagal: %s" % e)
                wait_key(ui)
                return 1
        refresh()

    pin_set = read_pin_set(os.path.join(inst.dest, "config.conf"))
    ui.show(screen_done(inst.dest, pin_set))
    wait_key(ui)
    return 0


# --------------------------------------------------------------------------- #
# Mode teks (tanpa TTY)
# --------------------------------------------------------------------------- #
def run_plain(inst):
    inst.quiet = False
    print("== ROUT installer ==")
    print("  sumber : %s" % inst.src)
    print("  tujuan : %s" % inst.dest)
    print()

    missing = missing_deps()
    if missing:
        sys.stderr.write("ERROR: belum terpasang: %s\n" % ", ".join(missing))
        sys.stderr.write("Jalankan dulu: pkg install python zsh\n")
        return 1

    if not os.environ.get("PREFIX"):
        print("PERINGATAN: ini sepertinya bukan lingkungan Termux.")

    try:
        for key, _label in STEPS:
            if key in ("hook", "instant") and not inst.do_hook:
                continue
            if key == "pin":
                if not inst.do_setup:
                    continue
                print()
                print("== Atur PIN ==")
                bash = shutil.which("bash") or "bash"
                subprocess.call([bash,
                                 os.path.join(inst.dest, "lock.sh"), "set-pin"])
                continue
            getattr(inst, "step_" + key)()
    except InstallError as e:
        sys.stderr.write("ERROR: %s\n" % e)
        return 1

    print()
    print("Selesai. Langkah berikutnya:")
    print("  1. Buka sesi Termux BARU (geser dari tepi kiri -> NEW SESSION)")
    print("  2. Layar kunci akan muncul")
    print()
    print("Perintah: lock-pin | lock-recovery | lock-status")
    return 0


# --------------------------------------------------------------------------- #
# Argumen
# --------------------------------------------------------------------------- #
def print_usage():
    print("""ROUT installer

Pakai:
  bash install.sh [opsi]

Opsi:
  --dest DIR     folder tujuan pemasangan (default: ~/.termux/rout)
  --no-hook      jangan pasang hook otomatis ke ~/.zshrc
  --no-setup     jangan langsung tawarkan set PIN
  -h, --help     tampilkan bantuan ini""")


def parse_args(argv):
    dest = os.path.join(os.path.expanduser("~"), ".termux", "rout")
    do_hook, do_setup, help_ = True, True, False
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--dest":
            i += 1
            if i >= len(argv):
                raise ValueError("--dest butuh argumen")
            dest = argv[i]
        elif a == "--no-hook":
            do_hook = False
        elif a == "--no-setup":
            do_setup = False
        elif a in ("-h", "--help"):
            help_ = True
        else:
            raise ValueError("argumen tidak dikenal: %s" % a)
        i += 1
    return {"dest": dest, "hook": do_hook, "setup": do_setup, "help": help_}


def main(argv):
    try:
        opts = parse_args(argv)
    except ValueError as e:
        sys.stderr.write("ERROR: %s\n" % e)
        print_usage()
        return 1
    if opts["help"]:
        print_usage()
        return 0

    src = os.path.dirname(os.path.realpath(__file__))
    inst = Installer(src, opts["dest"], opts["hook"], opts["setup"])
    if sys.stdin.isatty() and sys.stdout.isatty():
        return run_ui(inst)
    return run_plain(inst)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
