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

ROUT_DIR   = os.path.dirname(os.path.realpath(__file__))
CONF_PATH  = os.path.join(ROUT_DIR, "config.conf")
STATE_PATH = os.path.join(ROUT_DIR, "lock.state")

RESET = "\033[0m"

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
    "pin_min_len":         "4",
    "pin_salt":            "",
    "pin_hash":            "",
    "recovery_salt":       "",
    "recovery_hash":       "",
    "panic_file":          "1",
}

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def parse_conf(path=CONF_PATH):
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


def update_conf(updates, path=CONF_PATH):
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

    def enter(self):
        self.saved = termios.tcgetattr(sys.stdin.fileno())
        tty.setraw(sys.stdin.fileno())
        os.write(sys.stdout.fileno(),
                 b"\033[?1049h\033[?25l\033[2J")

    def leave(self):
        try:
            os.write(sys.stdout.fileno(),
                     b"\033[?25h\033[?1049l\033[0m")
        except OSError:
            pass
        if self.saved is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.saved)
            except Exception:
                pass

    @staticmethod
    def _center(text, cols):
        pad = max(0, (cols - len(text)) // 2)
        return " " * pad + text

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
            content.append(("[ Ketik kode pemulihan lalu Enter ]", self.c_fg))
            if st.get("show_switch"):
                content.append(("", self.c_fg))
                content.append(("Cara darurat (lupa PIN & kode):", self.c_err))
                content.append(("edit file ini:", self.c_fg))
                content.append(("~/.termux/rout/termux-lock-status.txt", self.c_fg))
                content.append(("ubah isinya jadi 0", self.c_fg))
                content.append(("lalu buka Termux lagi", self.c_fg))
                content.append(("", self.c_fg))
        elif mode == "pin":
            content.append(("[ Ketik PIN lalu Enter ]", self.c_fg))
            if (self.conf.get("recovery_hash") or "").strip():
                content.append(("[ r = lupa PIN ]", self.c_dim))

        if st.get("confirm_close"):
            content.append(("[ Tekan %s = tutup sesi ]" % keyname, self.c_err))
            content.append(("[ Esc = batal ]", self.c_dim))
        else:
            if mode in ("recovery", "newpin", "confirmpin"):
                content.append(("[ Esc = kembali ke PIN ]", self.c_dim))
            content.append(("[ %s = tutup sesi ]" % keyname, self.c_dim))

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


def read_input(timeout):
    try:
        ready, _, _ = select.select([sys.stdin.fileno()], [], [], timeout)
    except (OSError, ValueError):
        return None
    if not ready:
        return None
    try:
        data = os.read(sys.stdin.fileno(), 4096)
    except OSError:
        return b""
    return data or b""


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
            data = read_input(0.4)

            if data is None:
                continue

            text = data.decode("utf-8", "ignore")

            # Esc (sendirian) = batal konfirmasi / kembali ke layar PIN
            if text == "\x1b":
                state["confirm_close"] = False
                state["show_switch"] = False
                rec_attempts = 0
                if state["mode"] != "pin":
                    state["mode"] = "pin"
                    state["pin"] = ""
                    state["error"] = ""
                    state["status"] = "Masukkan PIN"
                else:
                    state["status"] = "Masukkan PIN"
                continue
            if text.startswith("\x1b"):
                continue

            for ch in text:
                mode = state["mode"]

                # Ctrl-C = batal konfirmasi / kembali ke layar PIN
                if ch == "\x03":
                    state["confirm_close"] = False
                    state["show_switch"] = False
                    rec_attempts = 0
                    if mode != "pin":
                        state["mode"] = "pin"
                        state["pin"] = ""
                        state["error"] = ""
                        state["status"] = "Masukkan PIN"
                    continue

                # tombol tutup sesi: q (untuk PIN/PIN baru) atau Ctrl-Q.
                # Di mode pemulihan dipakai Ctrl-Q supaya huruf q tetap bebas
                # dipakai sebagai karakter kode pemulihan.
                close_key = (ch == "\x11") or \
                            (ch in ("q", "Q") and mode != "recovery" and not state["pin"])
                if close_key:
                    if state["confirm_close"]:
                        return 3        # .zshrc akan menjalankan exit
                    keyname = "Ctrl-Q" if mode == "recovery" else "q"
                    state["confirm_close"] = True
                    state["error"] = ""
                    state["status"] = "Tekan %s lagi untuk menutup sesi" % keyname
                    continue

                state["confirm_close"] = False

                # masuk mode pemulihan: tombol r saat PIN masih kosong
                if mode == "pin" and ch in ("r", "R") and not state["pin"] and rec_set:
                    state["mode"] = "recovery"
                    state["error"] = ""
                    state["status"] = "Kode pemulihan"
                    rec_attempts = 0
                    state["show_switch"] = False
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
                        if verify_recovery(conf, state["pin"]):
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
    print("  --- Kode pemulihan (dipakai kalau lupa PIN) ---")
    try:
        rc1 = prompt_hidden("  Kode pemulihan : ")
        rc2 = prompt_hidden("  Ulangi kode    : ")
    except Exception:
        rc1 = rc2 = ""

    if not rc1:
        print("  Dilewati. Tanpa kode pemulihan, lupa PIN = reset manual.")
    elif len(rc1) < 4:
        print("  Kode minimal 4 karakter. Kode pemulihan tidak diubah.")
    elif rc1 != rc2:
        print("  Tidak sama. Kode pemulihan tidak diubah.")
    else:
        rsalt = gen_salt()
        updates["recovery_salt"] = rsalt
        updates["recovery_hash"] = hash_pin(rc1, rsalt)
        print("  Kode pemulihan disimpan.")

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
    try:
        rc1 = prompt_hidden("  Kode pemulihan : ")
        rc2 = prompt_hidden("  Ulangi kode    : ")
    except Exception:
        print("  Dibatalkan.")
        return 1
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
    print("  aktif efektif : %s" % efektif)
    print("  config        : %s" % CONF_PATH)
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


def print_help():
    print("ROUT — Termux Lock Screen")
    print("  lock.sh run        jalankan layar kunci (otomatis dari .zshrc)")
    print("  lock.sh set-pin    ganti / atur PIN")
    print("  lock.sh set-recovery  atur kode pemulihan (untuk lupa PIN)")
    print("  lock.sh status     lihat status")
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
