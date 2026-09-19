#!/usr/bin/env python3
"""
Backend API Saldo Brangkas (pengganti Supabase PostgREST).

Catatan keamanan:
- Aplikasi ini SENGAJA berjalan tanpa login/password (sesuai permintaan).
- Tidak ada session, cookie, atau bcrypt. Semua endpoint bersifat terbuka.
- Akses database tetap dibatasi lewat role terbatas "saldo_app" + RLS.

Tidak ada framework eksternal; hanya psycopg2 + stdlib.
"""
import json
import os
import re
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras


# --------------------------------------------------------------------------- #
# Konfigurasi
# --------------------------------------------------------------------------- #
def load_env(path):
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
load_env(os.path.join(PROJECT_DIR, ".env"))

DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "saldo_brangkas")
DB_USER = os.environ.get("DB_USER", "saldo_app")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
BIND_HOST = os.environ.get("BIND_HOST", "127.0.0.1")
BIND_PORT = int(os.environ.get("BIND_PORT", "8787"))
TZ = ZoneInfo(os.environ.get("APP_TZ", "Asia/Jakarta"))

JENIS_VALID = {"MASUK", "KELUAR", "KASBON"}
AUDIT_STATUS_VALID = {"COCOK", "PLUS", "MINUS"}
MODAL_STATUS_VALID = {"BELUM_DIKEMBALIKAN", "SUDAH_DIKEMBALIKAN"}

MAX_BODY = 256 * 1024


# --------------------------------------------------------------------------- #
# Utilitas
# --------------------------------------------------------------------------- #
class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return int(o) if o == o.to_integral_value() else float(o)
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        return super().default(o)


def jdump(obj):
    return json.dumps(obj, cls=DecimalEncoder, ensure_ascii=False)


def now_wib():
    return datetime.now(TZ)


def db_connect():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        connect_timeout=5,
    )


def query(sql, params=None, fetch=None, commit=False):
    conn = db_connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            if fetch == "all":
                out = [dict(r) for r in cur.fetchall()]
            elif fetch == "one":
                row = cur.fetchone()
                out = dict(row) if row else None
            else:
                out = None
            if commit:
                conn.commit()
            return out
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def clean_str(v, max_len=500, default=None):
    if v is None:
        return default
    return str(v).strip()[:max_len]


def parse_number(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(".", "").replace(",", ".")) if "," in str(v) else float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Handler
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "SaldoBrangkas/1.1"
    protocol_version = "HTTP/1.1"

    # ---------- low level ---------- #
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def _send(self, status, payload=None, headers=None, raw=False):
        body = b""
        if payload is not None:
            body = payload if raw else jdump(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8" if not raw else "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _err(self, status, message):
        self._send(status, {"error": message})

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length <= 0 or length > MAX_BODY:
            return None
        data = self.rfile.read(length)
        try:
            return json.loads(data.decode("utf-8"))
        except Exception:
            return None

    def _origin_ok(self):
        # Tanpa autentikasi, tetap tolak request lintas-situs untuk metode pengubah data
        # (mengurangi risiko CSRF dari situs lain).
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        try:
            from urllib.parse import urlparse
            return urlparse(origin).netloc == host
        except Exception:
            return False

    # ---------- routing ---------- #
    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    def do_HEAD(self):
        self._route("GET")

    def _route(self, method):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            if path == "/api/health" and method == "GET":
                return self._health()
            if path == "/api/data" and method == "GET":
                return self._get_data()
            if path == "/api/setting" and method == "PUT":
                return self._put_setting()
            if path == "/api/transaksi" and method == "POST":
                return self._create_transaksi()
            if path == "/api/transaksi" and method == "DELETE":
                return self._delete_all_transaksi()
            m = re.fullmatch(r"/api/transaksi/([0-9a-fA-F-]{36})", path)
            if m and method == "PUT":
                return self._update_transaksi(m.group(1))
            if m and method == "DELETE":
                return self._delete_transaksi(m.group(1))
            m = re.fullmatch(r"/api/transaksi/([0-9a-fA-F-]{36})/(toggle-disalin|toggle)", path)
            if m and method == "POST":
                return self._toggle_disalin(m.group(1))
            if path == "/api/modal-laci" and method == "GET":
                return self._list_modal()
            if path == "/api/modal-laci" and method == "POST":
                return self._create_modal()
            m = re.fullmatch(r"/api/modal-laci/(\d{4}-\d{2}-\d{2})", path)
            if m and method == "PUT":
                return self._update_modal(m.group(1))
            if path == "/api/audit-laci" and method == "GET":
                return self._list_audit()
            if path == "/api/audit-laci" and method == "POST":
                return self._create_audit()
            return self._err(404, "Endpoint tidak ditemukan")
        except psycopg2.Error as e:
            self.log_message("DB error: %s", str(e).replace("\n", " ")[:300])
            return self._err(500, "Kesalahan database")
        except Exception as e:
            self.log_message("Server error: %s", repr(e))
            return self._err(500, "Kesalahan server")

    # ---------- endpoints ---------- #
    def _health(self):
        row = query("SELECT 1 AS ok", fetch="one")
        self._send(200, {"ok": bool(row and row["ok"] == 1)})

    def _get_data(self):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)

        def _q(key, default=""):
            v = qs.get(key)
            return v[0] if v else default

        limit = 100
        try:
            page = int(_q("page", "1"))
        except (TypeError, ValueError):
            page = 1
        if page < 1:
            page = 1

        dari = _q("dari")
        sampai = _q("sampai")
        jenis = _q("jenis", "SEMUA")
        if jenis not in JENIS_VALID:
            jenis = "SEMUA"

        where = []
        wparams = []
        if dari:
            where.append("tanggal >= %s")
            wparams.append(dari)
        if sampai:
            where.append("tanggal <= %s")
            wparams.append(sampai)
        if jenis != "SEMUA":
            where.append("jenis = %s")
            wparams.append(jenis)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        setting = query("SELECT id, saldo_awal, catatan, updated_at FROM public.brangkas_setting WHERE id = 1", fetch="one")
        saldo_awal = (setting or {}).get("saldo_awal", 0) if setting else 0

        total = int((query("SELECT count(*) AS c FROM public.brangkas_transaksi" + where_sql, tuple(wparams), fetch="one") or {}).get("c", 0))

        sums = query(
            "SELECT "
            "COALESCE(SUM(CASE WHEN jenis='MASUK' THEN nominal ELSE 0 END),0) AS masuk, "
            "COALESCE(SUM(CASE WHEN jenis IN ('KELUAR','KASBON') THEN nominal ELSE 0 END),0) AS keluar "
            "FROM public.brangkas_transaksi" + where_sql,
            tuple(wparams),
            fetch="one",
        ) or {}

        offset = (page - 1) * limit
        rows = query(
            "SELECT * FROM ("
            " SELECT id, tanggal, jam, jenis, nominal, keterangan, pengambil, created_at, sudah_disalin,"
            "   %s + SUM(CASE WHEN jenis='MASUK' THEN nominal WHEN jenis IN ('KELUAR','KASBON') THEN -nominal ELSE 0 END)"
            "     OVER (ORDER BY tanggal ASC, jam ASC, created_at ASC, id ASC) AS saldo_setelah"
            " FROM public.brangkas_transaksi"
            ") sub" + where_sql
            + " ORDER BY tanggal DESC, jam DESC, created_at DESC, id DESC LIMIT %s OFFSET %s",
            tuple([saldo_awal] + wparams + [limit, offset]),
            fetch="all",
        )

        total_pages = ((total + limit - 1) // limit) if total > 0 else 1

        self._send(200, {
            "saldo_awal": saldo_awal,
            "catatan": (setting or {}).get("catatan") if setting else None,
            "transaksi": rows,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "total_masuk": int(sums.get("masuk") or 0),
            "total_keluar": int(sums.get("keluar") or 0),
        })

    def _put_setting(self):
        if not self._origin_ok():
            return self._err(403, "Origin tidak diizinkan")
        body = self._read_json()
        if body is None:
            return self._err(400, "Body JSON tidak valid")
        if "saldo_awal" in body:
            val = parse_number(body.get("saldo_awal"))
            if val is None or val < 0:
                return self._err(400, "Saldo awal tidak valid")
            query(
                "INSERT INTO public.brangkas_setting (id, saldo_awal, updated_at) VALUES (1, %s, now()) "
                "ON CONFLICT (id) DO UPDATE SET saldo_awal = EXCLUDED.saldo_awal, updated_at = now()",
                (val,),
                commit=True,
            )
        if "catatan" in body:
            catatan = clean_str(body.get("catatan"), 5000, "")
            query(
                "INSERT INTO public.brangkas_setting (id, catatan, updated_at) VALUES (1, %s, now()) "
                "ON CONFLICT (id) DO UPDATE SET catatan = EXCLUDED.catatan, updated_at = now()",
                (catatan,),
                commit=True,
            )
        self._send(200, {"ok": True})

    def _create_transaksi(self):
        if not self._origin_ok():
            return self._err(403, "Origin tidak diizinkan")
        body = self._read_json() or {}
        jenis = clean_str(body.get("jenis"), 10, "").upper()
        nominal = parse_number(body.get("nominal"))
        keterangan = clean_str(body.get("keterangan"), 1000, "-") or "-"
        pengambil = clean_str(body.get("pengambil"), 200, "-") or "-"
        if jenis not in JENIS_VALID:
            return self._err(400, "Jenis transaksi tidak valid")
        if nominal is None or nominal <= 0:
            return self._err(400, "Nominal tidak valid")
        now = now_wib()
        tanggal = clean_str(body.get("tanggal"), 10, now.strftime("%Y-%m-%d"))
        jam = clean_str(body.get("jam"), 8, now.strftime("%H:%M:%S"))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", tanggal or ""):
            tanggal = now.strftime("%Y-%m-%d")
        if not re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", jam or ""):
            jam = now.strftime("%H:%M:%S")
        if jenis == "MASUK":
            pengambil = "-"
        row = query(
            "INSERT INTO public.brangkas_transaksi (tanggal, jam, jenis, nominal, keterangan, pengambil) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (tanggal, jam, jenis, nominal, keterangan, pengambil),
            fetch="one",
            commit=True,
        )
        self._send(201, {"ok": True, "id": str(row["id"])})

    def _update_transaksi(self, tid):
        if not self._origin_ok():
            return self._err(403, "Origin tidak diizinkan")
        body = self._read_json() or {}
        sets = []
        params = []
        if "nominal" in body:
            nominal = parse_number(body.get("nominal"))
            if nominal is None or nominal <= 0:
                return self._err(400, "Nominal tidak valid")
            sets.append("nominal = %s")
            params.append(nominal)
        if "keterangan" in body:
            sets.append("keterangan = %s")
            params.append(clean_str(body.get("keterangan"), 1000, "-") or "-")
        if "pengambil" in body:
            sets.append("pengambil = %s")
            params.append(clean_str(body.get("pengambil"), 200, "-") or "-")
        if "sudah_disalin" in body:
            sets.append("sudah_disalin = %s")
            params.append(bool(body.get("sudah_disalin")))
        if not sets:
            return self._err(400, "Tidak ada perubahan")
        params.append(tid)
        row = query(
            "UPDATE public.brangkas_transaksi SET %s WHERE id = %%s RETURNING id" % ", ".join(sets),
            tuple(params),
            fetch="one",
            commit=True,
        )
        if not row:
            return self._err(404, "Transaksi tidak ditemukan")
        self._send(200, {"ok": True})

    def _delete_transaksi(self, tid):
        if not self._origin_ok():
            return self._err(403, "Origin tidak diizinkan")
        row = query("DELETE FROM public.brangkas_transaksi WHERE id = %s RETURNING id", (tid,), fetch="one", commit=True)
        if not row:
            return self._err(404, "Transaksi tidak ditemukan")
        self._send(200, {"ok": True})

    def _delete_all_transaksi(self):
        if not self._origin_ok():
            return self._err(403, "Origin tidak diizinkan")
        body = self._read_json() or {}
        if body.get("confirm") != "HAPUS_SEMUA":
            return self._err(400, "Konfirmasi penghapusan semua data diperlukan")
        query("DELETE FROM public.brangkas_transaksi WHERE id IS NOT NULL", commit=True)
        self._send(200, {"ok": True})

    def _toggle_disalin(self, tid):
        if not self._origin_ok():
            return self._err(403, "Origin tidak diizinkan")
        body = self._read_json() or {}
        if "sudah_disalin" in body:
            val = bool(body.get("sudah_disalin"))
            row = query(
                "UPDATE public.brangkas_transaksi SET sudah_disalin = %s WHERE id = %s RETURNING id",
                (val, tid), fetch="one", commit=True,
            )
        else:
            row = query(
                "UPDATE public.brangkas_transaksi SET sudah_disalin = NOT sudah_disalin WHERE id = %s RETURNING id",
                (tid,), fetch="one", commit=True,
            )
        if not row:
            return self._err(404, "Transaksi tidak ditemukan")
        self._send(200, {"ok": True})

    # ---------- modal laci ---------- #
    def _list_modal(self):
        rows = query(
            "SELECT tanggal_modal, nominal_modal, status, closing_at, returned_at, created_at "
            "FROM public.brangkas_modal_laci ORDER BY tanggal_modal DESC",
            fetch="all",
        )
        self._send(200, {"modal_laci": rows})

    def _create_modal(self):
        if not self._origin_ok():
            return self._err(403, "Origin tidak diizinkan")
        body = self._read_json() or {}
        tanggal = clean_str(body.get("tanggal_modal"), 10)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", tanggal or ""):
            tanggal = now_wib().strftime("%Y-%m-%d")
        nominal = parse_number(body.get("nominal_modal"))
        if nominal is None or nominal < 0:
            nominal = 500000
        status = clean_str(body.get("status"), 30, "BELUM_DIKEMBALIKAN")
        if status not in MODAL_STATUS_VALID:
            status = "BELUM_DIKEMBALIKAN"
        row = query(
            "INSERT INTO public.brangkas_modal_laci (tanggal_modal, nominal_modal, status) VALUES (%s, %s, %s) "
            "ON CONFLICT (tanggal_modal) DO UPDATE SET nominal_modal = EXCLUDED.nominal_modal, status = EXCLUDED.status "
            "RETURNING tanggal_modal",
            (tanggal, nominal, status), fetch="one", commit=True,
        )
        self._send(201, {"ok": True, "tanggal_modal": str(row["tanggal_modal"])})

    def _update_modal(self, tanggal):
        if not self._origin_ok():
            return self._err(403, "Origin tidak diizinkan")
        body = self._read_json() or {}
        sets = []
        params = []
        if "nominal_modal" in body:
            nominal = parse_number(body.get("nominal_modal"))
            if nominal is None or nominal < 0:
                return self._err(400, "Nominal modal tidak valid")
            sets.append("nominal_modal = %s")
            params.append(nominal)
        if "status" in body:
            status = clean_str(body.get("status"), 30, "")
            if status not in MODAL_STATUS_VALID:
                return self._err(400, "Status modal tidak valid")
            sets.append("status = %s")
            params.append(status)
            if status == "SUDAH_DIKEMBALIKAN":
                sets.append("returned_at = now()")
        if not sets:
            return self._err(400, "Tidak ada perubahan")
        params.append(tanggal)
        row = query(
            "UPDATE public.brangkas_modal_laci SET %s WHERE tanggal_modal = %%s RETURNING tanggal_modal" % ", ".join(sets),
            tuple(params), fetch="one", commit=True,
        )
        if not row:
            return self._err(404, "Data modal laci tidak ditemukan")
        self._send(200, {"ok": True})

    # ---------- audit laci ---------- #
    def _list_audit(self):
        rows = query(
            "SELECT id, tanggal, uang_sistem, uang_fisik, selisih, status, keterangan, created_at "
            "FROM public.brangkas_audit_laci ORDER BY tanggal DESC, created_at DESC",
            fetch="all",
        )
        self._send(200, {"audit_laci": rows})

    def _create_audit(self):
        if not self._origin_ok():
            return self._err(403, "Origin tidak diizinkan")
        body = self._read_json() or {}
        uang_sistem = parse_number(body.get("uang_sistem")) or 0
        uang_fisik = parse_number(body.get("uang_fisik")) or 0
        selisih = uang_fisik - uang_sistem
        status = clean_str(body.get("status"), 10, "").upper()
        if status not in AUDIT_STATUS_VALID:
            status = "COCOK" if selisih == 0 else ("PLUS" if selisih > 0 else "MINUS")
        tanggal = clean_str(body.get("tanggal"), 10)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", tanggal or ""):
            tanggal = now_wib().strftime("%Y-%m-%d")
        keterangan = clean_str(body.get("keterangan"), 1000, "-") or "-"
        row = query(
            "INSERT INTO public.brangkas_audit_laci (tanggal, uang_sistem, uang_fisik, selisih, status, keterangan) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (tanggal, uang_sistem, uang_fisik, selisih, status, keterangan),
            fetch="one", commit=True,
        )
        self._send(201, {"ok": True, "id": str(row["id"])})


def main():
    server = ThreadingHTTPServer((BIND_HOST, BIND_PORT), Handler)
    print("Saldo Brangkas API listening on %s:%d (db=%s) [tanpa autentikasi]" % (BIND_HOST, BIND_PORT, DB_NAME), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
