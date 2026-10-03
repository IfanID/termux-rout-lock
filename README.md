# ROUT — Layar Kunci Termux (PIN)

Layar kunci untuk **Termux** yang dijalankan dari shell: saat kamu membuka
sesi Termux, terminal diblokir penuh dan harus dibuka dengan **PIN**.

Dibuat murni dengan Python + shell, tanpa aplikasi tambahan.

## Fitur

- **Layar kunci penuh** di terminal (pakai alternate screen)
- **PIN** untuk membuka (disimpan sebagai hash SHA-256, bukan teks biasa)
- **Kode pemulihan** — kalau lupa PIN (tekan `r` di layar kunci)
- **Tombol tutup sesi** — batalkan sesi baru tanpa memasukkan PIN (`q` / `Ctrl-Q`)
- **Sakelar darurat** — file `0`/`1` untuk melewati lock dari luar
- **Pemicu pintar**:
  1. Saat sesi baru dibuka (langsung, sebelum prompt)
  2. Saat perintah foreground lama selesai (mis. keluar dari sebuah aplikasi TUI)
  3. Saat terminal diam (idle) sekian detik
- **MOTD (Welcome to Termux) tampil setelah unlock**, bukan sebelumnya
- Jam & tanggal, warna bisa diatur
- Bypass darurat: `TERMUX_NO_LOCK=1 zsh`

## Persyaratan

- **Termux** (Android)
- `python` (`pkg install python`)
- `zsh` (`pkg install zsh`)
- Shell login kamu **zsh** (default Termux)

> Belum terpasang? Installer bisa memasang `python` & `zsh` otomatis — lihat
> [Instalasi](#instalasi).

> Catatan: Termux menyambung kembali sesi lama saat app dibuka ulang, jadi
> pemicu utama lock adalah **diam (idle)** dan **selesainya perintah panjang**,
> bukan sekadar "app dibuka".

## Instalasi

```sh
git clone https://github.com/IfanID/termux-rout-lock.git
cd termux-rout-lock
bash install.sh
```

Installer berjalan dengan **UI layar penuh** di dalam terminal: judul dan daftar
langkah tampil satu per satu (`○ → … → ✓`), lalu ditutup ringkasan.

Kalau `python`/`zsh` belum terpasang, installer lebih dulu menampilkan layar
tanya:

- **Y** → memasang otomatis (`pkg install python zsh`), lalu lanjut memasang ROUT
- **N** → dibatalkan; pasang manual dulu, lalu jalankan ulang `bash install.sh`

Langkah yang dijalankan installer:
1. Menyalin `lock.py`, `lock.sh` & `uninstall.sh` ke `~/.termux/rout`
2. Membuat `config.conf` dari `config.default.conf` (kalau belum ada)
3. Memasang hook ke `~/.zshrc` (dengan backup dulu, tidak dobel)
4. Mematikan `POWERLEVEL9K_INSTANT_PROMPT` — agar layar kunci muncul **sebelum** prompt
5. Membuat `~/.hushlogin` — agar MOTD tidak muncul **sebelum** layar kunci
6. Menawarkan langsung mengatur PIN

> Installer ditulis dengan Python (`install-ui.py`) dan dipanggil oleh
> `install.sh`. Kalau dijalankan tanpa terminal (mis. di-pipe), installer
> otomatis memakai mode teks biasa.

Urutan saat membuka Termux:

```
Buka Termux → LAYAR KUNCI → masukkan PIN → MOTD → prompt zsh
```

Opsi:

```sh
bash install.sh --dest ~/.termux/rout   # ganti folder tujuan
bash install.sh --no-hook               # jangan ubah .zshrc
bash install.sh --no-setup              # jangan langsung set PIN
```

Setelah selesai, **buka sesi Termux baru** (geser dari tepi kiri → NEW SESSION).

## Pemakaian

| Aksi | Caranya |
|---|---|
| Buka Termux | ketik PIN lalu Enter |
| Lupa PIN | tekan `r` → masukkan kode pemulihan → buat PIN baru |
| Batal / kembali ke PIN | tekan `Esc` |
| Tutup sesi (layar PIN / PIN baru) | tekan `q` dua kali |
| Tutup sesi (layar kode pemulihan) | tekan `Ctrl-Q` dua kali |

### Perintah

| Perintah | Fungsi |
|---|---|
| `lock-pin` | ganti / atur PIN |
| `lock-recovery` | atur / ganti kode pemulihan |
| `lock-status` | lihat status |
| `lock` | kunci layar sekarang |
| `~/.termux/rout/lock.sh enable` / `disable` | hidupkan / matikan lock |

### Bypass darurat

- Sesi ini saja: jalankan shell dengan `TERMUX_NO_LOCK=1 zsh`
- Saat terkunci: edit `~/.termux/rout/termux-lock-status.txt` → isi `1` (aktif)
  atau `0` (dilewati)

## Konfigurasi

File: `~/.termux/rout/config.conf`

```ini
enabled=1                # 1 = aktif, 0 = mati
allow_pin=1              # 1 = boleh buka dengan PIN
show_clock=1
show_date=1
lock_once_per_open=0     # harus 0 agar idle-lock bisa berulang
message=ROUT
pin_min_len=4
panic_file=1             # sakelar darurat (termux-lock-status.txt)
```

Pengaturan tambahan lewat environment (di `~/.zshrc`):

```sh
export TERMUX_LOCK_IDLE=60       # detik diam di prompt -> kunci
export TERMUX_LOCK_AFTER_CMD=10  # perintah foreground >= N detik -> kunci saat selesai
```

## Sakelar darurat

File `~/.termux/rout/termux-lock-status.txt`:

- `1` → lock aktif (normal)
- `0` → lock **dilewati** (bisa masuk Termux & reset PIN)

Juga menerima `off`, `no`, `mati`, `false`.

Kalau tidak butuh, matikan dengan `panic_file=0`.

## Pencabutan (uninstall)

```sh
bash uninstall.sh              # hapus hook + folder
bash uninstall.sh --keep-files # hapus hook saja
bash uninstall.sh --yes        # tanpa konfirmasi
```

## Struktur

```
termux-rout-lock/
├── install.sh
├── install-ui.py        # installer layar penuh (dipanggil install.sh)
├── uninstall.sh
├── lock.py              # program utama (layar kunci)
├── lock.sh              # launcher
├── config.default.conf    # template konfigurasi
├── README.md
├── LICENSE
└── .gitignore
```

Saat terpasang, `~/.termux/rout/` berisi:
`lock.py`, `lock.sh`, `config.conf`, `uninstall.sh`, dan `termux-lock-status.txt`.

`uninstall.sh` selalu disalin ke folder terpasang — jadi kamu bisa mencabut ROUT
kapan saja tanpa perlu folder repo:

```sh
bash ~/.termux/rout/uninstall.sh
```

## Keamanan

- PIN & kode pemulihan disimpan sebagai **hash SHA-256 + salt**, bukan teks biasa.
- Fail-open: kalau PIN belum diatur, lock **tidak** akan mengunci (mencegah terkunci permanen).
- Sakelar darurat (`termux-lock-status.txt`) sengaja bisa diubah dari luar —
  kalau tidak ingin, set `panic_file=0`.
- `config.conf` **tidak** ikut di-commit (lihat `.gitignore`).

## Lisensi

MIT — lihat [LICENSE](LICENSE).
