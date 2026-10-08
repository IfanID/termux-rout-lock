#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ROUT — Termux Lock Screen
=========================
Layar kunci untuk Termux: menutup penuh terminal, dibuka dengan PIN.
Ganti PIN pakai PIN lama. Kalau lupa PIN, pakai kode pemulihan (tombol 'r'
di layar kunci) untuk membuat PIN baru.

Perintah:
  lock.py run            Menampilkan layar kunci (dipanggil dari .zshrc)
  lock.py set-pin        Ganti / atur PIN
  lock.py status         Lihat status
  lock.py enable|disable Aktif / nonaktifkan lock
"""

import os
import sys
import time
import hmac
import hashlib
import shutil
import select
import termios
import tty
import signal
import re

ROUT_DIR   = os.path.dirname(os.path.realpath(__file__))
CONF_PATH  = os.path.join(ROUT_DIR, "config.conf")
STATE_PATH = os.path.join(ROUT_DIR, "lock.state")

RESET = "\033[0m"

# --------------------------------------------------------------------------- #
# Keypad angka di dalam layar kunci
#
# Termux tidak bisa dipaksa menampilkan keyboard angka: view-nya bukan
# EditText dan TerminalView.onCreateInputConnection() mengunci
# outAttrs.inputType = InputType.TYPE_NULL. Jadi keypadnya kita gambar sendiri.
#
# Sentuhan jari dilaporkan Termux sebagai event mouse HANYA kalau mouse tracking
# aktif (TerminalView.GestureAndScaleRecognizer.Listener.onUp() memanggil
# sendMouseEventCode() bila mEmulator.isMouseTrackingActive()). Maka:
#   1000 -> kirim klik tekan + lepas
#   1006 -> protokol SGR, jadi koordinat tidak terpotong di layar lebar
# emulator lalu menulis ke stdin:
#   ESC [ < 0 ; KOL ; BAR M   (tekan)      ESC [ < 0 ; KOL ; BAR m   (lepas)
# Kolom/baris adalah indeks sel terminal (1-based), jadi bisa di-hit-test.
# --------------------------------------------------------------------------- #
MOUSE_ON  = b"\033[?1006h\033[?1000h"
MOUSE_OFF = b"\033[?1000l\033[?1006l"

MOUSE_RE = re.compile(rb"\x1b\[<(\d+);(\d+);(\d+)([Mm])")
# Escape lain (CSI, SS3, OSC) dibuang supaya tidak ikut jadi karakter PIN.
# Dijalankan SETELAH MOUSE_RE supaya event mouse tidak ikut tertangkap.
ESC_RE   = re.compile(
    rb"\x1b(?:\[[0-?]*[ -/]*[@-~]"        # CSI, mis. ESC [ A atau ESC [ 200 ~
    rb"|\][^\x07\x1b]*(?:\x07|\x1b\\)"    # OSC, mis. ESC ] 0 ; x BEL
    rb"|O[ -/]*[0-~])"                    # SS3 / F-key, mis. ESC O P
)
PASTE_RE = re.compile(rb"\x1b\[20[01]~")

KEYPAD_ROWS = [
    ["1", "2", "3"],
    ["4", "5", "6"],
    ["7", "8", "9"],
    ["\x1b", "0", "\x7f"],
]
KEYPAD_ENTER = "\r"
KEYPAD_MIN_COLS = 26
KEYPAD_MIN_ROWS = 34        # minimum baris terminal agar keypad tampil

# Tinggi tombol (baris "isi", di dalam garis atas & bawah) adaptsif.
KEYPAD_H_MIN = 2
KEYPAD_H_MAX = 5
# Baris yang dipakai blok teks di atas keypad (judul, jam, tanggal, status,
# PIN, petunjuk). Dipakai untuk menghitung sisa tinggi tombol.
KEYPAD_TEXT_ROWS = 14

KEYPAD_LABELS = {d: d for d in "0123456789"}
KEYPAD_LABELS.update({
    "\x1b": "ESC",
    "\x7f": "DEL",
    "\r":   "ENTER",
    "r":    "R lupa",
    "q":    "Q tutup",
    "\x11": "Q tutup",   # Ctrl-Q: tutup sesi di mode pemulihan
})

HARI  = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
         "Agustus", "September", "Oktober", "November", "Desember"]

DEFAULTS = {
    "enabled":             "1",
    "allow_pin":           "1",
    "show_clock":          "1",
    "show_date":           "1",
    "lock_once_per_open":  "1",
    "message":             "TERMUX LOCK",
    "color_bg":            "16,16,26",
    "color_fg":            "226,226,238",
    "color_accent":        "255,86,120",
    "color_dim":           "130,130,150",
    "color_err":           "255,120,120",
    "color_key":           "40,40,58",
    "pin_min_len":         "4",
    "pin_salt":            "",
    "pin_hash":            "",
    "recovery_salt":       "",
    "recovery_hash":       "",
    "panic_file":          "1",
    "pin_keypad":          "1",
}

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
BACKUP_DIR  = os.path.join(ROUT_DIR, "backup")
BACKUP_KEEP = 5          # simpan 5 backup terakhir


def backup_conf(path=None):
    """Salin config.conf ke ~/.termux/rout/backup/ sebelum ditimpa.

    Backup menyimpan hash PIN & kode pemulihan, jadi foldernya privat
    (0700) dan tiap file 0600. File lama yang melebihi BACKUP_KEEP dihapus.

    `path` sengaja bukan default argument: nilai `path=CONF_PATH` akan
    terikat saat fungsi didefinisikan, sehingga men reassign CONF_PATH tidak
    mengubah targetnya.
    """
    if path is None:
        path = CONF_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = f.read()
    except (FileNotFoundError, OSError):
        return None

    # tidak perlu backup kalau tidak ada hash yang hilang
    if not any(re.search(r"^(pin_hash|recovery_hash)=.+", line, re.M)
               for line in data.splitlines()):
        return None

    try:
        os.makedirs(BACKUP_DIR, mode=0o700, exist_ok=True)
        os.chmod(BACKUP_DIR, 0o700)
    except OSError:
        return None

    # pakai detik + mikrodetik supaya nama selalu urut kronologis bahkan
    # kalau beberapa backup dibuat dalam satu detik yang sama.
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-%06d" % (time.time() % 1 * 1e6)
    dest = os.path.join(BACKUP_DIR, "config-%s.conf" % stamp)
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = os.path.join(BACKUP_DIR, "config-%s-%d.conf" % (stamp, n))

    try:
        with open(dest, "w", encoding="utf-8") as f:
            f.write(data)
        os.chmod(dest, 0o600)
    except OSError:
        return None

    prune_backups()
    return dest


def prune_backups(keep=BACKUP_KEEP):
    """Hapus backup paling lama supaya tidak menumpuk."""
    all_paths = _sorted_backups()
    stale = all_paths[:-keep] if keep > 0 else all_paths
    for path in stale:
        try:
            os.remove(path)
        except OSError:
            pass


def _sorted_backups():
    """Semua backup, paling lama dulu. Urut dari mtime, bukan nama file,
    jadi nama dengan sufiks angka tetap tidak mengacaukan urutan."""
    try:
        names = [n for n in os.listdir(BACKUP_DIR)
                 if n.startswith("config-") and n.endswith(".conf")]
    except OSError:
        return []
    paths = [os.path.join(BACKUP_DIR, n) for n in names]
    try:
        return sorted(paths, key=lambda p: (os.stat(p).st_mtime, p))
    except OSError:
        return sorted(paths)


def list_backups():
    """Backup yang tersedia, terbaru dulu."""
    return list(reversed(_sorted_backups()))


def parse_conf(path=None):
    """Baca config. `path=None` memakai CONF_PATH saat fungsi dipanggil."""
    if path is None:
        path = CONF_PATH
    conf = dict(DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                conf[key.strip()] = val.strip()
    except FileNotFoundError:
        pass
    return conf


def update_conf(updates, path=None):
    # `path=None` supaya CONF_PATH dibaca saat dipanggil, bukan saat fungsi
    # didefinisikan. Kalau memakai `path=CONF_PATH`, reassign CONF_PATH tidak
    # berefek dan file yang ditimpa bisa keliru.
    if path is None:
        path = CONF_PATH

    # Backup dulu SEBELUM menimpa. config.conf tidak ada di git (gitignore),
    # jadi tanpa ini hash PIN yang hilang tidak bisa dipulihkan.
    backup_conf(path)

    lines = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        pass

    seen, out = set(), []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                out.append("%s=%s" % (key, updates[key]))
                seen.add(key)
                continue
        out.append(line)

    if out:
        out.append("")
    for key, val in updates.items():
        if key not in seen:
            out.append("%s=%s" % (key, val))

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip("\n") + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def hash_pin(pin, salt):
    return hashlib.sha256((salt + ":" + pin).encode("utf-8")).hexdigest()


def gen_salt():
    return os.urandom(16).hex()


def verify_pin(conf, pin):
    stored = (conf.get("pin_hash") or "").strip()
    if not stored or not pin:
        return False
    return hmac.compare_digest(hash_pin(pin, conf.get("pin_salt", "")), stored)


def verify_recovery(conf, code):
    stored = (conf.get("recovery_hash") or "").strip()
    if not stored or not code:
        return False
    return hmac.compare_digest(hash_pin(code, conf.get("recovery_salt", "")), stored)


# --------------------------------------------------------------------------- #
# Warna / util terminal
# --------------------------------------------------------------------------- #
def parse_rgb(text, default):
    try:
        parts = [int(x) for x in str(text).split(",")]
        if len(parts) == 3:
            return tuple(max(0, min(255, p)) for p in parts)
    except ValueError:
        pass
    return default


def fg(rgb):
    return "\033[38;2;%d;%d;%dm" % rgb


def bg(rgb):
    return "\033[48;2;%d;%d;%dm" % rgb


def term_size():
    try:
        size = shutil.get_terminal_size((80, 24))
        return size.columns, size.lines
    except Exception:
        return 80, 24


def tanggal_id(t):
    return "%s, %d %s %d" % (HARI[t.tm_wday], t.tm_mday, BULAN[t.tm_mon - 1], t.tm_year)


def proc_starttime(pid):
    try:
        with open("/proc/%s/stat" % pid, "r", encoding="utf-8") as f:
            data = f.read()
        rp = data.rfind(")")
        fields = data[rp + 2:].split()
        return fields[19]  # field 22 = starttime
    except Exception:
        return "?"


def parent_pid():
    return os.environ.get("TERMUX_LOCK_PARENT_PID") or str(os.getppid())


def app_token():
    pid = parent_pid()
    return "%s:%s" % (pid, proc_starttime(pid))


# --------------------------------------------------------------------------- #
# Marker "sekali per buka Termux"
# --------------------------------------------------------------------------- #
def already_locked():
    if (parse_conf().get("lock_once_per_open") or "1") != "1":
        return False
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            token = f.read().strip()
    except FileNotFoundError:
        return False
    return bool(token) and token == app_token()


def mark_locked():
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            f.write(app_token() + "\n")
        os.chmod(STATE_PATH, 0o600)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Layar kunci
# --------------------------------------------------------------------------- #
class Screen:
    def __init__(self, conf):
        self.conf  = conf
        self.c_bg  = parse_rgb(conf.get("color_bg"),     (16, 16, 26))
        self.c_fg  = parse_rgb(conf.get("color_fg"),     (226, 226, 238))
        self.c_acc = parse_rgb(conf.get("color_accent"), (255, 86, 120))
        self.c_dim = parse_rgb(conf.get("color_dim"),    (130, 130, 150))
        self.c_err = parse_rgb(conf.get("color_err"),    (255, 120, 120))
        self.saved = None
        self.keypad_on = conf.get("pin_keypad", "1") != "0"
        # Peta hit-test tombol: [(baris, kolom_awal, kolom_akhir, karakter)]
        self.keypad_map = []
        self.rec_set = bool((conf.get("recovery_hash") or "").strip())
        # Termux tidak punya API untuk menyembunyikan soft keyboard dari dalam
        # shell. Satu-satunya cara adalah tombol KEYBOARD di extra-keys, jadi
        # kalau keypad tampil kita kasih petunjuknya.
        self.show_kbd_hint = (conf.get("pin_keypad", "1") != "0"
                              and conf.get("keypad_kbd_hint", "1") != "0")

    def enter(self):
        self.saved = termios.tcgetattr(sys.stdin.fileno())
        tty.setraw(sys.stdin.fileno())
        seq = b"\033[?1049h\033[?25l\033[2J"
        if self.keypad_on:
            seq += MOUSE_ON
        os.write(sys.stdout.fileno(), seq)

    def leave(self):
        seq = b""
        if self.keypad_on:
            # Matikan dulu mouse tracking, kalau tidak tap di shell biasa
            # tidak lagi membuka keyboard / membuka URL.
            seq += MOUSE_OFF
        seq += b"\033[?25h\033[?1049l\033[0m"
        try:
            os.write(sys.stdout.fileno(), seq)
        except OSError:
            pass
        if self.saved is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.saved)
            except Exception:
                pass

    @staticmethod
    def _center(text, cols):
        """Pusatkan teks di lebar `cols`, dipotong kalau kelewat panjang."""
        if len(text) > cols:
            text = text[:max(0, cols - 1)] + "…"
        pad = max(0, (cols - len(text)) // 2)
        return " " * pad + text

    @staticmethod
    def _cells(cells, kind="mid"):
        """Render satu baris tombol.

        `cells` = [(label, lebar), ...]. Tombol dipisah 1 spasi.
        `kind` = "top" (garis atas), "mid" (isi), "bot" (garis bawah).

        Return (teks, [(offset_awal, offset_akhir), ...]) dengan offset
        relatif terhadap awal baris.
        """
        gap = 1
        parts, spans, x = [], [], 0
        for label, w in cells:
            inner = w - 2
            if kind == "top":
                body = "╭" + "─" * inner + "╮"
            elif kind == "bot":
                body = "╰" + "─" * inner + "╯"
            else:
                body = "│" + label.center(inner) + "│"
            parts.append(body)
            spans.append((x, x + w - 1))
            x += w + gap
        return " ".join(parts), spans

    @staticmethod
    def _cells_blank(cells):
        """Baris kosong di dalam kotak tombol (untuk menambah tinggi tombol)."""
        return " ".join("│" + " " * (w - 2) + "│" for _label, w in cells)

    def _keypad_rows(self, cols, lines, mode="pin"):
        """(baris, warna, tombol_per_baris) untuk keypad, atau None kalau tidak muat.

        Lebar tombol dihitung dari lebar layar, bukan angka tetap, jadi keypad
        ikut melebar di layar lebar (landscape/tablet) dan tetap muat di layar
        sempit. Setiap entri `tombol_per_baris` adalah daftar
        (offset_kolom_awal, offset_kolom_akhir, karakter) untuk baris itu,
        offset relatif terhadap awal baris (belum ditambah posisi tengah).
        """
        if not self.keypad_on or cols < KEYPAD_MIN_COLS or lines < KEYPAD_MIN_ROWS:
            return None

        # Susun grup tombol. Tiap grup = (daftar kunci, pakai warna accent).
        groups = [(list(keys), False) for keys in KEYPAD_ROWS]
        groups.append(([KEYPAD_ENTER], True))

        # Mode "setup" dipakai installer saat mengatur PIN untuk pertama kali:
        # di situ belum ada sesi yang bisa ditutup, jadi tombol R/Q tidak
        # boleh muncul — hanya angka + ENTER.
        if self.conf.get("keypad_close", "1") != "0" and mode != "setup":
            # Tombol fungsi menyesuaikan mode, supaya label dan fungsinya
            # selalu sama — tidak ada tombol yang tertulis tapi tak berguna.
            #
            #   pin    : R lupa (selalu ada walau kode pemulihan belum
            #            diset) + Q tutup
            #   recovery: Q tutup dipetakan ke Ctrl-Q (\x11), karena huruf
            #            q harus tetap bebas jadi KARAKTER kode pemulihan
            #   newpin/confirmpin: Q tutup saja (R sudah tidak relevan)
            if mode == "recovery":
                groups.append((["\x11"], False))
            elif mode == "pin":
                groups.append((["r", "q"], False))
            else:
                groups.append((["q"], False))

        # Lebar tombol memakai hampir seluruh lebar layar: margin kiri/kanan
        # cuma 1 kolom. Tombol jadi jauh lebih besar dari versi sebelumnya.
        avail = cols - 2
        cell_w = max(3, min(16, (avail - 2) // 3))
        full_w = 3 * cell_w + 2

        # Tinggi tombol memakai ruang vertikal yang tersisa. Layar HP portrait
        # sempit tapi tinggi, jadi sisa ruang dipakai untuk menambah tinggi
        # tombol (bukan menambah jumlah tombol).
        n_groups = len(groups)
        # room = baris yang boleh dipakai keypad (dikurangi konten teks di
        # atasnya + 1 baris pemisah). Konten teks biasanya 11-13 baris.
        room = lines - KEYPAD_TEXT_ROWS - 1
        per_group = room // n_groups if n_groups else 0
        cell_h = max(KEYPAD_H_MIN,
                     min(KEYPAD_H_MAX, per_group - 2))

        rows, colors, hits = [], [], []

        for keys, is_accent in groups:
            if len(keys) == 3:
                widths = [cell_w] * 3
            elif len(keys) == 1:
                widths = [full_w]          # ENTER selebar baris keypad
            else:
                w = (full_w - 1) // 2      # baris fungsi: 2 tombol
                widths = [w, full_w - 1 - w]

            row_w = sum(widths) + len(widths) - 1
            # draw() memusatkan tiap baris dengan Screen._center(), jadi
            # offset kolom harus dihitung dengan rumus yang sama persis.
            left = (cols - row_w) // 2

            cells = [(KEYPAD_LABELS[k], w) for k, w in zip(keys, widths)]
            _top, spans = self._cells(cells, "top")

            base = len(rows)                 # baris "top" untuk grup ini
            rows.append(_top)
            colors.append(self.c_dim)

            # Baris "isi": label di tengah vertikal, sisanya baris kosong
            # di dalam kotak. Label selalu tepat di tengah agar tombol yang
            # tinggi tetap seimbang dilihat.
            vpos = (cell_h - 1) // 2
            for h in range(cell_h):
                if h == vpos:
                    _mid, _ = self._cells(cells, "mid")
                    rows.append(_mid)
                    colors.append(self.c_acc if is_accent else self.c_fg)
                else:
                    rows.append(self._cells_blank(cells))
                    colors.append(self.c_acc if is_accent else self.c_fg)

            _bot, _ = self._cells(cells, "bot")
            rows.append(_bot)
            colors.append(self.c_dim)

            # Semua baris tombol bisa ditekan supaya area sentuhnya besar.
            # Baris relatif terhadap awal keypad (cell_h + 2 baris per grup).
            for i in range(cell_h + 2):
                for (a, b), k in zip(spans, keys):
                    hits.append((base + i, left + a + 1, left + b, k))

        return rows, colors, hits

    def hit_test(self, col, row):
        """Karakter untuk tap pada (col,row), atau None kalau bukan tombol."""
        for r, c1, c2, key in self.keypad_map:
            if r == row and c1 <= col <= c2:
                return key
        return None

    def draw(self, st):
        cols, lines = term_size()
        now = time.localtime()

        content = [(self.conf.get("message", "TERMUX LOCK"), self.c_acc), ("", self.c_fg)]
        if self.conf.get("show_clock", "1") == "1":
            content.append((time.strftime("%H : %M : %S", now), self.c_fg))
        if self.conf.get("show_date", "1") == "1":
            content.append((tanggal_id(now), self.c_dim))
        content.append(("", self.c_fg))
        content.append((st.get("status", ""), self.c_dim))
        content.append(("", self.c_fg))

        pin = st.get("pin") or ""
        if pin:
            content.append(("*" * len(pin), self.c_acc))
            content.append(("", self.c_fg))
        if st.get("error"):
            content.append((st["error"], self.c_err))
            content.append(("", self.c_fg))

        mode = st.get("mode", "pin")
        keyname = "Ctrl-Q" if mode == "recovery" else "q"

        if mode == "recovery":
            if self.rec_set:
                content.append(("[ Ketik kode pemulihan lalu Enter ]", self.c_fg))
                if st.get("show_switch"):
                    content.append(("", self.c_fg))
                    content.append(("Cara darurat (lupa PIN & kode):", self.c_err))
                    content.append(("edit file ini:", self.c_fg))
                    content.append(("~/.termux/rout/termux-lock-status.txt", self.c_fg))
                    content.append(("ubah isinya jadi 0", self.c_fg))
                    content.append(("lalu buka Termux lagi", self.c_fg))
                    content.append(("", self.c_fg))
            else:
                # Belum ada kode pemulihan -> langsung tunjukkan jalan keluar.
                content.append(("[ Belum ada kode pemulihan ]", self.c_err))
                content.append(("", self.c_fg))
                content.append(("Cara keluar:", self.c_fg))
                content.append(("  1) Dari Termux (kalau bisa masuk):", self.c_fg))
                content.append(("     lock-recovery", self.c_acc))
                content.append(("", self.c_fg))
                content.append(("  2) Pulihkan dari backup (kalau lupa PIN):", self.c_fg))
                content.append(("     lock-restore", self.c_acc))
                content.append(("", self.c_fg))
                content.append(("  3) Reset manual (paling akhir):", self.c_fg))
                content.append(("     ubah termux-lock-status.txt", self.c_fg))
                content.append(("     jadi 0, lalu buka Termux lagi", self.c_fg))
        elif mode == "pin":
            if self.keypad_on:
                content.append(("[ Ketik PIN di keypad, lalu ENTER ]", self.c_fg))
            else:
                content.append(("[ Ketik PIN lalu Enter ]", self.c_fg))
            if self.rec_set:
                content.append(("[ r = lupa PIN ]", self.c_dim))
            else:
                content.append(("[ r = lupa PIN (belum ada kode) ]", self.c_dim))
        elif mode == "setup":
            if self.keypad_on:
                content.append(("[ Ketik PIN di keypad, lalu ENTER ]", self.c_fg))
            else:
                content.append(("[ Ketik PIN lalu Enter ]", self.c_fg))

        if mode == "setup":
            # Belum ada sesi yang perlu ditutup, jadi tidak ada tombol Q.
            content.append(("[ Esc = hapus isian / kosong = batal ]", self.c_dim))
            content.append(("[ ENTER = lanjut ]", self.c_dim))
        elif st.get("confirm_close"):
            content.append(("[ Tekan %s lagi = tutup sesi ]" % keyname, self.c_err))
            content.append(("[ Esc = batal ]", self.c_dim))
        else:
            if mode in ("recovery", "newpin", "confirmpin"):
                content.append(("[ Esc = kembali & hapus isian ]", self.c_dim))
            else:
                content.append(("[ Esc = hapus isian ]", self.c_dim))
            content.append(("[ %s = tutup sesi ]" % keyname, self.c_dim))

        keypad = self._keypad_rows(cols, lines, mode)

        if keypad:
            kp_rows, kp_colors, kp_hits = keypad

            # Keypad ditempel ke bawah layar supaya mudah dijangkau jempol,
            # teks (jam, tanggal, status, PIN) mengisi ruang di atasnya.
            kp_last = lines
            kp_first = lines - len(kp_rows) + 1
            room = kp_first - 2           # 1 baris kosong sebelum keypad

            if self.show_kbd_hint:
                hint = ("[ KEYBOARD di bawah = tutup keyboard ]"
                        if cols >= 40 else
                        "[ KEYBOARD = tutup keyboard ]")
                content.append((hint, self.c_dim))

            # Pangkas baris kosong di tengah konten kalau teks tidak muat.
            while len(content) > room:
                idx = next((i for i, (t, _) in enumerate(content)
                            if t == "" and 0 < i < len(content) - 1), None)
                if idx is None:
                    break
                content.pop(idx)

            text_len = len(content)
            start = max(1, (room - text_len) // 2 + 1)

            # Dipisah 1 baris kosong, lalu baris keypad.
            # kp_hits sudah membawa nomor baris sendiri (tombolnya 3 baris),
            # jadi tidak perlu dihitung ulang per baris di sini.
            base = start + text_len + 1
            self.keypad_map = [(base + i, c1, c2, key)
                               for (i, c1, c2, key) in kp_hits]
            content = list(content) + [("", self.c_fg)]
            content.extend(zip(kp_rows, kp_colors))
        else:
            start = max(1, (lines - len(content)) // 2 + 1)

        buf = [bg(self.c_bg)]
        for row in range(1, lines + 1):
            buf.append("\033[%d;1H" % row)
            idx = row - start
            if 0 <= idx < len(content):
                text, color = content[idx]
                buf.append(fg(color) + self._center(text, cols))
            else:
                buf.append(fg(self.c_fg))
            buf.append("\033[K")
        buf.append(RESET)
        os.write(sys.stdout.fileno(), "".join(buf).encode("utf-8"))


def split_input(data):
    """Pecah input mentah jadi (teks, [(kolom,baris), ...] dari tap).

    Event mouse SGR diambil keluar; escape lain & penanda paste dibuang supaya
    tidak ada karakteraneh yang ikut masuk ke PIN. Sisa escape yang belum
    lengkap disimpan supaya bisa disambung di pembacaan berikutnya.
    """
    taps = []

    def grab(m):
        # 'M' = tombol ditekan (tap), 'm' = dilepas. Yang dipakai hanya 'M'.
        if m.group(4) == b"M":
            taps.append((int(m.group(2)), int(m.group(3))))
        return b""

    text = MOUSE_RE.sub(grab, data)
    text = PASTE_RE.sub(b"", text)
    text = ESC_RE.sub(b"", text)
    return text, taps


def _is_incomplete_escape(chunk):
    """True kalau `chunk` adalah awal escape yang belum selesai.

    Hanya prefix yang jelas-jelas milik escape panjang yang ditahan. ESC
    sendirian (tekan tombol Esc) dianggap escape lengkap supaya tidak
    menggantung menanti byte berikutnya.
    """
    if chunk in (b"\x1b", b""):
        return False
    if ESC_RE.match(chunk):
        return False
    # ESC + satu karakter biasa = Alt-key (mis. Alt-x), sudah lengkap.
    if len(chunk) == 2 and chunk[1] not in b"[]O":
        return False
    # ESC [ ... / ESC O ... / ESC ] ... -> belum ketemu byte penutup
    return len(chunk) <= 24


# Sisa escape dari pembacaan sebelumnya (sequence bisa terpotong antar-read).
_input_tail = b""


def read_input(timeout):
    """Baca satu paket input. Return (teks, taps) atau (None, []) kalau idle."""
    global _input_tail
    try:
        ready, _, _ = select.select([sys.stdin.fileno()], [], [], timeout)
    except (OSError, ValueError):
        return None, []
    if not ready:
        return None, []

    try:
        chunk = os.read(sys.stdin.fileno(), 4096)
    except OSError:
        return b"", []
    if not chunk:
        return b"", []

    # Sisa dari pembacaan sebelumnya disambung DI AWAL chunk baru, bukan
    # menggantikannya. Kalau diganti, byte sisanya tidak akan pernah terbaca.
    data = _input_tail + chunk
    _input_tail = b""

    text, taps = split_input(data)

    # Escape yang belum lengkap harus ditahan supaya bisa disambung di
    # pembacaan berikutnya. Yang ditahan hanya ekor yang PREFIX dari escape
    # yang dikenal (ESC [ ... / ESC O ... / ESC ] ...). ESC sendirian bukan
    # prefix — itu tombol Esc, harus langsung diteruskan.
    cut = text.rfind(b"\x1b")
    if cut >= 0 and _is_incomplete_escape(text[cut:]):
        _input_tail = text[cut:]
        text = text[:cut]

    return text.decode("utf-8", "ignore"), taps


SWITCH_NAME = "termux-lock-status.txt"
SWITCH_PATH = os.path.join(ROUT_DIR, SWITCH_NAME)
OFF_VALUES = ("0", "off", "no", "mati", "false", "n", "disable", "disabled")


def ensure_switch_file():
    """Buat file sakelar (isi '1' = lock aktif) kalau belum ada.
    Letaknya di folder privat ~/.termux/rout supaya aplikasi lain tidak bisa
    mengaksesnya. Isi file yang sudah ada TIDAK diubah."""
    if not os.path.exists(SWITCH_PATH):
        try:
            with open(SWITCH_PATH, "w", encoding="utf-8") as f:
                f.write("1\n")
            os.chmod(SWITCH_PATH, 0o600)
        except OSError:
            pass
    return SWITCH_PATH


def switch_is_off():
    """True kalau file sakelar berisi nilai mati (0/off/...)."""
    try:
        with open(SWITCH_PATH, "r", encoding="utf-8") as f:
            v = f.read().strip()
    except OSError:
        return False
    return v.lower() in OFF_VALUES


def cmd_run():
    conf = parse_conf()
    if conf.get("enabled", "1") != "1":
        return 0
    if not (os.isatty(0) and os.isatty(1)):
        return 0
    if already_locked():
        return 0

    # sakelar darurat: file di ~/.termux/rout (privat). Isi 0 = lock dilewati.
    # Sengaja TANPA menulis pesan: mencetak ke layar saat .zshrc berjalan membuat
    # prompt powerlevel10k tidak tampil sampai Enter ditekan.
    if conf.get("panic_file", "1") == "1" and switch_is_off():
        return 0

    pin_set = bool((conf.get("pin_hash") or "").strip())
    allow_pin = conf.get("allow_pin", "1") == "1"

    # PIN adalah satu-satunya jalan buka. Kalau belum ada / dilarang,
    # jangan pernah mengunci supaya tidak terkunci permanen.
    if not pin_set or not allow_pin:
        return 0

    rec_set = bool((conf.get("recovery_hash") or "").strip())
    min_len = int(conf.get("pin_min_len") or 4)

    def on_term(_signum, _frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGHUP, on_term)

    screen = Screen(conf)
    # alur: pin --(r)--> recovery --> newpin --> confirmpin --> Terbuka
    state = {"mode": "pin", "status": "Masukkan PIN", "pin": "", "error": "",
             "confirm_close": False, "show_switch": False}
    switch_on = conf.get("panic_file", "1") == "1"
    attempts = 0
    rec_attempts = 0
    new_pin = ""
    unlocked = False

    def backoff(n):
        if n < 3:
            return
        delay = min(2 ** (n - 2), 30)
        end = time.time() + delay
        while time.time() < end:
            state["error"] = "Tunggu %d detik..." % int(end - time.time() + 1)
            screen.draw(state)
            time.sleep(1)

    screen.enter()
    try:
        while not unlocked:
            screen.draw(state)
            text, taps = read_input(0.4)

            if text is None:
                continue

            # Tap pada keypad behaving seperti mengetik karakter biasa, jadi
            # tombol-tombol di layar kunci tetap bisa dipakai tanpa keyboard.
            if taps:
                typed = []
                for col, row in taps:
                    key = screen.hit_test(col, row)
                    if key:
                        typed.append(key)
                if typed:
                    text = "".join(typed) + text

            # Escape & Ctrl-C ditangani per-karakter di bawah, jadi tidak lagi
            # dibuang di depan. Sebelumnya `text.startswith("\x1b")`
            # membuat ESC dari keypad ikut terbuang.
            for ch in text:
                mode = state["mode"]

                # Esc / Ctrl-C = batal konfirmasi, kembali ke layar PIN,
                # dan HAPUS isian yang sudah diketik.
                if ch in ("\x1b", "\x03"):
                    state["confirm_close"] = False
                    state["show_switch"] = False
                    rec_attempts = 0
                    if mode != "pin":
                        state["mode"] = "pin"
                        state["error"] = ""
                        state["status"] = "Masukkan PIN"
                    state["pin"] = ""
                    continue

                # tombol tutup sesi: q (untuk PIN/PIN baru) atau Ctrl-Q.
                # Di mode pemulihan dipakai Ctrl-Q supaya huruf q tetap bebas
                # dipakai sebagai karakter kode pemulihan.
                # Batas "pin masih kosong" sengaja TIDAK dipakai: q harus
                # tetap bisa menutup sesi walau PIN sudah terisi sebagian.
                close_key = (ch == "\x11") or \
                            (ch in ("q", "Q") and mode != "recovery")
                if close_key:
                    if state["confirm_close"]:
                        return 3        # .zshrc akan menjalankan exit
                    keyname = "Ctrl-Q" if mode == "recovery" else "q"
                    state["confirm_close"] = True
                    state["error"] = ""
                    state["status"] = "Tekan %s lagi untuk menutup sesi" % keyname
                    continue

                state["confirm_close"] = False

                # Masuk mode pemulihan lewat tombol "R lupa".
                # Batas "PIN masih kosong" sengaja TIDAK dipakai: tombolnya
                # harus tetap berfungsi walau PIN sudah terisi sebagian.
                # Di mode pemulihan huruf r tetap bebas jadi karakter kode.
                if mode == "pin" and ch in ("r", "R"):
                    state["mode"] = "recovery"
                    state["pin"] = ""
                    state["error"] = ""
                    rec_attempts = 0
                    # Kode pemulihan belum diset -> langsung tampilkan cara
                    # darurat daripada membuat user mengira tidak ada jalan.
                    state["show_switch"] = not rec_set
                    state["status"] = ("Kode pemulihan"
                                       if rec_set else "Belum ada kode pemulihan")
                    continue

                if ch in ("\x7f", "\b"):
                    state["pin"] = state["pin"][:-1]
                    continue

                if ch in ("\r", "\n"):
                    if mode == "pin":
                        if verify_pin(conf, state["pin"]):
                            unlocked = True
                            break
                        attempts += 1
                        state["pin"] = ""
                        state["error"] = "PIN salah (%d)" % attempts
                        state["status"] = "Coba lagi"
                        backoff(attempts)
                    elif mode == "recovery":
                        if not rec_set:
                            # Tidak ada kode pemulihan: jangan pernah
                            # menambah attempts atau memberi harapan palsu.
                            state["pin"] = ""
                            state["error"] = "Kode pemulihan belum diset"
                            state["status"] = "Belum ada kode pemulihan"
                            state["show_switch"] = True
                        elif verify_recovery(conf, state["pin"]):
                            attempts = 0
                            rec_attempts = 0
                            state["show_switch"] = False
                            state["mode"] = "newpin"
                            state["pin"] = ""
                            state["error"] = "Kode benar. Buat PIN baru."
                            state["status"] = "PIN baru"
                        else:
                            rec_attempts += 1
                            state["pin"] = ""
                            state["error"] = "Kode salah (%d)" % rec_attempts
                            state["status"] = "Coba lagi"
                            if rec_attempts >= 3 and switch_on:
                                state["show_switch"] = True
                            backoff(rec_attempts)
                    elif mode == "newpin":
                        if not state["pin"].isdigit():
                            state["error"] = "PIN harus angka"
                        elif len(state["pin"]) < min_len:
                            state["error"] = "PIN minimal %d angka" % min_len
                        else:
                            new_pin = state["pin"]
                            state["mode"] = "confirmpin"
                            state["pin"] = ""
                            state["error"] = ""
                            state["status"] = "Ulangi PIN baru"
                    elif mode == "confirmpin":
                        if state["pin"] == new_pin:
                            salt = gen_salt()
                            update_conf({"pin_salt": salt,
                                         "pin_hash": hash_pin(new_pin, salt)})
                            unlocked = True
                            break
                        new_pin = ""
                        state["mode"] = "newpin"
                        state["pin"] = ""
                        state["error"] = "Tidak sama. Ulangi."
                        state["status"] = "PIN baru"
                    continue

                # karakter biasa
                if mode == "recovery":
                    if ch.isprintable() and not ch.isspace() and len(state["pin"]) < 64:
                        state["pin"] += ch
                        state["error"] = ""
                elif mode in ("pin", "newpin", "confirmpin"):
                    if ch.isdigit() and len(state["pin"]) < 64:
                        state["pin"] += ch
                        state["error"] = ""
            if unlocked:
                break

        state["mode"] = "pin"
        state["status"] = "Terbuka"
        state["error"] = ""
        state["pin"] = ""
        screen.draw(state)
        time.sleep(0.25)
        mark_locked()
    finally:
        screen.leave()
    return 0


# --------------------------------------------------------------------------- #
# set-pin / status / enable
# --------------------------------------------------------------------------- #
def prompt_hidden(prompt):
    try:
        import getpass
        return getpass.getpass(prompt)
    except Exception:
        return ""


def prompt_yes_no(question, default=True, tries=5):
    """Tanya ya/tidak. Mengulang sampai dapat jawaban yang dimengerti.

    Menjawab kosong memakai `default`. Selain y/t, menerima juga
    "ya"/"iya"/"nggak"/"tidak" supaya tidak perluebak dulu.
    """
    suffix = "[Y/n]" if default else "[y/N]"
    yes = {"y", "ya", "iya", "yes", "iyaa", "ok"}
    no  = {"n", "t", "tak", "tidak", "nggak", "enggak", "no"}

    for _ in range(tries):
        try:
            ans = input("  %s %s " % (question, suffix)).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("")
            return default
        if not ans:
            return default
        if ans in yes:
            return True
        if ans in no:
            return False
        print("  Jawab dengan y atau n.")

    print("  Input tidak valid — memakai jawaban default.")
    return default


def cmd_set_pin():
    conf = parse_conf()
    print("")
    print("  === Ganti PIN ROUT ===  ")
    pin_set = bool((conf.get("pin_hash") or "").strip())

    if pin_set:
        old = prompt_hidden("  PIN lama    : ")
        if not verify_pin(conf, old):
            print("  PIN lama salah. Dibatalkan.")
            return 1

    try:
        new1 = prompt_hidden("  PIN baru    : ")
        new2 = prompt_hidden("  Ulangi PIN  : ")
    except Exception:
        print("  Dibatalkan.")
        return 1

    if not new1.isdigit():
        print("  PIN harus angka.")
        return 1
    min_len = int(conf.get("pin_min_len") or 4)
    if len(new1) < min_len:
        print("  PIN minimal %d angka." % min_len)
        return 1
    if new1 != new2:
        print("  Tidak sama. Dibatalkan.")
        return 1

    salt = gen_salt()
    updates = {"pin_salt": salt, "pin_hash": hash_pin(new1, salt)}

    print("")
    print("  --- Kode pemulihan ---")
    print("  Dipakai kalau lupa PIN: tekan 'r' di layar kunci lalu masukkan kode.")

    if prompt_yes_no("Buat kode pemulihan sekarang?", default=True):
        rc1 = prompt_hidden("  Kode pemulihan : ")
        rc2 = prompt_hidden("  Ulangi kode    : ")

        if len(rc1) < 4:
            print("  Kode minimal 4 karakter. Kode pemulihan tidak diubah.")
        elif rc1 != rc2:
            print("  Tidak sama. Kode pemulihan tidak diubah.")
        else:
            rsalt = gen_salt()
            updates["recovery_salt"] = rsalt
            updates["recovery_hash"] = hash_pin(rc1, rsalt)
            print("  Kode pemulihan disimpan.")
    else:
        if (conf.get("recovery_hash") or "").strip():
            print("  Kode pemulihan lama DIPERTAHANKAN.")
        else:
            print("  Dilewati. Lupa PIN = reset manual lewat")
            print("  ~/.termux/rout/termux-lock-status.txt (isi 0), lalu lock-pin.")

    update_conf(updates)
    print("  PIN berhasil disimpan.")
    return 0


def cmd_set_recovery():
    conf = parse_conf()
    print("")
    print("  === Atur Kode Pemulihan ===  ")
    if not (conf.get("pin_hash") or "").strip():
        print("  Belum ada PIN. Jalankan lock-pin dulu.")
        return 1
    pin = prompt_hidden("  PIN sekarang   : ")
    if not verify_pin(conf, pin):
        print("  PIN salah. Dibatalkan.")
        return 1
    print("")
    print("  Kode pemulihan dipakai kalau lupa PIN: tekan 'r' di layar kunci.")
    if not prompt_yes_no("Buat kode pemulihan?", default=True):
        print("  Dibatalkan.")
        return 1
    rc1 = prompt_hidden("  Kode pemulihan : ")
    rc2 = prompt_hidden("  Ulangi kode    : ")
    if len(rc1) < 4:
        print("  Kode minimal 4 karakter.")
        return 1
    if rc1 != rc2:
        print("  Tidak sama. Dibatalkan.")
        return 1
    rsalt = gen_salt()
    update_conf({"recovery_salt": rsalt, "recovery_hash": hash_pin(rc1, rsalt)})
    print("  Kode pemulihan disimpan.")
    return 0


def cmd_status():
    conf = parse_conf()
    pin_set = bool((conf.get("pin_hash") or "").strip())
    allow_pin = conf.get("allow_pin", "1") == "1"

    if conf.get("enabled", "1") != "1":
        efektif = "nonaktif (enabled=0)"
    elif not pin_set:
        efektif = "DIMATIKAN - PIN belum diset"
    elif not allow_pin:
        efektif = "DIMATIKAN - allow_pin=0"
    else:
        efektif = "aktif (PIN)"

    print("ROUT — status")
    print("  enabled       : %s" % conf.get("enabled"))
    print("  PIN terpasang : %s" % ("ya" if pin_set else "belum"))
    print("  kode pemulihan: %s" % ("ya" if (conf.get("recovery_hash") or "").strip() else "belum"))
    print("  lock sekali   : %s" % conf.get("lock_once_per_open"))
    print("  keypad        : %s" % ("tampil" if conf.get("pin_keypad", "1") != "0"
                                    else "mati"))
    print("  aktif efektif : %s" % efektif)
    print("  config        : %s" % CONF_PATH)
    n_backup = len(list_backups())
    if n_backup:
        print("  backup        : %d file (%s)" % (n_backup, BACKUP_DIR))
    if conf.get("panic_file", "1") == "1":
        ensure_switch_file()
        if switch_is_off():
            print("  sakelar       : NONAKTIF -> lock dilewati (%s)" % SWITCH_PATH)
        else:
            print("  sakelar       : normal (%s)" % SWITCH_PATH)
    else:
        print("  sakelar       : dimatikan (panic_file=0)")
    if not pin_set:
        print("")
        print("  ! PIN belum diset -> lock TIDAK akan muncul.")
        print("    Jalankan: lock-pin")
    return 0


def cmd_backup():
    """Tampilkan daftar backup config yang bisa dipulihkan."""
    print("")
    print("  === Backup config ROUT ===")
    print("  Lokasi: %s" % BACKUP_DIR)
    backups = list_backups()
    if not backups:
        print("  Belum ada backup.")
        print("  Backup dibuat otomatis setiap kali PIN/kode pemulihan diganti.")
        return 0
    for path in backups:
        name = os.path.basename(path)
        conf = parse_conf(path)
        ada_pin = "PIN ya" if (conf.get("pin_hash") or "").strip() else "PIN kosong"
        print("  %-28s %s" % (name, ada_pin))
    print("")
    print("  Pulihkan: lock.sh restore           (backup terbaru)")
    print("            lock.sh restore <nama-file>")
    return 0


def cmd_restore(which=None):
    """Kembalikan config.conf dari backup."""
    backups = list_backups()
    if not backups:
        print("Tidak ada backup di %s" % BACKUP_DIR)
        return 1

    if which:
        cand = [p for p in backups if os.path.basename(p) == which]
        if not cand:
            cand = [p for p in backups if which in os.path.basename(p)]
        if not cand:
            print("Backup tidak ditemukan: %s" % which)
            print("Yang tersedia:")
            for p in backups:
                print("  %s" % os.path.basename(p))
            return 1
        src = cand[0]
    else:
        src = backups[0]

    # backup dulu kondisi sekarang, supaya restore bisa dibatalkan
    cur = backup_conf()
    if cur:
        print("Config sekarang dicadangkan ke %s" % os.path.basename(cur))

    try:
        with open(src, "r", encoding="utf-8") as f:
            data = f.read()
        with open(CONF_PATH, "w", encoding="utf-8") as f:
            f.write(data)
        os.chmod(CONF_PATH, 0o600)
    except OSError as e:
        print("Gagal memulihkan: %s" % e)
        return 1

    conf = parse_conf()
    print("Config dipulihkan dari %s" % os.path.basename(src))
    print("  PIN terpasang : %s" % ("ya" if (conf.get("pin_hash") or "").strip()
                                  else "belum"))
    print("  kode pemulihan: %s" % ("ya" if (conf.get("recovery_hash") or "").strip()
                                     else "belum"))
    if not (conf.get("pin_hash") or "").strip():
        print("")
        print("  PIN di backup ini kosong — lock tidak akan muncul.")
        print("  Jalankan lock-pin untuk membuat PIN baru.")
    return 0


def print_help():
    print("ROUT — Termux Lock Screen")
    print("  lock.sh run        jalankan layar kunci (otomatis dari .zshrc)")
    print("  lock.sh set-pin    ganti / atur PIN")
    print("  lock.sh set-recovery  atur kode pemulihan (untuk lupa PIN)")
    print("  lock.sh status     lihat status")
    print("  lock.sh backup     lihat backup config (untuk memulihkan PIN)")
    print("  lock.sh restore    pulihkan config dari backup terbaru")
    print("  lock.sh enable     aktifkan lock")
    print("  lock.sh disable    nonaktifkan lock")
    print("")
    print("  Sakelar lock: ~/.termux/rout/termux-lock-status.txt")
    print("  Isi 1 = lock aktif, 0 = lock dilewati (untuk reset).")


def main(argv):
    ensure_switch_file()
    cmd = argv[1] if len(argv) > 1 else "run"
    if cmd == "run":
        return cmd_run()
    if cmd in ("set-pin", "setpin", "pin"):
        return cmd_set_pin()
    if cmd in ("set-recovery", "setrec", "recovery"):
        return cmd_set_recovery()
    if cmd == "status":
        return cmd_status()
    if cmd in ("backup", "backups"):
        return cmd_backup()
    if cmd == "restore":
        return cmd_restore(argv[2] if len(argv) > 2 else None)
    if cmd == "enable":
        update_conf({"enabled": "1"})
        print("Lock diaktifkan.")
        return 0
    if cmd == "disable":
        update_conf({"enabled": "0"})
        print("Lock dinonaktifkan.")
        return 0
    if cmd in ("-h", "--help", "help"):
        print_help()
        return 0
    print("Perintah tidak dikenal: %s" % cmd)
    print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
