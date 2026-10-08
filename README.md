# ROUT — Layar Kunci Termux (PIN)

Layar kunci untuk **Termux** yang dijalankan dari shell: saat kamu membuka
sesi Termux, terminal diblokir penuh dan harus dibuka dengan **PIN**.

Dibuat murni dengan Python + shell, tanpa aplikasi tambahan.

## Fitur

- **Layar kunci penuh** di terminal (pakai alternate screen)
- **Keypad angka di layar** — tombol `0-9`, `ESC`, `DEL`, `ENTER`, `R lupa`, `Q tutup`.
  Bentuknya kotak membulat seperti keypad telepon, tiap tombol setinggi 3 baris
  terminal supayaarea sentuhnya besar. Lebar tombol mengikuti lebar layar, tetap
  muat di layar sempit. Ketik manual juga tetap bisa dipakai.
  Butuh layar **≥ 26 kolom & ≥ 34 baris**; kalau tidak cukup, keypad
  disembunyikan dan layar kembali ke mode ketik manual.
- **PIN** untuk membuka (disimpan sebagai hash SHA-256, bukan teks biasa)
- **Kode pemulihan** — diminta langsung di akhir instalasi (kalau lupa PIN, tekan `R lupa` di layar kunci lalu ketik kodenya)
- **Tombol tutup sesi** — batalkan sesi baru tanpa memasukkan PIN (`q` / `Ctrl-Q`)
- **Sakelar darurat** — file `0`/`1` untuk melewati lock dari luar
- **Pemicu pintar**:
  1. Saat sesi baru dibuka (langsung, sebelum prompt)
  2. Saat perintah foreground lama selesai (mis. keluar dari sebuah aplikasi TUI)
  3. Saat terminal diam (idle) sekian detik
- **MOTD (Welcome to Termux) tampil setelah unlock**, bukan sebelumnya —
  bisa dimatikan dengan `show_motd=0`
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

Semua animasinya bisa dilihat tanpa installing apa pun:

```sh
bash preview.sh                # simulasi langkah instalasi
bash preview.sh --slow 0.4     # lebih cepat
bash preview.sh --pin          # sekaligus-atur PIN + kode pemulihan
bash preview.sh --no-pin       # lewati langkah PIN & pemulihan
bash preview.sh --demo-dep     # fase dependensi (hanya tanya sekali)
bash preview.sh --demo-manual  # jawaban 'N' atau pkg tidak ada
bash preview.sh --demo-asked   # dependensi sudah ditanyakan install.sh
```

`preview.sh` menjalankan **UI installer yang sungguhan** (spinner dan berkasnya
sama persis) tapi mengarahkannya ke folder sementara — `~/.zshrc` dan
`config.conf` asli tidak akan disentuh.

Kalau sedang tidak berada di folder repo, pakai path penuh:

```sh
bash ~/.termux/rout/preview.sh
```

Tanda status: `○` belum · spinner `⠋⠙⠹⠸…` sedang jalan · `✓` selesai ·
`–` dilewati (mis. `config.conf` sudah ada, jadi tidak ditimpa) · `!` gagal.

Kalau `python` atau `zsh` belum terpasang, installer lebih dulu menampilkan
**satu** layar tanya — untuk semua yang kurang sekaligus:

```
  !  Belum terpasang: python zsh

  Pasang otomatis sekarang? [Y/n]:

     [ Y ]   Ya, pasang otomatis
     [ N ]   Tidak, saya pasang manual
```

- **Y** → memasang otomatis dengan satu perintah (`pkg install -y python zsh`),
  lalu instalasi ROUT lanjut
- **N** → dibatalkan; pasang manual dulu, lalu jalankan ulang `bash install.sh`

Pertanyaan ini **tanya sekali saja**, bahkan kalauemasanya tidak berhasil —
`install.sh` menandai dirinya sudah pernah bertanya lewat `ROUT_DEPS_ASKED`,
lalu `install-ui.py` langsung memberi tahu hasilanych tanpa menanyakan lagi.
Kalau `pkg` gagal, yang muncul adalah `[ R ] Coba lagi`, bukan pertanyaan baru.

Langkah yang dijalankan installer:
1. Menyalin `lock.py`, `lock.sh` & `uninstall.sh` ke `~/.termux/rout`
2. Membuat `config.conf` dari `config.default.conf` (kalau belum ada)
3. Memasang hook ke `~/.zshrc` (dengan backup dulu, tidak dobel)
4. Mematikan `POWERLEVEL9K_INSTANT_PROMPT` — agar layar kunci muncul **sebelum** prompt
5. Membuat `~/.hushlogin` — agar MOTD tidak muncul **sebelum** layar kunci
6. **Set PIN** — layar penuh dengan keypad yang sama seperti layar kunci
7. **Kode pemulihan** — diminta langsung (tanpa pertanyaan Y/N)

### Set PIN & kode pemulihan langsung di installer

Langkah 6–7 berjalan **di dalam UI installer**, jadi setelah instalasi selesai
tidak perlu mengetik `lock-pin` atau `lock-recovery` sama sekali.

- **PIN** diketik di keypad layar penuh (angka + `ENTER`), persis seperti layar
  kunci. Minta PIN lalu ulanginya; kalau tidak sama, otomatis diminta lagi.
  `ESC` menghapus isian — kalau isian sudah kosong, `ESC` berarti **batal**
  (langkah ditandai `–`, instalasi tetap selesai, lock belum aktif).
- **Kode pemulihan** diminta **langsung**, tanpa pertanyaan Y/N — supaya
  selalu ada jalan keluar kalau lupa PIN. Kode boleh huruf dan angka, jadi
  diketik lewat keyboard (minimal 4 karakter, lalu diulang sekali). `ESC`
  berarti **lewati** (ditandai `–`). Kalau tidak sama atau terlalu pendek,
  kode **tidak** diubah dan langkah ditandai `–` — kode lama (kalau ada)
  tetap utuh.
- Kode hanya ditampilkan **sekali**, di layar `✓ Kode pemulihan disimpan`.
  Setelah itu yang tersimpan hanya hash-nya, jadi tidak bisa dilihat lagi.

Ringkasan di akhir selalu menyebut status lock dan kode pemulihan:

```
  ✓  Semua selesai

  Terpasang di : ~/.termux/rout
  Lock         : aktif (PIN)
  Pemulihan   : aktif (kode ada)
```

Kalau PIN belum di-set, barisnya jadi `Lock : belum diset` dan
`Pemulihan : —` — jadi jelas dari mana harus melanjutkan.

**Enter cukup ditekan sekali**, di layar ringkasan terakhir. Semua layar di
tengah instalasi berjalan sendiri:

| Layar | Menunggu |
|---|---|
| Pesan kesalahan (kode tidak sama / terlalu pendek) | 3 detik |
| Dependency gagal / sudah pernah ditanyakan | 5 detik |
| **Kode tersimpan** (`✓ Kode pemulihan disimpan`) | hitung mundur **5 detik** |
| **Ringkasan akhir** | **menunggu Enter** |

Tekan Enter lebih awal kalau tidak mau menunggu. Kalau kodenya belum sempat
dicatat, atur ulang saja: `lock-recovery` (PIN yang sedang dipakai akan
ditanyakan lebih dulu).

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
bash install.sh --zshrc ~/.zshrc        # zshrc yang dituju (default: $ZDOTDIR/.zshrc)
bash install.sh --no-hook               # jangan ubah .zshrc
bash install.sh --no-setup              # jangan langsung set PIN
```

Setelah selesai, **buka sesi Termux baru** (geser dari tepi kiri → NEW SESSION).

## Pemakaian

| Aksi | Caranya |
|---|---|
| Buka Termux | tekan angka di keypad lalu `ENTER`, atau ketik PIN manual |
| Lupa PIN | tekan `r` (atau tombol `R lupa`) → masukkan kode pemulihan → buat PIN baru |
| Batal / hapus isian | tekan `Esc` atau `Ctrl-C` (atau tombol `ESC`) — isian PIN dikosongkan |
| Hapus digit terakhir | tekan `DEL` di keypad (atau `Backspace`) |
| Tutup sesi (layar PIN / PIN baru) | tekan `q` dua kali (tombol `Q tutup`) |
| Tutup sesi (layar kode pemulihan) | tekan `Ctrl-Q` dua kali |

Tombol `Q`, `R`, dan `ESC` **tetap jalan walau PIN sudah terisi sebagian** —
tidak perlu dihapus dulu.

### Menyembunyikan keyboard HP

Termux tidak menyediakan cara menyembunyikan soft keyboard **dari dalam shell**,
jadi keyboard Android tetap tampil di bawah keypad. Ada dua cara agar layar
kunci bersih:

1. **`hide-soft-keyboard-on-startup = true`** di `~/.termux/termux.properties`
   (sudah diaktifkan di instalasi ini). Keyboard tidak muncul saat Termux
   dibuka — persis saat layar kunci tampil. Setelah terbuka, tampilkan
   keyboard dengan tombol **`KEYBOARD`** di baris extra-keys.
2. **Ketuk layar** — tap di area mana pun akan memunculkan keyboard lagi
   (kecuali sudah ada mouse tracking aktif).

Kalau `hide-soft-keyboard-on-startup` tidak overruled oleh setting Termux
("Show soft keyboard on start" di Settings → Terminal), tap tombol `KEYBOARD`.

### Perintah

| Perintah | Fungsi |
|---|---|
| `lock-pin` | ganti / atur PIN (menanyakan mau buat kode pemulihan atau tidak) |
| `lock-recovery` | atur / ganti kode pemulihan |
| `lock-status` | lihat status |
| `lock-backup` | lihat backup config yang tersedia |
| `lock-restore` | pulihkan config dari backup terbaru |
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
show_motd=1              # 0 = jangan tampilkan banner "Welcome to Termux!"
lock_once_per_open=0     # harus 0 agar idle-lock bisa berulang
message=ROUT
pin_min_len=4
panic_file=1             # sakelar darurat (termux-lock-status.txt)
pin_keypad=1             # keypad angka di layar kunci
keypad_close=1           # tombol "R lupa" & "Q tutup"
keypad_kbd_hint=1         # petunjuk cara tutup keyboard HP
color_key=40,40,58       # warna latar tombol keypad
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

## Kode pemulihan

`lock-pin` selalu menanyakan dulu:

```
  --- Kode pemulihan ---
  Dipakai kalau lupa PIN: tekan 'r' di layar kunci lalu masukkan kode.
  Buat kode pemulihan sekarang? [Y/n]
```

- **`Y` / Enter** → buat kode pemulihan (minimal 4 karakter, isi 2×).
- **`n`** → lewati. Kalau sebelumnya sudah ada kodenya, kode **lama
  dipertahankan** — tidak dihapus diam-diam.
- Kalau menjawab **`n`** padahal belum pernah ada kodenya, muncul petunjuk
  cara reset manual lewat `~/.termux/rout/termux-lock-status.txt` (isi `0`).
- Jawaban ngawur akan ditanyakan ulang, bukan dianggap `n`.
- PIN tetap tersimpan walau kode pemulihan gagal dibuat (terlalu pendek
  atau tidak sama) — kode yang lama tidak ikut tertimpa.

Tombol **R lupa** selalu ada di keypad, walau kode pemulihan belum diset. Kalau
kodenya belum ada, menekan R langsung menampilkan tiga cara keluar:

1. `lock-recovery` — kalau kamu masih bisa masuk Termux
2. `lock-restore` — memulihkan PIN dari `~/.termux/rout/backup/`
3. ubah `termux-lock-status.txt` jadi `0` — reset manual, paling akhir

## Backup config (lokal)

`config.conf` **tidak** masuk git — isinya hash PIN & kode pemulihan. Supaya
hash yang hilang tidak jadi permanen, setiap kali PIN atau kode pemulihan
diubah, config yang lama otomatis disalin ke `~/.termux/rout/backup/` dulu:

```sh
lock-backup              # lihat daftar backup
lock-restore             # pulihkan dari backup terbaru
lock-restore config-20261007-143022-123456.conf   # pulihkan yang tertentu
```

- Disimpan 5 backup terakhir, sisanya dihapus otomatis.
- Folder `backup/` berizin `0700`, tiap file `0600`.
- Backup **lokal saja** — tidak dikirim ke mana pun, dan tidak masuk git
  (ada di `.gitignore`).
- Backup hanya dibuat kalau `pin_hash`/`recovery_hash` terisi, jadi foldernya
  tidak berisi sampah.
- `lock-restore` mencadangkan kondisi sekarang dulu sebelum menimpa, jadi
  restore bisa dibatalkan.
- Uninstall menghapus folder `backup/` ikut — karena berisi hash PIN.

## Keamanan

- PIN & kode pemulihan disimpan sebagai **hash SHA-256 + salt**, bukan teks biasa.
- Fail-open: kalau PIN belum diatur, lock **tidak** akan mengunci (mencegah terkunci permanen).
- Sakelar darurat (`termux-lock-status.txt`) sengaja bisa diubah dari luar —
  kalau tidak ingin, set `panic_file=0`.
- `config.conf` **tidak** ikut di-commit (lihat `.gitignore`).

## Lisensi

MIT — lihat [LICENSE](LICENSE).
