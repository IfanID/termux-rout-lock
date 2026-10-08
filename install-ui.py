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
import importlib.util
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

# Spinner: braille butuh font yang mendukung (Termux pakai Nerd Font, jadi
# aman). SPIN_BRAILLE dipakai kalau terminal mendukung UTF-8, kalau tidak
# turun ke SPIN_ASCII.
# Layar di tengah instalasi tidak menunggu tombol: mereka advancing sendiri
# setelah beberapa detik. Enter cukup ditekan sekali, di layar ringkasan
# terakhir. Layar kode pemulihan memakai AUTO_CODE karena isinya (kode)
# harus sempat dicatat user.
AUTO_SHORT = 3.0
AUTO_LONG = 5.0
AUTO_CODE = 5.0

SPIN_BRAILLE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPIN_ASCII = "|/-\\"
SPIN = list(SPIN_BRAILLE)

# (nama tampil, nama binary)
DEP_BINS = [("python", "python3"), ("zsh", "zsh")]
DEP_NAMES = [n for n, _b in DEP_BINS]


# --------------------------------------------------------------------------- #
# Animasi
# --------------------------------------------------------------------------- #


class Spinner:
    """Frame spinner yang maju mengikuti waktu, bukan jumlah loop."""

    def __init__(self, interval=0.08):
        self.frames = (SPIN_BRAILLE if _supports_unicode()
                       else SPIN_ASCII)
        self.interval = interval
        self.t0 = time.time()

    def frame(self):
        return self.frames[int((time.time() - self.t0) / self.interval)
                           % len(self.frames)]


def _supports_unicode():
    enc = (getattr(sys.stdout, "encoding", None) or "").lower()
    return "utf" in enc


def animate(ui, body_fn, work, min_show=0.30, interval=0.08):
    """Jalankan `work` sambil menggambar ulang `body_fn(frame)`.

    `work` dieksekusi sekali; setelah selesai layar tetap digambar sampai
    `min_show` terlampaui supaya animasinya sempat terlihat (langkah yang
    sangat cepat tidak akan berkedip).
    Exception dari `work` diteruskan setelah animasi selesai.
    """
    sp = Spinner(interval)
    t0 = time.time()
    # `done` harus terpisah dari `result`. Kalau None dipakai sebagai penanda
    # "belum selesai", work() yang mengembalikan None akan membuat loop
    # berjalan tanpa henti (installer membeku).
    done = False
    result = None
    error = None

    while True:
        ui.show(body_fn(sp.frame()))
        if not done:
            try:
                result = work()
                done = True
            except BaseException as e:       # noqa: BLE001
                error = e
                done = True
        if done and time.time() - t0 >= min_show:
            break
        time.sleep(interval)

    if error is not None:
        raise error
    return result


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
    ("recovery",  "Kode pemulihan"),
]

# File yang disalin — tiap file punya baris animasinya sendiri.
FILES = ["lock.py", "lock.sh", "uninstall.sh", "config.conf"]

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
    """Lebar x tinggi terminal yang SEBENARNYA.

    `shutil.get_terminal_size()` membaca variabel COLUMNS/LINES lebih dulu,
    dan variabel itu sering tertinggal berisi ukuran jendela yang lalu.
    Kalau nilainya terlalu besar, tiap baris yang digambar melebihi lebar
    terminal, terminalMEMBUNGKUS baris itu, dan akibatnya beberapa kotak
    tampak bertumpuk. Jadi tanya langsung ke tty dulu; COLUMNS hanya
    dipakai kalau tty tidak bisa dijawab.
    """
    for fd in (1, 0, 2):
        try:
            sz = os.get_terminal_size(fd)
        except OSError:
            continue
        if sz.columns > 0 and sz.lines > 0:
            return sz.columns, sz.lines
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


# Escape warna SGR — lebarnya 0, jadi tidak dihitung saat memotong.
SGR_RE = re.compile("\x1b\\[[0-9;]*m")


def clip_ansi(s, limit):
    """Potong `s` jadi paling banyak `limit` karakter terlihat.

    Kode warna tetap ikut (lebarnya nol). Dipakai supaya tidak ada baris
    yang melebihi lebar terminal — kalau sampai, terminal membungkus baris
    dan kotak jadi berantakan.
    """
    out = []
    vis = 0
    i = 0
    n = len(s)
    while i < n:
        m = SGR_RE.match(s, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        if vis >= limit:
            break
        out.append(s[i])
        vis += 1
        i += 1
    return "".join(out)


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
        text = clip_ansi(text, inner)     # potong per lebar TAMPIL
        pad = " " * max(0, inner - visible_len(text))
        rows.append(fg(C_DIM) + "│ " + fg(color) + text + pad +
                    fg(C_DIM) + " │")
    rows.append(fg(C_DIM) + bot)
    return rows, width


def visible_len(s):
    """Jumlah karakter yang benar-benar terlihat (kode warna diabaikan)."""
    return len(SGR_RE.sub("", s))


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
        # Sisakan 4 kolom: kalau ukuran tty sempat dilaporkan lebih besar
        # dari lebar sebenarnya (mis.inux Android), kotak tetap muat.
        width = max(28, min(60, cols - 4))
        rows, w = box(content, width)
        self._draw(rows, w)

    def _draw(self, rows, width):
        cols, lines = term_size()
        # Di HP ukuran terminal sering berubah (keyboard Android muncul/
        # hilang) DI ANTARA hitungan lebar dan penggambarannya. Kalau kotak
        # keburu lebih lebar dari terminal, tiap baris membungkus dan
        # layarnya berantakan. Jadi Always potong baris ke lebar yang
        # benar-benar tersedia saat menulis.
        left = max(0, (cols - width) // 2)
        avail = max(1, cols - left)
        pad = " " * left
        top = max(1, (lines - len(rows)) // 2 + 1)
        buf = [bg(C_BG), "\033[H"]
        for r in range(1, lines + 1):
            buf.append("\033[%d;1H" % r)
            buf.append("\033[K")
            idx = r - top
            if 0 <= idx < len(rows):
                buf.append(pad)
                buf.append(clip_ansi(rows[idx], avail))
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


def screen_dep_asked_again(missing):
    """Dipakai kalau install.sh sudah menanyakan dependensi lebih dulu."""
    body = head_lines()
    for name in missing:
        body.append(("  !  %-8s belum terpasang" % name, C_ERR))
    body += [
        ("", C_FG),
        ("  Sudah ditanyakan sebelumnya, tapi", C_FG),
        ("  belum berhasil. Selesaikan manual:", C_FG),
        ("", C_FG),
        ("     pkg install %s" % " ".join(missing), C_ACC),
        ("", C_FG),
        ("  Lalu jalankan lagi:  bash install.sh", C_FG),
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


def screen_checking(frame):
    """Layar saat memeriksa python/zsh — dengan spinner."""
    body = head_lines()
    body += [
        ("", C_FG),
        ("  %s  Memeriksa python & zsh..." % frame, C_ACC),
        ("", C_FG),
        ("     cek: python3 ....... ", C_DIM),
        ("     cek: zsh ...........", C_DIM),
        ("", C_FG),
    ]
    return body


def screen_steps(statuses, note="", files=None, frame="…"):
    """Daftar langkah + daftar file, keduanya memakai mark yang sama."""
    body = head_lines()

    if files is not None:
        body.append(("  Berkas:", C_FG))
        for name in FILES:
            st = (files or {}).get(name, "pending")
            if st == "done":
                mark, color = "✓", C_OK
            elif st == "running":
                mark, color = frame, C_ACC
            elif st == "failed":
                mark, color = "!", C_ERR
            elif st == "skip":
                mark, color = "–", C_DIM
            else:
                mark, color = "○", C_DIM
            body.append(("    %s  %s" % (mark, name), color))
        body.append(("", C_FG))

    for key, label in STEPS:
        st = statuses.get(key, "pending")
        if st == "done":
            mark, color = "✓", C_OK
        elif st == "running":
            mark, color = frame, C_ACC
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


def screen_done(dest, pin_set, rec_set=False):
    # Baris status harus muat di kotak sempit (~38 karakter isi di layar
    # 40 kolom). Kalau lebih panjang, barisnya dipotong di tengah kalimat
    # dan jadi tidak terbaca.
    lock = "aktif (PIN)" if pin_set else "belum diset"
    lcolor = C_OK if pin_set else C_ERR
    if not pin_set:
        rec = "—"
    elif rec_set:
        rec = "aktif (kode ada)"
    else:
        rec = "BELUM DISET"
    rcolor = C_OK if rec_set else (C_DIM if not pin_set else C_ERR)
    return [
        ("", C_FG),
        ("  ✓  Semua selesai", C_OK),
        ("", C_FG),
        ("  Terpasang di : %s" % tilde(dest), C_FG),
        ("  Lock         : %s" % lock, lcolor),
        ("  Pemulihan   : %s" % rec, rcolor),
        ("", C_FG),
        ("  Berikutnya:", C_FG),
        ("  1. Buka sesi Termux BARU", C_FG),
        ("  2. Layar kunci akan muncul", C_FG),
        ("", C_FG),
        ("  lock-pin · lock-recovery", C_DIM),
        ("  lock-status · lock-backup", C_DIM),
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


def _conf_flag(conf_path, key):
    """True kalau `key` punya nilai di config.

    Semua baris diperiksa, bukan cuma yang pertama: kalau ada kunci yang
    terduplikasi, nilai yang terisi ikut dihitung.
    """
    try:
        with open(conf_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(key + "="):
                    if line.split("=", 1)[1].strip():
                        return True
    except OSError:
        pass
    return False


def read_pin_set(conf_path):
    return _conf_flag(conf_path, "pin_hash")


def read_rec_set(conf_path):
    return _conf_flag(conf_path, "recovery_hash")


# --------------------------------------------------------------------------- #
# Setting PIN & kode pemulihan (di dalam UI installer)
# --------------------------------------------------------------------------- #
# PIN dipakai keypad yang SAMA dengan layar kunci — dimuat dari lock.py
# yang baru saja disalin, supaya tidak ada keypad kedua yang harus dijaga.
# Kode pemulihan boleh huruf, jadi diketik lewat keyboard; layar setup
# menjelaskan caranya.
#
# Yang dibuang: CSI (termasuk laporan mouse SGR `ESC [ < b ; x ; y M`),
# OSC, dan panah. Yang tersisa: karakter biasa + ESC polos (tombol batal).
ESC_SEQ = re.compile(r"\x1b\[[0-9;?<>]*[a-zA-Z~]"
                     r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
                     r"|\x1b[OPQRS][A-Za-z~]"
                     r"|\x1b[NO]")

# lock.py dimuat satu kali per folder tujuan. Selain lebih cepat, ini
# membuat modul bisa diganti (mis. saat pengujian).
_LOCK_CACHE = {}


def load_lock(dest):
    """Impor lock.py dari folder tujuan. None kalau belum ada."""
    path = os.path.realpath(os.path.join(dest, "lock.py"))
    if path in _LOCK_CACHE:
        return _LOCK_CACHE[path]
    if not os.path.exists(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location("rout_lock_" +
                                                       os.path.basename(dest),
                                                       path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception:
        return None
    _LOCK_CACHE[path] = mod
    return mod


def _clean_keys(raw):
    """Buang sequence panah/F-keys; sisakan ESC sebagai tombol batal."""
    s = ESC_SEQ.sub("", raw)
    # sisa ESC + '[' atau 'O' yang tak lengkap -> buang
    if len(s) == 2 and s[0] == "\x1b" and s[1] in "[O":
        return ""
    return s


def prompt_setup_line(ui, title, value="", hint="", maxlen=32):
    """Ketik satu baris teks (kode pemulihan). Return (nilai, ok)."""
    val = value
    while True:
        ui.show(screen_setup_line(title, val, hint))
        raw = ui.key(0.4)
        if raw is None:
            continue
        for ch in _clean_keys(raw):
            if ch in ("\x1b", "\x03"):
                return "", False
            if ch in ("\r", "\n"):
                return val, True
            if ch in ("\x7f", "\b"):
                val = val[:-1]
                continue
            if ch in ("�", "\x00"):
                # Byte multi-byte terpotong / karakter hasil decode
                # error: diamkan, jangan masukkan ke kode.
                continue
            if ch.isprintable() and len(val) < maxlen:
                val += ch


def run_pin_setup(ui, inst):
    """Set PIN lewat keypad layar penuh.

    Return (rc, dict). rc 0 = PIN tersimpan, 1 = dibatalkan user.
    """
    lock = load_lock(inst.dest)
    conf_path = os.path.join(inst.dest, "config.conf")
    if lock is None:
        # lock.py belum ada / gagal dimuat -> pakai lock.sh set-pin biasa.
        return _pin_setup_fallback(inst), {}

    conf = lock.parse_conf(conf_path)
    conf["message"] = "A T U R   P I N"
    conf["show_clock"] = "0"
    conf["show_date"] = "0"
    min_len = int(conf.get("pin_min_len") or 4)
    screen = lock.Screen(conf)

    state = {"mode": "setup", "status": "Masukkan PIN baru",
             "pin": "", "error": "", "confirm_close": False}
    stage = "new1"
    pin1 = ""

    screen.enter()
    try:
        while True:
            screen.draw(state)
            text, taps = lock.read_input(0.4)
            if text is None and not taps:
                continue

            # Tap keypad = mengetik karakter biasa, seperti di layar kunci.
            typed = []
            for col, row in taps:
                k = screen.hit_test(col, row)
                if k:
                    typed.append(k)
            if typed:
                text = "".join(typed) + text

            state["error"] = ""
            for ch in text:
                if ch in ("\x1b", "\x03"):
                    # Isian sudah kosong + Esc/Ctrl-C = batal set PIN.
                    # Kalau masih ada isian, Esc hanya menghapus.
                    if not state["pin"]:
                        return 1, {}
                    state["pin"] = ""
                elif ch in ("\r", "\n"):
                    if len(state["pin"]) < min_len:
                        state["error"] = "PIN minimal %d angka." % min_len
                        state["pin"] = ""
                    elif stage == "new1":
                        pin1 = state["pin"]
                        state["pin"] = ""
                        stage = "new2"
                        state["status"] = "Ulangi PIN baru"
                    elif state["pin"] == pin1:
                        salt = lock.gen_salt()
                        lock.update_conf({"pin_salt": salt,
                                          "pin_hash": lock.hash_pin(pin1, salt)},
                                         path=conf_path)
                        return 0, {"pin": pin1}
                    else:
                        # Tidak sama -> ulangi dari awal.
                        state["error"] = "Tidak sama. Mulai lagi."
                        state["status"] = "Masukkan PIN baru"
                        state["pin"] = ""
                        pin1 = ""
                        stage = "new1"
                elif ch in ("\x7f", "\b"):
                    state["pin"] = state["pin"][:-1]
                elif ch.isdigit() and len(state["pin"]) < 24:
                    state["pin"] += ch
    finally:
        screen.leave()


def _pin_setup_fallback(inst):
    """Kalau keypad tidak bisa dipakai, serahkan ke lock.sh set-pin."""
    bash = shutil.which("bash") or "bash"
    try:
        rc = subprocess.call([bash, os.path.join(inst.dest, "lock.sh"),
                              "set-pin"])
    except OSError:
        return 1
    return rc if rc == 0 else 1


def run_recovery_setup(ui, inst):
    """Minta kode pemulihan + konfirmasi.

    Tidak ada pertanyaan Y/N — kode pemulihan selalu diminta, karena
    tanpa kode lupa PIN tidak bisa keluar. Yang tersisa: `Esc` untuk
    melewati (langkah ditandai `–`, kode lama tetap utuh).

    Return (rc, dict). rc 0 = tersimpan atau dilewati, 1 = tidak ada PIN.
    """
    lock = load_lock(inst.dest)
    conf_path = os.path.join(inst.dest, "config.conf")
    if lock is None:
        return 1, {}
    conf = lock.parse_conf(conf_path)
    if not (conf.get("pin_hash") or "").strip():
        return 1, {}

    # Lebar layar cukup untuk kode yang enak dibaca + tombol Esc batal.
    code, ok1 = prompt_setup_line(
        ui, "Kode pemulihan",
        hint="Minimal 4 karakter, boleh huruf & angka. "
             "Simpan baik-baik — ini satu-satunya jalan kalau lupa PIN. "
             "Esc = lewati.")
    if not ok1 or not code:
        return 0, {"skipped": True}
    code2, ok2 = prompt_setup_line(
        ui, "Ulangi kode pemulihan",
        hint="Ketik ulang kode yang sama persis. Isian sengaja "
             "dikosongkan supaya tidak salah ketik. Esc = lewati.")
    if not ok2:
        return 0, {"skipped": True}
    if code2 != code:
        ui.show(screen_setup_error("Kode tidak sama. "
                                   "Kode pemulihan tidak diubah."))
        wait_key(ui, auto=AUTO_SHORT)
        return 0, {"skipped": True}
    if len(code) < 4:
        ui.show(screen_setup_error("Kode minimal 4 karakter. "
                                   "Kode pemulihan tidak diubah."))
        wait_key(ui, auto=AUTO_SHORT)
        return 0, {"skipped": True}

    rsalt = lock.gen_salt()
    lock.update_conf({"recovery_salt": rsalt,
                      "recovery_hash": lock.hash_pin(code, rsalt)},
                     path=conf_path)
    # Kode hanya ditampilkan SEKALI di sini — tidak ada cara melihatnya lagi
    # nanti, jadi user harus sempat mencatatnya.
    wait_countdown(ui, screen_recovery_done(code), AUTO_CODE)
    return 0, {"code": code}


def screen_recovery_done(code):
    body = head_lines()
    body += [
        ("", C_FG),
        ("  ✓  Kode pemulihan disimpan", C_OK),
        ("", C_FG),
        ("  CATAT KODE INI:", C_ERR),
        ("", C_FG),
        ("      %s" % code, C_ACC),
        ("", C_FG),
        ("  Dipakai kalau lupa PIN: di layar kunci tekan R,", C_FG),
        ("  lalu masukkan kode di atas.", C_FG),
        ("", C_FG),
        ("  Disimpan di tempat aman. Kode tidak bisa dilihat", C_DIM),
        ("  lagi nanti — hanya hash-nya yang tersimpan.", C_DIM),
        ("", C_FG),
    ]
    return body


def screen_setup_line(title, value, hint=""):
    body = head_lines()
    body += [
        ("", C_FG),
        ("  %s" % title, C_ACC),
        ("", C_FG),
    ]
    if hint:
        for ln in wrap_text(hint, 40):
            body.append(("  %s" % ln, C_DIM))
        body.append(("", C_FG))
    shown = value if value else "…"
    body.append(("  > %s" % shown, C_FG if value else C_DIM))
    body.append(("", C_FG))
    body.append(("  [ ENTER ]  lanjut     [ Esc ]  batal", C_DIM))
    return body


def screen_ask_recovery():
    """Layar pengantar (dipakai preview). Kode diminta langsung, tanpa Y/N."""
    body = head_lines()
    body += [
        ("", C_FG),
        ("  Kode pemulihan", C_ACC),
        ("", C_FG),
        ("  Satu-satunya jalan keluar kalau lupa PIN.", C_FG),
        ("  Dipakai di layar kunci: tekan R, lalu ketik kodenya.", C_FG),
        ("", C_FG),
        ("  Minimal 4 karakter, boleh huruf & angka.", C_FG),
        ("", C_FG),
    ]
    return body


def screen_setup_error(msg):
    body = head_lines()
    body += [
        ("", C_FG),
        ("  !  %s" % msg, C_ERR),
        ("", C_FG),
    ]
    return body


def wrap_text(text, width):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w) if cur else w
    if cur:
        lines.append(cur)
    return lines


# --------------------------------------------------------------------------- #
# Installer
# --------------------------------------------------------------------------- #
class Installer:
    def __init__(self, src, dest, do_hook, do_setup, zshrc=None):
        self.src = src
        self.dest = dest
        self.do_hook = do_hook
        self.do_setup = do_setup
        self.quiet = False
        self.log = []
        # Status tiap file: pending / running / done / skip / failed.
        # Dipakai supaya tiap berkas punya animasinya sendiri.
        self.file_state = {n: "pending" for n in FILES}
        # Dipanggil Installer tiap kali status file berubah, supaya UI
        # bisa langsung menggambar ulang di tengah langkah.
        self.on_update = None
        zdir = os.environ.get("ZDOTDIR") or os.path.expanduser("~")
        # zshrc boleh diberikan eksplisit (dipakai tes & instalasi ke direktori
        # lain) supaya tidak pernah menyentuh ~/.zshrc milik pengguna.
        self.zshrc = zshrc or os.path.join(zdir, ".zshrc")

    def _file(self, name, status):
        """Ubah status satu file & beri tahu UI."""
        self.file_state[name] = status
        if self.on_update:
            self.on_update()

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
        for name in ("lock.py", "lock.sh", "uninstall.sh"):
            self._file(name, "running")
            p = os.path.join(self.dest, name)
            if same:
                ok = os.path.exists(p)
            else:
                s = os.path.join(self.src, name)
                ok = os.path.exists(s)
                if ok:
                    shutil.copyfile(s, p)
            if ok:
                try:
                    os.chmod(p, 0o700)
                except OSError:
                    pass
                self._file(name, "done")
            else:
                self._file(name, "skip")
        if same:
            self._log("  [ok] sumber = tujuan (repo dipakai langsung), "
                      "tidak menyalin file")
        else:
            self._log("  [ok] lock.py, lock.sh & uninstall.sh dipasang")

    def step_config(self):
        conf = os.path.join(self.dest, "config.conf")
        self._file("config.conf", "running")
        if os.path.exists(conf):
            self._file("config.conf", "skip")
            self._log("  [ok] config.conf sudah ada (tidak ditimpa)")
            return
        template = os.path.join(self.src, "config.default.conf")
        if not os.path.exists(template):
            self._file("config.conf", "failed")
            raise InstallError("config.default.conf tidak ditemukan")
        shutil.copyfile(template, conf)
        try:
            os.chmod(conf, 0o600)
        except OSError:
            pass
        self._file("config.conf", "done")
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
  # Matikan tampilan ini dengan:  sed -i 's/^show_motd=.*/show_motd=0/' \
  #     ~/.termux/rout/config.conf
  _termux_motd_cfg="$HOME/.termux/rout/config.conf"
  if [ ! -r "$_termux_motd_cfg" ] \
     || ! grep -q '^show_motd=0' "$_termux_motd_cfg" 2>/dev/null; then
    _termux_motd="${PREFIX:-/data/data/com.termux/files/usr}/etc/motd.sh"
    [ -r "$_termux_motd" ] && bash "$_termux_motd"
  fi

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
alias lock-backup='"$TERMUX_LOCK_SH" backup'
alias lock-restore='"$TERMUX_LOCK_SH" restore'
# ===== end ROUT =====
'''


def hook_block(lock_sh):
    return HOOK_BLOCK.replace("__ROUT_LOCK_SH__", lock_sh)


# --------------------------------------------------------------------------- #
# Alur UI
# --------------------------------------------------------------------------- #
def wait_key(ui, auto=None):
    """Tunggu satu tombol dari pengguna.

    `auto` = lamanya (detik) sebelum layar lanjut sendiri. Dipakai untuk
    layar-layar di tengah supaya tidak perlu menekan Enter berulang kali —
    tombol Enter tinggal ditekan sekali, di layar ringkasan terakhir.
    Tanpa `auto`, menunggu tanpa batas (layar penutup).
    """
    t0 = time.time()
    while True:
        if auto is not None and time.time() - t0 >= auto:
            return
        k = ui.key(None if auto is None else 0.25)
        if k is None:
            continue
        for ch in k:
            if ch in ("\r", "\n", "q", "\x1b", "\x03"):
                return
        # Enter lewat keypad
        if k.startswith("\x1b"):
            return


def wait_countdown(ui, content, seconds):
    """Tampilkan `content` sambil menghitung mundur, lalu lanjut sendiri.

    Untuk layar yang isinya harus sempat dibaca (mis. kode pemulihan) —
    tekan Enter untuk lanjut sekarang juga boleh.
    """
    t0 = time.time()
    while True:
        left = int(round(seconds - (time.time() - t0)))
        if left <= 0:
            return
        ui.show(list(content) + [
            ("", C_FG),
            ("  Lanjut sendiri dalam %d detik" % left, C_DIM),
            ("  Enter untuk lanjut sekarang", C_DIM),
            ("", C_FG),
        ])
        k = ui.key(0.25)
        if k is None:
            continue
        for ch in k:
            if ch in ("\r", "\n", "q", "\x1b", "\x03"):
                return


def show_manual(ui, missing, notermux=False):
    ui.show(screen_manual(missing, notermux))
    wait_key(ui, auto=AUTO_SHORT)


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
        # Cek dependency boleh sangat cepat, jadi diberi spinner minimal
        # 0.6 detik supaya kelihatan animasinya.
        missing = animate(ui, screen_checking, missing_deps, min_show=0.6)
        if missing:
            # install.sh sudah menanyakan & mencoba memasang dependensi
            # lebih dulu. Menanyakan lagi akan membingungkan, jadi di sini
            # cukup diberi tahu hasilnya.
            if os.environ.get("ROUT_DEPS_ASKED") == "1":
                ui.show(screen_dep_asked_again(missing))
                wait_key(ui, auto=AUTO_LONG)
                return 1
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
                wait_key(ui, auto=AUTO_LONG)
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
    box = {"frame": "…"}
    # Hasil langkah PIN -> dipakai langkah berikutnya (kode pemulihan).
    setup = {"pin": None}
    conf_path = os.path.join(inst.dest, "config.conf")

    def body(frame):
        # Simpan frame terbaru supaya callback on_update (yang dipanggil di
        # tengah langkah, dari dalam work) memakai spinner yang sama.
        box["frame"] = frame
        return screen_steps(statuses, files=inst.file_state, frame=frame)

    # Installer memberi tahu UI tiap kali status file berubah, supaya tiap
    # berkas punya animasinya sendiri di tengah langkah.
    def on_file_update():
        try:
            ui.show(body(box["frame"]))
        except OSError:
            pass
    inst.on_update = on_file_update

    ui.show(body(box["frame"]))

    for key, _label in STEPS:
        if key in ("hook", "instant") and not inst.do_hook:
            statuses[key] = "skip"
            ui.show(body(box["frame"]))
            continue
        if key in ("pin", "recovery") and not inst.do_setup:
            statuses[key] = "skip"
            ui.show(body(box["frame"]))
            continue
        # Kode pemulihan hanya relevan kalau PIN-nya sudah ada — baik PIN yang
        # baru saja diset, maupun PIN yang dari instalasi sebelumnya.
        if key == "recovery" and not (setup.get("pin")
                                      or read_pin_set(conf_path)):
            statuses[key] = "skip"
            ui.show(body(box["frame"]))
            continue

        statuses[key] = "running"
        ui.show(body(box["frame"]))

        if key == "pin":
            # Set PIN & kode pemulihan di DALAM UI installer, pakai keypad
            # yang sama dengan layar kunci — user tidak perlu mengetik
            # perintah apa pun setelah instalasi selesai.
            try:
                rc, info = run_pin_setup(ui, inst)
            except Exception:            # noqa: BLE001
                rc, info = 1, {}
            if rc == 0:
                setup["pin"] = info.get("pin")
                statuses[key] = "done"
            else:
                # User batal / tidak jadi — bukan kegagalan instalasi.
                statuses[key] = "skip"
            ui.show(body(box["frame"]))
            continue

        if key == "recovery":
            try:
                rc, info = run_recovery_setup(ui, inst)
            except Exception:            # noqa: BLE001
                rc, info = 1, {}
            # "done" hanya kalau kodenya benar-benar tersimpan; dilewati
            # kalau user menjawab N atau kode tidak valid.
            statuses[key] = ("done" if (rc == 0 and not info.get("skipped"))
                             else "skip")
            ui.show(body(box["frame"]))
            continue

        try:
            work = getattr(inst, "step_" + key)
            # Setiap langkah jalan dengan spinner; langkah yang sangat cepat
            # tetap ditampilkan minimal 0.3 detik supaya tidak berkedip.
            # `work` tidak boleh menerima argumen — animate() memanggilnya
            # tanpa argumen; frame spinner datang dari body().
            animate(ui, body, work)
            statuses[key] = "done"
        except InstallError as e:
            statuses[key] = "failed"
            ui.show(screen_steps(statuses, "Gagal: %s" % e,
                                 files=inst.file_state))
            wait_key(ui, auto=AUTO_LONG)
            return 1
        except Exception as e:  # noqa: BLE001
            statuses[key] = "failed"
            ui.show(screen_steps(statuses, "Gagal: %s" % e,
                                 files=inst.file_state))
            wait_key(ui, auto=AUTO_LONG)
            return 1
        ui.show(body(box["frame"]))

    inst.on_update = None
    pin_set = read_pin_set(conf_path)
    rec_set = read_rec_set(conf_path)
    ui.show(screen_done(inst.dest, pin_set, rec_set))
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
        sys.stderr.write("Jalankan dulu: pkg install %s\n" % " ".join(missing))
        if os.environ.get("ROUT_DEPS_ASKED") == "1":
            sys.stderr.write("(sudah ditanyakan otomatis oleh install.sh "
                             "tetapi belum berhasil)\n")
        return 1

    if not os.environ.get("PREFIX"):
        print("PERINGATAN: ini sepertinya bukan lingkungan Termux.")

    try:
        for key, _label in STEPS:
            if key in ("hook", "instant") and not inst.do_hook:
                continue
            if key in ("pin", "recovery"):
                # Tanpa TTY tidak ada keypad, jadi set-pin dipakai apa adanya
                # (lock.py sudah menanyakan kode pemulihan di dalamnya).
                if not inst.do_setup:
                    continue
                if key == "recovery":
                    continue
                print()
                print("== Atur PIN (+ kode pemulihan) ==")
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
    print("Perintah: lock-pin | lock-recovery | lock-status | lock-backup")
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
  --zshrc FILE   file zshrc yang dituju (default: $ZDOTDIR/.zshrc)
  --no-hook      jangan pasang hook otomatis ke ~/.zshrc
  --no-setup     jangan langsung tawarkan set PIN
  -h, --help     tampilkan bantuan ini""")


def parse_args(argv):
    dest = os.path.join(os.path.expanduser("~"), ".termux", "rout")
    do_hook, do_setup, help_ = True, True, False
    zshrc = None
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--dest":
            i += 1
            if i >= len(argv):
                raise ValueError("--dest butuh argumen")
            dest = argv[i]
        elif a == "--zshrc":
            i += 1
            if i >= len(argv):
                raise ValueError("--zshrc butuh argumen")
            zshrc = argv[i]
        elif a == "--no-hook":
            do_hook = False
        elif a == "--no-setup":
            do_setup = False
        elif a in ("-h", "--help"):
            help_ = True
        else:
            raise ValueError("argumen tidak dikenal: %s" % a)
        i += 1
    return {"dest": dest, "hook": do_hook, "setup": do_setup, "help": help_,
            "zshrc": zshrc}


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
    inst = Installer(src, opts["dest"], opts["hook"], opts["setup"],
                      zshrc=opts.get("zshrc"))
    if sys.stdin.isatty() and sys.stdout.isatty():
        return run_ui(inst)
    return run_plain(inst)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
