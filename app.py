import os
import json
import sqlite3
import hashlib
import secrets
import re
from datetime import datetime, date
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, g, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)


def get_secret_key():
    """Return a persistent secret key so sessions survive restarts."""
    key = os.environ.get('SECRET_KEY')
    if key:
        return key
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'secret_key')
    os.makedirs(os.path.dirname(key_file), exist_ok=True)
    if not os.path.exists(key_file):
        with open(key_file, 'w') as f:
            f.write(secrets.token_hex(32))
    with open(key_file) as f:
        return f.read().strip()


app.secret_key = get_secret_key()

DATABASE_URL = os.environ.get('DATABASE_URL', '')
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database', 'toko.db')

USE_POSTGRES = DATABASE_URL.startswith('postgres')


def q(sql):
    """Adapt an SQLite-style query to the active database.

    SQLite uses '?' placeholders and strftime(); PostgreSQL uses '%s'
    placeholders and TO_CHAR(). This keeps the rest of the code uniform.
    """
    if USE_POSTGRES:
        sql = sql.replace('?', '%s')
        sql = re.sub(r"strftime\('%Y-%m',\s*([\w.]+)\)", r"TO_CHAR(\1, 'YYYY-MM')", sql)
    return sql


class PostgresDB:
    """Minimal adapter so the rest of the code can use the same db.execute()
    API whether SQLite or PostgreSQL is active."""

    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        import psycopg2.extras
        cur = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if params is None:
            cur.execute(sql)
        else:
            cur.execute(sql, params)
        return cur

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


# ─── Database ────────────────────────────────────────────────────────────────

def get_db():
    if 'db' not in g:
        if USE_POSTGRES:
            import psycopg2
            g.db = PostgresDB(psycopg2.connect(DATABASE_URL))
        else:
            db_dir = os.path.dirname(DB_PATH)
            if not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)
            if not os.path.exists(DB_PATH):
                init_db()
            g.db = sqlite3.connect(DB_PATH)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    if USE_POSTGRES:
        import psycopg2
        import psycopg2.extras
        db = psycopg2.connect(DATABASE_URL)
        db.autocommit = True
        cur = db.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT DEFAULT 'karyawan',
                nama_lengkap TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS kategori (
                id SERIAL PRIMARY KEY,
                nama TEXT UNIQUE NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS barang (
                id SERIAL PRIMARY KEY,
                kode TEXT UNIQUE NOT NULL,
                nama TEXT NOT NULL,
                kategori_id INTEGER REFERENCES kategori(id),
                harga_beli REAL DEFAULT 0,
                harga_jual REAL DEFAULT 0,
                stok INTEGER DEFAULT 0,
                satuan TEXT DEFAULT 'pcs',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS stok_log (
                id SERIAL PRIMARY KEY,
                barang_id INTEGER NOT NULL REFERENCES barang(id),
                jenis TEXT NOT NULL CHECK(jenis IN ('masuk', 'keluar')),
                jumlah INTEGER NOT NULL,
                keterangan TEXT,
                tanggal DATE DEFAULT CURRENT_DATE,
                waktu TIME DEFAULT CURRENT_TIME,
                user_id INTEGER REFERENCES users(id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS transaksi (
                id SERIAL PRIMARY KEY,
                jenis TEXT NOT NULL CHECK(jenis IN ('pemasukan', 'pengeluaran')),
                jumlah REAL NOT NULL,
                keterangan TEXT,
                tanggal DATE DEFAULT CURRENT_DATE,
                waktu TIME DEFAULT CURRENT_TIME,
                user_id INTEGER REFERENCES users(id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pengaturan (
                id SERIAL PRIMARY KEY,
                nama_toko TEXT DEFAULT 'Toko Karunia Tambu',
                alamat TEXT,
                telepon TEXT,
                logo TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS nota (
                id SERIAL PRIMARY KEY,
                no_nota TEXT UNIQUE NOT NULL,
                pelanggan TEXT DEFAULT 'Umum',
                total REAL NOT NULL DEFAULT 0,
                diskon REAL DEFAULT 0,
                metode TEXT DEFAULT 'tunai',
                status TEXT DEFAULT 'lunas' CHECK(status IN ('lunas', 'utang')),
                tanggal DATE DEFAULT CURRENT_DATE,
                waktu TIME DEFAULT CURRENT_TIME,
                user_id INTEGER REFERENCES users(id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS nota_item (
                id SERIAL PRIMARY KEY,
                nota_id INTEGER NOT NULL REFERENCES nota(id) ON DELETE CASCADE,
                barang_id INTEGER REFERENCES barang(id),
                nama_barang TEXT NOT NULL,
                harga REAL NOT NULL,
                qty INTEGER NOT NULL,
                subtotal REAL NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pemesanan (
                id SERIAL PRIMARY KEY,
                barang_id INTEGER NOT NULL REFERENCES barang(id),
                supplier TEXT,
                qty INTEGER NOT NULL,
                satuan TEXT DEFAULT 'pcs',
                status TEXT DEFAULT 'dipesan' CHECK(status IN ('dipesan', 'diterima', 'batal')),
                catat_pengeluaran INTEGER DEFAULT 0,
                tanggal DATE DEFAULT CURRENT_DATE,
                user_id INTEGER REFERENCES users(id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS satuan (
                id SERIAL PRIMARY KEY,
                nama TEXT UNIQUE NOT NULL
            )
        """)

        # Hapus akun admin default (admin/admin123) bila masih memakai password bawaan.
        cur.execute("SELECT id, password FROM users WHERE username = 'admin'")
        row = cur.fetchone()
        if row and (check_password_hash(row['password'], 'admin123') or row['password'] == hashlib.sha256(b'admin123').hexdigest()):
            cur.execute("SELECT id FROM users WHERE username != 'admin' AND role = 'admin' ORDER BY id LIMIT 1")
            lain = cur.fetchone()
            if lain:
                for tbl in ('stok_log', 'transaksi', 'nota'):
                    cur.execute(f"UPDATE {tbl} SET user_id = %s WHERE user_id = %s", (lain['id'], row['id']))
                cur.execute("DELETE FROM users WHERE id = %s", (row['id'],))

        cur.execute("SELECT COUNT(*) FROM kategori")
        if cur.fetchone()[0] == 0:
            default_kategori = ['Makanan', 'Minuman', 'Rokok', 'Sembako', 'Kebutuhan Rumah', 'Lainnya']
            for k in default_kategori:
                cur.execute("INSERT INTO kategori (nama) VALUES (%s)", (k,))

        cur.execute("SELECT id FROM pengaturan LIMIT 1")
        if not cur.fetchone():
            cur.execute("INSERT INTO pengaturan (nama_toko) VALUES (%s)", ('Toko Karunia Tambu',))

        cur.execute("SELECT COUNT(*) FROM satuan")
        if cur.fetchone()[0] == 0:
            for s in ['pcs', 'sak', 'dus', 'box', 'roll', 'unit', 'meter', 'kaleng', 'liter']:
                cur.execute("INSERT INTO satuan (nama) VALUES (%s)", (s,))

        try:
            cur.execute("ALTER TABLE barang ADD COLUMN foto TEXT DEFAULT ''")
        except Exception:
            pass

        cur.close()
        db.close()
    else:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        db = sqlite3.connect(DB_PATH)
        db.execute("PRAGMA foreign_keys = ON")

        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT DEFAULT 'karyawan',
                nama_lengkap TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS kategori (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nama TEXT UNIQUE NOT NULL
            );
            CREATE TABLE IF NOT EXISTS barang (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kode TEXT UNIQUE NOT NULL,
                nama TEXT NOT NULL,
                kategori_id INTEGER,
                harga_beli REAL DEFAULT 0,
                harga_jual REAL DEFAULT 0,
                stok INTEGER DEFAULT 0,
                satuan TEXT DEFAULT 'pcs',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (kategori_id) REFERENCES kategori(id)
            );
            CREATE TABLE IF NOT EXISTS stok_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                barang_id INTEGER NOT NULL,
                jenis TEXT NOT NULL CHECK(jenis IN ('masuk', 'keluar')),
                jumlah INTEGER NOT NULL,
                keterangan TEXT,
                tanggal DATE DEFAULT (date('now')),
                waktu TIME DEFAULT (time('now', 'localtime')),
                user_id INTEGER,
                FOREIGN KEY (barang_id) REFERENCES barang(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS transaksi (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                jenis TEXT NOT NULL CHECK(jenis IN ('pemasukan', 'pengeluaran')),
                jumlah REAL NOT NULL,
                keterangan TEXT,
                tanggal DATE DEFAULT (date('now')),
                waktu TIME DEFAULT (time('now', 'localtime')),
                user_id INTEGER,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS pengaturan (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nama_toko TEXT DEFAULT 'Toko Karunia Tambu',
                alamat TEXT,
                telepon TEXT,
                logo TEXT
            );
            CREATE TABLE IF NOT EXISTS nota (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                no_nota TEXT UNIQUE NOT NULL,
                pelanggan TEXT DEFAULT 'Umum',
                total REAL NOT NULL DEFAULT 0,
                diskon REAL DEFAULT 0,
                metode TEXT DEFAULT 'tunai',
                status TEXT DEFAULT 'lunas' CHECK(status IN ('lunas', 'utang')),
                tanggal DATE DEFAULT (date('now')),
                waktu TIME DEFAULT (time('now', 'localtime')),
                user_id INTEGER,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS nota_item (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nota_id INTEGER NOT NULL,
                barang_id INTEGER,
                nama_barang TEXT NOT NULL,
                harga REAL NOT NULL,
                qty INTEGER NOT NULL,
                subtotal REAL NOT NULL,
                FOREIGN KEY (nota_id) REFERENCES nota(id) ON DELETE CASCADE,
                FOREIGN KEY (barang_id) REFERENCES barang(id)
            );
            CREATE TABLE IF NOT EXISTS pemesanan (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                barang_id INTEGER NOT NULL,
                supplier TEXT,
                qty INTEGER NOT NULL,
                satuan TEXT DEFAULT 'pcs',
                status TEXT DEFAULT 'dipesan' CHECK(status IN ('dipesan', 'diterima', 'batal')),
                catat_pengeluaran INTEGER DEFAULT 0,
                tanggal DATE DEFAULT (date('now')),
                user_id INTEGER,
                FOREIGN KEY (barang_id) REFERENCES barang(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS satuan (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nama TEXT UNIQUE NOT NULL
            );
        """)

        # Hapus akun admin default (admin/admin123) bila masih memakai password bawaan.
        cursor = db.execute("SELECT id, password FROM users WHERE username = 'admin'")
        row = cursor.fetchone()
        if row and verify_password('admin123', row[1]):
            lain = db.execute(q("SELECT id FROM users WHERE username != 'admin' AND role = 'admin' ORDER BY id LIMIT 1")).fetchone()
            if lain:
                id_lain = lain[0] if not hasattr(lain, 'keys') else lain['id']
                for tbl in ('stok_log', 'transaksi', 'nota'):
                    db.execute(q(f"UPDATE {tbl} SET user_id = ? WHERE user_id = ?"), (id_lain, row[0]))
                db.execute(q("DELETE FROM users WHERE id = ?"), (row[0],))

        cursor = db.execute("SELECT COUNT(*) FROM kategori")
        if cursor.fetchone()[0] == 0:
            default_kategori = ['Makanan', 'Minuman', 'Rokok', 'Sembako', 'Kebutuhan Rumah', 'Lainnya']
            for k in default_kategori:
                db.execute("INSERT INTO kategori (nama) VALUES (?)", (k,))

        cursor = db.execute("SELECT id FROM pengaturan LIMIT 1")
        if not cursor.fetchone():
            db.execute("INSERT INTO pengaturan (nama_toko) VALUES (?)", ('Toko Karunia Tambu',))

        cursor = db.execute("SELECT COUNT(*) FROM satuan")
        if cursor.fetchone()[0] == 0:
            for s in ['pcs', 'sak', 'dus', 'box', 'roll', 'unit', 'meter', 'kaleng', 'liter']:
                db.execute("INSERT INTO satuan (nama) VALUES (?)", (s,))

        cols = [r[1] for r in db.execute("PRAGMA table_info(barang)").fetchall()]
        if 'foto' not in cols:
            db.execute("ALTER TABLE barang ADD COLUMN foto TEXT DEFAULT ''")

        db.commit()
        db.close()


# ─── Auth Helpers ────────────────────────────────────────────────────────────

def hash_password(password):
    return generate_password_hash(password)


def verify_password(password, hashed):
    # Migrate legacy SHA-256 hashes (64 hex chars) to werkzeug automatically.
    if len(hashed) == 64 and all(c in '0123456789abcdef' for c in hashed):
        return hashlib.sha256(password.encode()).hexdigest() == hashed
    return check_password_hash(hashed, password)


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Silakan login terlebih dahulu', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.template_filter('rupiah')
def rupiah(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0
    if value == int(value):
        value = int(value)
    return f"Rp {value:,.0f}".replace(',', '.')


# ─── Routes: Auth ────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']

        db = get_db()
        user = db.execute(
            q("SELECT * FROM users WHERE lower(username) = lower(?)"),
            (username,)
        ).fetchone()

        if user and verify_password(password, user['password']):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            session['nama_lengkap'] = user['nama_lengkap']
            flash(f'Selamat datang, {user["nama_lengkap"] or user["username"]}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Username atau password salah', 'danger')

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('Anda telah logout', 'info')
    return redirect(url_for('login'))


# ─── Routes: Dashboard ──────────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()

    total_barang = db.execute(q("SELECT COUNT(*) FROM barang")).fetchone()[0]
    total_stok = db.execute(q("SELECT COALESCE(SUM(stok), 0) FROM barang")).fetchone()[0]
    stok_habis = db.execute(q("SELECT COUNT(*) FROM barang WHERE stok = 0")).fetchone()[0]

    today = date.today().isoformat()
    pemasukan_hari = db.execute(
        q("SELECT COALESCE(SUM(jumlah), 0) FROM transaksi WHERE jenis='pemasukan' AND tanggal=?"),
        (today,)
    ).fetchone()[0]
    pengeluaran_hari = db.execute(
        q("SELECT COALESCE(SUM(jumlah), 0) FROM transaksi WHERE jenis='pengeluaran' AND tanggal=?"),
        (today,)
    ).fetchone()[0]

    bulan = datetime.now().strftime('%Y-%m')
    pemasukan_bulan = db.execute(
        q("SELECT COALESCE(SUM(jumlah), 0) FROM transaksi WHERE jenis='pemasukan' AND strftime('%Y-%m', tanggal)=?"),
        (bulan,)
    ).fetchone()[0]
    pengeluaran_bulan = db.execute(
        q("SELECT COALESCE(SUM(jumlah), 0) FROM transaksi WHERE jenis='pengeluaran' AND strftime('%Y-%m', tanggal)=?"),
        (bulan,)
    ).fetchone()[0]

    barang_stok_rendah = db.execute(
        q("SELECT * FROM barang WHERE stok <= 5 ORDER BY stok ASC LIMIT 5")
    ).fetchall()

    transaksi_terakhir = db.execute(q("""
        SELECT s.*, b.nama as nama_barang, b.kode
        FROM stok_log s
        JOIN barang b ON s.barang_id = b.id
        ORDER BY s.id DESC LIMIT 5
    """)).fetchall()

    pengaturan = db.execute(q("SELECT * FROM pengaturan LIMIT 1")).fetchone()

    transaksi_nota_hari = db.execute(
        q("SELECT COUNT(*) FROM nota WHERE tanggal=?"),
        (today,)
    ).fetchone()[0]

    return render_template('dashboard.html',
        total_barang=total_barang,
        total_stok=total_stok,
        stok_habis=stok_habis,
        pemasukan_hari=pemasukan_hari,
        pengeluaran_hari=pengeluaran_hari,
        pemasukan_bulan=pemasukan_bulan,
        pengeluaran_bulan=pengeluaran_bulan,
        barang_stok_rendah=barang_stok_rendah,
        transaksi_terakhir=transaksi_terakhir,
        transaksi_nota_hari=transaksi_nota_hari,
        pengaturan=pengaturan
    )


# ─── Routes: Barang ─────────────────────────────────────────────────────────

@app.route('/barang')
@login_required
def barang_list():
    db = get_db()
    search = request.args.get('search', '')
    kategori_filter = request.args.get('kategori', '')
    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = 20

    where = " WHERE 1=1"
    params = []
    if search:
        where += " AND (b.nama LIKE ? OR b.kode LIKE ?)"
        params.extend([f'%{search}%', f'%{search}%'])
    if kategori_filter:
        where += " AND b.kategori_id = ?"
        params.append(kategori_filter)

    total = db.execute(q(f"SELECT COUNT(*) FROM barang b{where}"), params).fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)

    query = f"""
        SELECT b.*, k.nama as nama_kategori
        FROM barang b
        LEFT JOIN kategori k ON b.kategori_id = k.id
        {where}
        ORDER BY b.nama ASC
        LIMIT {per_page} OFFSET {(page - 1) * per_page}
    """
    barang = db.execute(q(query), params).fetchall()
    kategori = db.execute(q("SELECT * FROM kategori ORDER BY nama")).fetchall()
    satuan = db.execute(q("SELECT * FROM satuan ORDER BY nama")).fetchall()
    stok_menipis = db.execute(q("SELECT COUNT(*) FROM barang WHERE stok <= 5")).fetchone()[0]
    kategori_aktif = db.execute(q("SELECT COUNT(DISTINCT kategori_id) FROM barang WHERE kategori_id IS NOT NULL")).fetchone()[0]

    return render_template('barang.html', barang=barang, kategori=kategori, satuan=satuan,
                           search=search, kategori_filter=kategori_filter,
                           page=page, total_pages=total_pages, total=total,
                           stok_menipis=stok_menipis, kategori_aktif=kategori_aktif)


UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')


def simpan_foto(file_storage):
    """Simpan foto upload ke static/uploads dan kembalikan nama filenya."""
    if not file_storage or not file_storage.filename:
        return ''
    ekstensi = os.path.splitext(file_storage.filename)[1].lower()
    if ekstensi not in ('.jpg', '.jpeg', '.png', '.webp'):
        return ''
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    nama = secrets.token_hex(8) + ekstensi
    file_storage.save(os.path.join(UPLOAD_DIR, nama))
    return nama


@app.route('/barang/tambah', methods=['POST'])
@login_required
def barang_tambah():
    db = get_db()
    kode = request.form['kode'].strip()
    nama = request.form['nama'].strip()
    kategori_id = request.form['kategori_id'] or None
    harga_beli = request.form.get('harga_beli', '0') or 0
    harga_jual = request.form.get('harga_jual', '0') or 0
    stok = request.form.get('stok', '0') or 0
    satuan = request.form.get('satuan', 'pcs')
    foto = simpan_foto(request.files.get('foto'))

    if not kode or not nama:
        flash('Kode dan nama barang wajib diisi', 'danger')
        return redirect(url_for('barang_list'))

    existing = db.execute(q("SELECT id FROM barang WHERE kode = ?"), (kode,)).fetchone()
    if existing:
        flash(f'Kode barang "{kode}" sudah ada', 'danger')
        return redirect(url_for('barang_list'))

    db.execute(
        q("INSERT INTO barang (kode, nama, kategori_id, harga_beli, harga_jual, stok, satuan, foto) VALUES (?,?,?,?,?,?,?,?)"),
        (kode, nama, kategori_id, float(harga_beli), float(harga_jual), int(stok), satuan, foto)
    )
    db.commit()
    flash(f'Barang "{nama}" berhasil ditambahkan', 'success')
    return redirect(url_for('barang_list'))


@app.route('/barang/edit/<int:id>', methods=['POST'])
@login_required
def barang_edit(id):
    db = get_db()
    nama = request.form['nama'].strip()
    kategori_id = request.form['kategori_id'] or None
    harga_beli = request.form.get('harga_beli', '0') or 0
    harga_jual = request.form.get('harga_jual', '0') or 0
    stok = request.form.get('stok', '0') or 0
    satuan = request.form.get('satuan', 'pcs')

    foto = simpan_foto(request.files.get('foto'))
    if foto:
        db.execute(
            q("UPDATE barang SET nama=?, kategori_id=?, harga_beli=?, harga_jual=?, stok=?, satuan=?, foto=? WHERE id=?"),
            (nama, kategori_id, float(harga_beli), float(harga_jual), int(stok), satuan, foto, id)
        )
    else:
        db.execute(
            q("UPDATE barang SET nama=?, kategori_id=?, harga_beli=?, harga_jual=?, stok=?, satuan=? WHERE id=?"),
            (nama, kategori_id, float(harga_beli), float(harga_jual), int(stok), satuan, id)
        )
    db.commit()
    flash('Barang berhasil diupdate', 'success')
    return redirect(url_for('barang_list'))


@app.route('/barang/hapus/<int:id>')
@login_required
def barang_hapus(id):
    if session.get('role') != 'admin':
        flash('Hanya admin yang bisa menghapus barang', 'danger')
        return redirect(url_for('barang_list'))

    db = get_db()
    barang = db.execute(q("SELECT foto FROM barang WHERE id = ?"), (id,)).fetchone()
    # Lepaskan riwayat nota dari barang (nama barang tetap tersimpan di nota)
    db.execute(q("UPDATE nota_item SET barang_id = NULL WHERE barang_id = ?"), (id,))
    # Hapus pemesanan yang masih terkait barang ini
    db.execute(q("DELETE FROM pemesanan WHERE barang_id = ?"), (id,))
    db.execute(q("DELETE FROM stok_log WHERE barang_id = ?"), (id,))
    db.execute(q("DELETE FROM barang WHERE id = ?"), (id,))
    db.commit()
    if barang and barang['foto']:
        path = os.path.join(UPLOAD_DIR, barang['foto'])
        if os.path.exists(path):
            os.remove(path)
    flash('Barang berhasil dihapus', 'success')
    return redirect(url_for('barang_list'))


# ─── Routes: Kategori ───────────────────────────────────────────────────────

@app.route('/kategori/tambah', methods=['POST'])
@login_required
def kategori_tambah():
    nama = request.form['nama'].strip()
    if not nama:
        flash('Nama kategori wajib diisi', 'danger')
        return redirect(url_for('kategori_page'))

    db = get_db()
    existing = db.execute(q("SELECT id FROM kategori WHERE nama = ?"), (nama,)).fetchone()
    if existing:
        flash('Kategori sudah ada', 'danger')
        return redirect(url_for('kategori_page'))

    db.execute(q("INSERT INTO kategori (nama) VALUES (?)"), (nama,))
    db.commit()
    flash(f'Kategori "{nama}" berhasil ditambahkan', 'success')
    return redirect(url_for('kategori_page'))


# ─── Routes: Barang Masuk / Keluar ──────────────────────────────────────────

@app.route('/stok')
@login_required
def stok_page():
    db = get_db()
    barang = db.execute(q("SELECT * FROM barang ORDER BY nama")).fetchall()

    log = db.execute(q("""
        SELECT s.*, b.nama as nama_barang, b.kode, u.username
        FROM stok_log s
        JOIN barang b ON s.barang_id = b.id
        LEFT JOIN users u ON s.user_id = u.id
        ORDER BY s.id DESC LIMIT 50
    """)).fetchall()

    return render_template('stok.html', barang=barang, log=log)


@app.route('/stok/masuk', methods=['POST'])
@login_required
def stok_masuk():
    db = get_db()
    barang_id = request.form['barang_id']
    try:
        jumlah = int(request.form['jumlah'])
    except (TypeError, ValueError):
        flash('Jumlah harus berupa angka', 'danger')
        return redirect(url_for('stok_page'))
    keterangan = request.form.get('keterangan', '')

    if jumlah <= 0:
        flash('Jumlah harus lebih dari 0', 'danger')
        return redirect(url_for('stok_page'))

    db.execute(q("UPDATE barang SET stok = stok + ? WHERE id = ?"), (jumlah, barang_id))
    db.execute(
        q("INSERT INTO stok_log (barang_id, jenis, jumlah, keterangan, user_id) VALUES (?, 'masuk', ?, ?, ?)"),
        (barang_id, jumlah, keterangan, session['user_id'])
    )
    db.commit()
    flash('Barang masuk berhasil dicatat', 'success')
    return redirect(url_for('stok_page'))


@app.route('/stok/keluar', methods=['POST'])
@login_required
def stok_keluar():
    db = get_db()
    barang_id = request.form['barang_id']
    try:
        jumlah = int(request.form['jumlah'])
    except (TypeError, ValueError):
        flash('Jumlah harus berupa angka', 'danger')
        return redirect(url_for('stok_page'))
    keterangan = request.form.get('keterangan', '')

    if jumlah <= 0:
        flash('Jumlah harus lebih dari 0', 'danger')
        return redirect(url_for('stok_page'))

    barang = db.execute(q("SELECT stok FROM barang WHERE id = ?"), (barang_id,)).fetchone()
    if barang['stok'] < jumlah:
        flash(f'Stok tidak mencukupi. Stok tersisa: {barang["stok"]}', 'danger')
        return redirect(url_for('stok_page'))

    db.execute(q("UPDATE barang SET stok = stok - ? WHERE id = ?"), (jumlah, barang_id))
    db.execute(
        q("INSERT INTO stok_log (barang_id, jenis, jumlah, keterangan, user_id) VALUES (?, 'keluar', ?, ?, ?)"),
        (barang_id, jumlah, keterangan, session['user_id'])
    )
    db.commit()
    flash('Barang keluar berhasil dicatat', 'success')
    return redirect(url_for('stok_page'))


# ─── Routes: Keuangan ───────────────────────────────────────────────────────

@app.route('/keuangan')
@login_required
def keuangan_page():
    db = get_db()
    bulan = request.args.get('bulan', datetime.now().strftime('%Y-%m'))
    jenis_filter = request.args.get('jenis', '')

    query = "SELECT * FROM transaksi WHERE strftime('%Y-%m', tanggal) = ?"
    params = [bulan]

    if jenis_filter:
        query += " AND jenis = ?"
        params.append(jenis_filter)

    query += " ORDER BY id DESC"
    transaksi = db.execute(q(query), params).fetchall()

    total_pemasukan = db.execute(
        q("SELECT COALESCE(SUM(jumlah), 0) FROM transaksi WHERE jenis='pemasukan' AND strftime('%Y-%m', tanggal)=?"),
        (bulan,)
    ).fetchone()[0]

    total_pengeluaran = db.execute(
        q("SELECT COALESCE(SUM(jumlah), 0) FROM transaksi WHERE jenis='pengeluaran' AND strftime('%Y-%m', tanggal)=?"),
        (bulan,)
    ).fetchone()[0]

    # Peta no_nota -> id nota untuk transaksi penjualan, agar baris riwayat bisa diklik
    nota_ids = {}
    for t in transaksi:
        if 'nota' in (t['keterangan'] or ''):
            m = re.search(r'nota (INV-[\d-]+)', t['keterangan'] or '')
            if m:
                row = db.execute(q("SELECT id FROM nota WHERE no_nota = ?"), (m.group(1),)).fetchone()
                if row:
                    nota_ids[t['id']] = row[0]

    return render_template('keuangan.html',
        transaksi=transaksi, bulan=bulan, jenis_filter=jenis_filter,
        total_pemasukan=total_pemasukan, total_pengeluaran=total_pengeluaran,
        nota_ids=nota_ids
    )


@app.route('/keuangan/tambah', methods=['POST'])
@login_required
def keuangan_tambah():
    db = get_db()
    jenis = request.form['jenis']
    try:
        jumlah = float(request.form['jumlah'])
    except (TypeError, ValueError):
        flash('Jumlah harus berupa angka', 'danger')
        return redirect(url_for('keuangan_page'))
    keterangan = request.form.get('keterangan', '')

    if jumlah <= 0:
        flash('Jumlah harus lebih dari 0', 'danger')
        return redirect(url_for('keuangan_page'))

    db.execute(
        q("INSERT INTO transaksi (jenis, jumlah, keterangan, user_id) VALUES (?, ?, ?, ?)"),
        (jenis, jumlah, keterangan, session['user_id'])
    )
    db.commit()
    flash(f'{jenis.capitalize()} berhasil dicatat', 'success')
    if jenis == 'pengeluaran':
        return redirect(url_for('keuangan_page', jenis='pengeluaran'))
    return redirect(url_for('keuangan_page'))


@app.route('/keuangan/hapus/<int:id>')
@login_required
def keuangan_hapus(id):
    if session.get('role') != 'admin':
        flash('Hanya admin yang bisa menghapus transaksi', 'danger')
        return redirect(url_for('keuangan_page'))

    db = get_db()
    db.execute(q("DELETE FROM transaksi WHERE id = ?"), (id,))
    db.commit()
    flash('Transaksi berhasil dihapus', 'success')
    return redirect(url_for('keuangan_page'))


# ─── Routes: Laporan ────────────────────────────────────────────────────────

@app.route('/laporan')
@login_required
def laporan_page():
    db = get_db()
    bulan = request.args.get('bulan', datetime.now().strftime('%Y-%m'))

    total_pemasukan = db.execute(
        q("SELECT COALESCE(SUM(jumlah), 0) FROM transaksi WHERE jenis='pemasukan' AND strftime('%Y-%m', tanggal)=?"),
        (bulan,)
    ).fetchone()[0]

    total_pengeluaran = db.execute(
        q("SELECT COALESCE(SUM(jumlah), 0) FROM transaksi WHERE jenis='pengeluaran' AND strftime('%Y-%m', tanggal)=?"),
        (bulan,)
    ).fetchone()[0]

    laba_bersih = total_pemasukan - total_pengeluaran

    barang_terjual = db.execute(q("""
        SELECT b.nama, b.kode, SUM(s.jumlah) as total
        FROM stok_log s
        JOIN barang b ON s.barang_id = b.id
        WHERE s.jenis = 'keluar' AND strftime('%Y-%m', s.tanggal) = ?
        GROUP BY b.id
        ORDER BY total DESC
    """), (bulan,)).fetchall()

    barang_terlaris = db.execute(q("""
        SELECT b.nama, b.kode, SUM(s.jumlah) as total
        FROM stok_log s
        JOIN barang b ON s.barang_id = b.id
        WHERE s.jenis = 'keluar' AND strftime('%Y-%m', s.tanggal) = ?
        GROUP BY b.id
        ORDER BY total DESC LIMIT 10
    """), (bulan,)).fetchall()

    stok_saat_ini = db.execute(q("""
        SELECT b.nama, b.kode, b.stok, b.satuan, k.nama as kategori
        FROM barang b
        LEFT JOIN kategori k ON b.kategori_id = k.id
        ORDER BY b.stok ASC
    """)).fetchall()

    return render_template('laporan.html',
        bulan=bulan, total_pemasukan=total_pemasukan,
        total_pengeluaran=total_pengeluaran, laba_bersih=laba_bersih,
        barang_terjual=barang_terjual, barang_terlaris=barang_terlaris,
        stok_saat_ini=stok_saat_ini
    )


# ─── Routes: Pengaturan ─────────────────────────────────────────────────────

@app.route('/pengaturan', methods=['GET', 'POST'])
@login_required
def pengaturan_page():
    if session.get('role') != 'admin':
        flash('Hanya admin yang bisa mengakses pengaturan', 'danger')
        return redirect(url_for('dashboard'))

    db = get_db()

    if request.method == 'POST':
        nama_toko = request.form.get('nama_toko', 'Toko Karunia Tambu')
        alamat = request.form.get('alamat', '')
        telepon = request.form.get('telepon', '')

        db.execute(
            q("UPDATE pengaturan SET nama_toko=?, alamat=?, telepon=? WHERE id=1"),
            (nama_toko, alamat, telepon)
        )
        db.commit()
        flash('Pengaturan berhasil disimpan', 'success')
        return redirect(url_for('pengaturan_page'))

    pengaturan = db.execute(q("SELECT * FROM pengaturan LIMIT 1")).fetchone()
    users = db.execute(q("SELECT * FROM users ORDER BY id")).fetchall()

    return render_template('pengaturan.html', pengaturan=pengaturan, users=users)


@app.route('/pengaturan/tambah-user', methods=['POST'])
@login_required
def tambah_user():
    if session.get('role') != 'admin':
        flash('Hanya admin yang bisa menambah user', 'danger')
        return redirect(url_for('pengaturan_page'))

    username = request.form['username'].strip()
    password = request.form['password']
    nama_lengkap = request.form.get('nama_lengkap', '')
    role = request.form.get('role', 'karyawan')

    db = get_db()
    existing = db.execute(q("SELECT id FROM users WHERE username = ?"), (username,)).fetchone()
    if existing:
        flash('Username sudah ada', 'danger')
        return redirect(url_for('pengaturan_page'))

    db.execute(
        q("INSERT INTO users (username, password, role, nama_lengkap) VALUES (?, ?, ?, ?)"),
        (username, hash_password(password), role, nama_lengkap)
    )
    db.commit()
    flash(f'User "{username}" berhasil ditambahkan', 'success')
    return redirect(url_for('pengaturan_page'))


# ─── Routes: Nota / Penjualan ───────────────────────────────────────────────

def buat_no_nota(db):
    today = date.today().strftime('%Y%m%d')
    row = db.execute(q("SELECT COUNT(*) FROM nota WHERE no_nota LIKE ?"), (f'INV-{today}-%',)).fetchone()[0]
    return f"INV-{today}-{row + 1:03d}"


@app.route('/nota')
@login_required
def nota_buat():
    db = get_db()
    barang = db.execute(q("""
        SELECT b.*, k.nama as nama_kategori
        FROM barang b
        LEFT JOIN kategori k ON b.kategori_id = k.id
        ORDER BY b.nama ASC
    """)).fetchall()
    kategori = db.execute(q("SELECT * FROM kategori ORDER BY nama")).fetchall()
    return render_template('nota_buat.html', barang=barang, kategori=kategori)


@app.route('/nota/simpan', methods=['POST'])
@login_required
def nota_simpan():
    db = get_db()
    try:
        items = json.loads(request.form.get('items', '[]'))
    except ValueError:
        flash('Data nota tidak valid', 'danger')
        return redirect(url_for('nota_buat'))

    if not items:
        flash('Nota kosong. Tambahkan minimal satu barang', 'danger')
        return redirect(url_for('nota_buat'))

    pelanggan = request.form.get('pelanggan', 'Umum').strip() or 'Umum'
    metode = request.form.get('metode', 'tunai')
    if metode not in ('tunai', 'transfer', 'qris'):
        metode = 'tunai'
    status = 'lunas'
    try:
        diskon = float(request.form.get('diskon', 0) or 0)
    except (TypeError, ValueError):
        diskon = 0

    # Validasi stok & hitung total
    total = 0
    barang_cache = {}
    for it in items:
        b = db.execute(q("SELECT * FROM barang WHERE id = ?"), (it['id'],)).fetchone()
        if not b:
            flash('Ada barang yang tidak ditemukan', 'danger')
            return redirect(url_for('nota_buat'))
        qty = int(it.get('qty', 0))
        if qty <= 0:
            continue
        if b['stok'] < qty:
            flash(f'Stok "{b["nama"]}" tidak mencukupi. Sisa: {b["stok"]}', 'danger')
            return redirect(url_for('nota_buat'))
        subtotal = b['harga_jual'] * qty
        total += subtotal
        barang_cache[it['id']] = (b, qty, subtotal)

    if total <= 0:
        flash('Nota kosong. Tambahkan minimal satu barang', 'danger')
        return redirect(url_for('nota_buat'))

    total_akhir = max(0, total - diskon)
    no_nota = buat_no_nota(db)

    db.execute(
        q("INSERT INTO nota (no_nota, pelanggan, total, diskon, metode, status, user_id) VALUES (?,?,?,?,?,?,?)"),
        (no_nota, pelanggan, total_akhir, diskon, metode, status, session['user_id'])
    )
    nota_id = db.execute(q("SELECT id FROM nota WHERE no_nota = ?"), (no_nota,)).fetchone()[0]

    for bid, (b, qty, subtotal) in barang_cache.items():
        db.execute(
            q("INSERT INTO nota_item (nota_id, barang_id, nama_barang, harga, qty, subtotal) VALUES (?,?,?,?,?,?)"),
            (nota_id, b['id'], b['nama'], b['harga_jual'], qty, subtotal)
        )
        db.execute(q("UPDATE barang SET stok = stok - ? WHERE id = ?"), (qty, b['id']))
        db.execute(
            q("INSERT INTO stok_log (barang_id, jenis, jumlah, keterangan, user_id) VALUES (?, 'keluar', ?, ?, ?)"),
            (b['id'], qty, f'Penjualan nota {no_nota}', session['user_id'])
        )

    db.execute(
        q("INSERT INTO transaksi (jenis, jumlah, keterangan, user_id) VALUES ('pemasukan', ?, ?, ?)"),
        (total_akhir, f'Penjualan nota {no_nota} - {pelanggan}', session['user_id'])
    )
    db.commit()
    return redirect(url_for('nota_cetak', id=nota_id))


@app.route('/nota/daftar')
@login_required
def nota_daftar():
    db = get_db()
    notas = db.execute(q("""
        SELECT n.*, u.username, COUNT(ni.id) as jumlah_item
        FROM nota n
        LEFT JOIN users u ON n.user_id = u.id
        LEFT JOIN nota_item ni ON ni.nota_id = n.id
        GROUP BY n.id
        ORDER BY n.id DESC LIMIT 100
    """)).fetchall()
    return render_template('nota_list.html', notas=notas)


@app.route('/nota/cetak/<int:id>')
@login_required
def nota_cetak(id):
    db = get_db()
    nota = db.execute(q("SELECT * FROM nota WHERE id = ?"), (id,)).fetchone()
    if not nota:
        flash('Nota tidak ditemukan', 'danger')
        return redirect(url_for('nota_daftar'))
    items = db.execute(q("SELECT * FROM nota_item WHERE nota_id = ?"), (id,)).fetchall()
    pengaturan = db.execute(q("SELECT * FROM pengaturan LIMIT 1")).fetchone()
    return render_template('nota_cetak.html', nota=nota, items=items, pengaturan=pengaturan)


@app.route('/nota/lunas/<int:id>')
@login_required
def nota_lunas(id):
    if session.get('role') != 'admin':
        flash('Hanya admin yang bisa menandai nota lunas', 'danger')
        return redirect(url_for('nota_daftar'))
    db = get_db()
    nota = db.execute(q("SELECT * FROM nota WHERE id = ? AND status = 'utang'"), (id,)).fetchone()
    if not nota:
        flash('Nota tidak ditemukan atau sudah lunas', 'danger')
        return redirect(url_for('nota_daftar'))
    db.execute(q("UPDATE nota SET status = 'lunas' WHERE id = ?"), (id,))
    db.execute(
        q("INSERT INTO transaksi (jenis, jumlah, keterangan, user_id) VALUES ('pemasukan', ?, ?, ?)"),
        (nota['total'], f'Pelunasan nota {nota["no_nota"]} - {nota["pelanggan"]}', session['user_id'])
    )
    db.commit()
    flash(f'Nota {nota["no_nota"]} ditandai lunas', 'success')
    return redirect(url_for('nota_daftar'))


# ─── Routes: Pemesanan Barang ───────────────────────────────────────────────

@app.route('/pemesanan')
@login_required
def pemesanan_page():
    db = get_db()
    barang = db.execute(q("SELECT * FROM barang ORDER BY nama")).fetchall()
    satuan_list = db.execute(q("SELECT * FROM satuan ORDER BY nama")).fetchall()
    pesanan = db.execute(q("""
        SELECT p.*, b.nama as nama_barang, b.kode, u.username
        FROM pemesanan p
        JOIN barang b ON p.barang_id = b.id
        LEFT JOIN users u ON p.user_id = u.id
        ORDER BY p.id DESC LIMIT 100
    """)).fetchall()
    return render_template('pemesanan.html', barang=barang, pesanan=pesanan, satuan_list=satuan_list)


@app.route('/pemesanan/tambah', methods=['POST'])
@login_required
def pemesanan_tambah():
    db = get_db()
    try:
        barang_id = int(request.form['barang_id'])
        qty = int(request.form['qty'])
    except (KeyError, TypeError, ValueError):
        flash('Data pemesanan tidak valid', 'danger')
        return redirect(url_for('pemesanan_page'))
    supplier = request.form.get('supplier', '').strip()
    satuan = request.form.get('satuan', 'pcs')
    catat = 1 if request.form.get('catat_pengeluaran') == 'on' else 0

    if qty <= 0:
        flash('Jumlah harus lebih dari 0', 'danger')
        return redirect(url_for('pemesanan_page'))

    db.execute(
        q("INSERT INTO pemesanan (barang_id, supplier, qty, satuan, catat_pengeluaran, user_id) VALUES (?,?,?,?,?,?)"),
        (barang_id, supplier, qty, satuan, catat, session['user_id'])
    )
    db.commit()
    flash('Pemesanan barang berhasil dicatat', 'success')
    return redirect(url_for('pemesanan_page'))


@app.route('/pemesanan/terima/<int:id>')
@login_required
def pemesanan_terima(id):
    db = get_db()
    p = db.execute(q("SELECT * FROM pemesanan WHERE id = ? AND status = 'dipesan'"), (id,)).fetchone()
    if not p:
        flash('Pemesanan tidak ditemukan atau sudah diproses', 'danger')
        return redirect(url_for('pemesanan_page'))

    b = db.execute(q("SELECT * FROM barang WHERE id = ?"), (p['barang_id'],)).fetchone()
    db.execute(q("UPDATE barang SET stok = stok + ? WHERE id = ?"), (p['qty'], p['barang_id']))
    db.execute(
        q("INSERT INTO stok_log (barang_id, jenis, jumlah, keterangan, user_id) VALUES (?, 'masuk', ?, ?, ?)"),
        (p['barang_id'], p['qty'], f'Penerimaan pemesanan #{id} dari {p["supplier"] or "supplier"}', session['user_id'])
    )
    if p['catat_pengeluaran'] and b:
        db.execute(
            q("INSERT INTO transaksi (jenis, jumlah, keterangan, user_id) VALUES ('pengeluaran', ?, ?, ?)"),
            (b['harga_beli'] * p['qty'], f'Pembelian pemesanan #{id}: {b["nama"]} x{p["qty"]}', session['user_id'])
        )
    db.execute(q("UPDATE pemesanan SET status = 'diterima' WHERE id = ?"), (id,))
    db.commit()
    flash('Pemesanan diterima, stok barang bertambah', 'success')
    return redirect(url_for('pemesanan_page'))


@app.route('/pemesanan/batal/<int:id>')
@login_required
def pemesanan_batal(id):
    db = get_db()
    p = db.execute(q("SELECT * FROM pemesanan WHERE id = ? AND status = 'dipesan'"), (id,)).fetchone()
    if not p:
        flash('Pemesanan tidak ditemukan atau sudah diproses', 'danger')
        return redirect(url_for('pemesanan_page'))
    db.execute(q("UPDATE pemesanan SET status = 'batal' WHERE id = ?"), (id,))
    db.commit()
    flash('Pemesanan dibatalkan', 'info')
    return redirect(url_for('pemesanan_page'))


# ─── Routes: Kategori & Satuan ──────────────────────────────────────────────

@app.route('/kategori')
@login_required
def kategori_page():
    db = get_db()
    kategori = db.execute(q("""
        SELECT k.*, COUNT(b.id) as jumlah_barang
        FROM kategori k
        LEFT JOIN barang b ON b.kategori_id = k.id
        GROUP BY k.id
        ORDER BY k.nama
    """)).fetchall()
    satuan = db.execute(q("""
        SELECT s.*, COUNT(b.id) as jumlah_barang
        FROM satuan s
        LEFT JOIN barang b ON b.satuan = s.nama
        GROUP BY s.id
        ORDER BY s.nama
    """)).fetchall()
    return render_template('kategori.html', kategori=kategori, satuan=satuan)


@app.route('/kategori/hapus/<int:id>')
@login_required
def kategori_hapus(id):
    if session.get('role') != 'admin':
        flash('Hanya admin yang bisa menghapus kategori', 'danger')
        return redirect(url_for('kategori_page'))
    db = get_db()
    dipakai = db.execute(q("SELECT COUNT(*) FROM barang WHERE kategori_id = ?"), (id,)).fetchone()[0]
    if dipakai:
        flash('Kategori masih dipakai oleh barang, tidak bisa dihapus', 'danger')
        return redirect(url_for('kategori_page'))
    db.execute(q("DELETE FROM kategori WHERE id = ?"), (id,))
    db.commit()
    flash('Kategori berhasil dihapus', 'success')
    return redirect(url_for('kategori_page'))


@app.route('/satuan/tambah', methods=['POST'])
@login_required
def satuan_tambah():
    nama = request.form['nama'].strip().lower()
    if not nama:
        flash('Nama satuan wajib diisi', 'danger')
        return redirect(url_for('kategori_page'))
    db = get_db()
    existing = db.execute(q("SELECT id FROM satuan WHERE nama = ?"), (nama,)).fetchone()
    if existing:
        flash('Satuan sudah ada', 'danger')
        return redirect(url_for('kategori_page'))
    db.execute(q("INSERT INTO satuan (nama) VALUES (?)"), (nama,))
    db.commit()
    flash(f'Satuan "{nama}" berhasil ditambahkan', 'success')
    return redirect(url_for('kategori_page'))


@app.route('/satuan/hapus/<int:id>')
@login_required
def satuan_hapus(id):
    if session.get('role') != 'admin':
        flash('Hanya admin yang bisa menghapus satuan', 'danger')
        return redirect(url_for('kategori_page'))
    db = get_db()
    sat = db.execute(q("SELECT nama FROM satuan WHERE id = ?"), (id,)).fetchone()
    if sat:
        dipakai = db.execute(q("SELECT COUNT(*) FROM barang WHERE satuan = ?"), (sat['nama'],)).fetchone()[0]
        if dipakai:
            flash('Satuan masih dipakai oleh barang, tidak bisa dihapus', 'danger')
            return redirect(url_for('kategori_page'))
    db.execute(q("DELETE FROM satuan WHERE id = ?"), (id,))
    db.commit()
    flash('Satuan berhasil dihapus', 'success')
    return redirect(url_for('kategori_page'))


# ─── Run ─────────────────────────────────────────────────────────────────────

# Initialize the database when the app is loaded. This runs both under
# `python app.py` and under a WSGI server (e.g. PythonAnywhere), so the
# tables are always ready before the first request. init_db() is idempotent.
init_db()

if __name__ == '__main__':
    print("=" * 50)
    print("  Toko Karunia Tambu - Sistem Manajemen")
    print("  Buka browser: http://127.0.0.1:5000")
    print("=" * 50)
    debug = os.environ.get('FLASK_DEBUG') == '1'
    app.run(host='0.0.0.0', port=5000, debug=debug)
