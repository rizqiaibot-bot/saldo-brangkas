# Laporan Migrasi — Saldo Brangkas (Supabase → VPS Biznet)

- Tanggal: 2026-09-13
- Sumber: Supabase project `dsryxvelpbuitjmnswxc` (schema `public`)
- Tujuan: PostgreSQL 14 VPS Biznet `103.127.96.23`, database `saldo_brangkas`
- Status: **SELESAI & TERUJI** (website aktif, data production berhasil dimigrasikan)
- Project Juwita One **tidak disentuh** (DB `juwita_one` dan folder/domain-nya tetap utuh)

---

## 1. Ringkasan

Aplikasi Saldo Brangkas yang semula bergantung pada Supabase (PostgREST + GoTrue Auth)
dipindahkan sepenuhnya ke VPS Biznet:

- Database production (5 tabel, 1.023 baris) berhasil dibackup dan direstore ke `saldo_brangkas`.
- PostgREST & Supabase Auth digantikan **backend API kustom** (Python) + PostgreSQL biasa.
- Login email/password tetap berjalan dengan **hash bcrypt asli** dari `auth.users`
  (password lama pengguna tetap berlaku; tidak ada password plaintext).
- Whitelist user tetap ditegakkan lewat tabel `brangkas_users`.
- Nginx memakai server block terpisah; konfigurasi `juwita.biz.id` tidak diubah.

## 2. Audit database Supabase (aktual)

Diakses read-only via Supabase Management API (hanya perintah `SELECT`; tidak ada
`INSERT/UPDATE/DELETE` ke production Supabase).

### 2.1 Tabel & jumlah data

| Tabel | Baris | PK | Catatan |
|---|---|---|---|
| `public.brangkas_users` | 1 | `user_id` (FK ke `auth.users`) | whitelist user |
| `public.brangkas_setting` | 1 | `id` | `id=1`, saldo_awal = 2.077.000 |
| `public.brangkas_transaksi` | 1004 | `id` | rentang 2026-05-18 s/d 2026-09-12 |
| `public.brangkas_modal_laci` | 17 | `tanggal_modal` | |
| `public.brangkas_audit_laci` | 0 | `id` | kosong |

Distribusi transaksi: `KELUAR=710`, `MASUK=287`, `KASBON=7`.

### 2.2 Kolom & tipe

| Tabel | Kolom (tipe / default) |
|---|---|
| `brangkas_users` | `user_id uuid PK`, `created_at timestamptz default now()` |
| `brangkas_setting` | `id int PK`, `saldo_awal numeric default 0`, `updated_at timestamptz default now()`, `catatan text` |
| `brangkas_transaksi` | `id uuid default gen_random_uuid()`, `tanggal date`, `jam text`, `jenis text`, `nominal numeric`, `keterangan text default '-'`, `pengambil text default '-'`, `created_at timestamptz default now()`, `sudah_disalin bool default false` |
| `brangkas_modal_laci` | `tanggal_modal date PK`, `nominal_modal numeric default 500000`, `status text default 'BELUM_DIKEMBALIKAN'`, `closing_at timestamptz`, `returned_at timestamptz`, `created_at timestamptz default now()` |
| `brangkas_audit_laci` | `id uuid default gen_random_uuid()`, `tanggal date default CURRENT_DATE`, `uang_sistem numeric default 0`, `uang_fisik numeric default 0`, `selisih numeric default 0`, `status text`, `keterangan text default '-'`, `created_at timestamptz default now()` |

### 2.3 Constraint / index

- PK: `brangkas_users_pkey(user_id)`, `brangkas_setting_pkey(id)`,
  `brangkas_transaksi_pkey(id)`, `brangkas_modal_laci_pkey(tanggal_modal)`,
  `brangkas_audit_laci_pkey(id)`.
- FK: `brangkas_users.user_id → auth.users(id) ON DELETE CASCADE` (Supabase-only).
- CHECK:
  - `brangkas_transaksi_jenis_check`: jenis ∈ {MASUK, KELUAR, KASBON}
  - `brangkas_modal_laci_status_check`: status ∈ {BELUM_DIKEMBALIKAN, SUDAH_DIKEMBALIKAN}
  - `brangkas_audit_laci_status_check`: status ∈ {COCOK, PLUS, MINUS}
- Index tambahan: hanya index PK (tidak ada index non-PK).
- Trigger: **tidak ada**.
- Extension relevan: `pgcrypto` (untuk `gen_random_uuid()`), `pg_graphql`, `pg_stat_statements`, `supabase_vault`, `plpgsql`.

### 2.4 Function / RPC

| Function | Definisi |
|---|---|
| `public.is_brangkas_user(uid uuid) RETURNS boolean` | `LANGUAGE sql STABLE SECURITY DEFINER SET search_path=''` → `EXISTS(SELECT 1 FROM public.brangkas_users WHERE user_id=uid)` |

### 2.5 RLS & policy (Supabase)

RLS aktif pada kelima tabel. Semua policy `TO authenticated` dengan
`USING/WITH CHECK = public.is_brangkas_user(auth.uid())`:
`brangkas_setting` (SELECT/INSERT/UPDATE), `brangkas_transaksi` (SELECT/INSERT/UPDATE/DELETE),
`brangkas_modal_laci` (INSERT/UPDATE), `brangkas_audit_laci` (INSERT),
`brangkas_users` (tanpa policy, hanya dibaca function SECURITY DEFINER).

### 2.6 Auth

- Pemakai: **1 user** — `rizqi.aibot@gmail.com` (id `893e20e6-70cc-46b9-aa29-71e849709a42`),
  password **bcrypt** (`$2a$...`), email terkonfirmasi.
- Hanya user ini yang ada di whitelist `brangkas_users`.

## 3. Backup

- File: **`saldo-brangkas-supabase-backup.sql`** (± 296 KB, 1.166 baris)
- Isi: DDL 5 tabel + constraint, function `is_brangkas_user`, seluruh data (`INSERT`),
  tabel sesi `brangkas_sessions`, RLS + policy adaptasi, dan GRANT.
- Catatan metode: password DB Supabase tidak tersedia sehingga `pg_dump` binary tidak
  dapat dipakai. Backup dibuat sebagai **logical dump** melalui Supabase Management API
  (SQL `SELECT`), hasilnya diverifikasi dengan restore ke VPS (jumlah baris sama persis).
- Data Supabase production **tidak diubah/dihapus**.

## 4. Perubahan Supabase-only → PostgreSQL VPS

| Objek Supabase | Penyesuaian di VPS |
|---|---|
| `auth.users` + FK `brangkas_users.user_id` | FK dihapus. Kolom `email`, `password_hash` (bcrypt), `is_active` ditambahkan ke `brangkas_users`. |
| GoTrue Auth (`auth.uid()`) | **Dihapus** (2026-09-13). Aplikasi kini tanpa login/password; akses DB lewat role terbatas `saldo_app`. |
| PostgREST + anon/publishable key | Diganti REST API `/api/*` (Python + `psycopg2`). |
| RLS `TO authenticated USING is_brangkas_user(auth.uid())` | RLS tetap aktif; policy `FOR ALL TO saldo_app USING(true) WITH CHECK(true)`. Tabel `brangkas_users`/`brangkas_sessions` tetap ada (tidak ada tabel/data dihapus) namun tidak lagi dipakai untuk login. |
| anon/service_role key | Tidak dipakai lagi (dihapus dari `index.html`). |
| Extension Supabase lain | Tidak diperlukan. `gen_random_uuid()` tersedia di PostgreSQL 14 core. |

## 5. Implementasi VPS

- Project: `/home/juwita/saldo-brangkas`
- Website Nginx: `/var/www/saldo-brangkas/index.html`
- Database: `saldo_brangkas` (role terbatas `saldo_app`)
- Backend: `/home/juwita/saldo-brangkas/backend/app.py`
- Service: `saldo-brangkas.service` (systemd, listen `127.0.0.1:8787`, enabled on boot)
- Nginx: `/etc/nginx/sites-available/saldo-brangkas` (server_name `103.127.96.23`;
  `/api/` di-proxy ke backend). File `default` (juwita.biz.id) tidak diubah.
- Akses sementara: **http://103.127.96.23/**

## 6. Keamanan

Sejak pembaruan 2026-09-13, aplikasi **sengaja berjalan tanpa autentikasi** (sesuai
permintaan). Konsekuensi & pengamanan yang tersisa:

- Endpoint `/api/*` terbuka tanpa login. **Siapa pun yang bisa menjangkau
  `http://103.127.96.23/` dapat membaca dan mengubah data.**
- Tidak ada password/bcrypt/session/cookie di kode maupun runtime.
- Akses database tetap lewat role terbatas `saldo_app` (hanya database `saldo_brangkas`
  dan hanya tabel brangkas). Terbukti **tidak bisa membaca** tabel `juwita_one`
  (`permission denied`).
- Validasi input; query selalu parameterized (anti SQL injection).
- Cek `Origin` pada request pengubah data (mengurangi CSRF lintas situs).
- Hapus-semua-transaksi tetap memerlukan konfirmasi `{"confirm":"HAPUS_SEMUA"}`.
- `.env` (berisi password DB) mode `600`, tidak masuk Git (`.gitignore`).
- Tabel `brangkas_users` & `brangkas_sessions` dan seluruh data **tidak dihapus**;
  hanya tidak lagi dipakai.

## 7. Pengujian (semua lulus)

| Uji | Hasil |
|---|---|
| Website via IP VPS (eksternal) | HTTP 200 |
| `/api/health` (direct + via nginx) | `{"ok":true}` |
| Halaman tanpa login | langsung tampil & memuat data |
| Data saldo tampil | saldo_awal 2.077.000, 1004 transaksi |
| Tambah transaksi | 201 |
| Edit transaksi | 200 |
| Checklist "sudah disalin" | 200 |
| Hapus transaksi | 200 |
| Autosave catatan | 200 tersimpan |
| Hapus semua tanpa konfirmasi | 400 (ditolak) |
| Reload/ganti perangkat | data tetap (tersimpan di PostgreSQL VPS) |
| Jumlah data akhir | tetap 1004 (tidak ada yang hilang) |
| Juwita One | DB `juwita_one` utuh (25 tabel); situs `https://juwita.biz.id` HTTP 200 |

## 8. Yang tidak dapat dipindahkan

- **TLS/HTTPS** untuk Saldo Brangkas belum ada; sementara via HTTP di IP VPS.
- Konfigurasi `auth`/`storage`/`realtime`/`edge functions` Supabase tidak dipakai dan
  tidak diperlukan oleh aplikasi.

## 9. Langkah berikutnya (opsional)

1. Batasi akses jaringan bila perlu (mis. firewall/allowlist IP atau reverse proxy auth).
2. Ganti password DB `saldo_app` bila ingin rotasi.
3. Pasang backup terjadwal `pg_dump` untuk `saldo_brangkas` (cron harian).
4. Hapus `.temp` dan file transfer sementara di VPS.

## 10. Pembaruan 2026-09-13 — Penghapusan Autentikasi

- `index.html`: form login/overlay, tombol Logout, dan seluruh kode login/logout/session
  dihapus. Halaman langsung memuat data.
- `backend/app.py`: endpoint `/api/login`, `/api/logout`, `/api/session`, verifikasi bcrypt,
  cookie, session, dan rate-limit login dihapus. Semua endpoint kini tanpa autentikasi.
- `.env.example`: variabel `SESSION_TTL_HOURS` dan `COOKIE_NAME` dihapus (tidak dipakai).
- Database **tidak diubah**: tabel `brangkas_users` & `brangkas_sessions` dan seluruh data
  transaksi/saldo/modal/audit dibiarkan utuh.
- Backup database tidak dihapus (tetap ada di lokal & VPS; tidak dipublikasikan ke Git).
- Commit: `feat: remove password authentication from saldo brangkas`.
