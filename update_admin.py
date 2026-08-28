"""One-time helper: update the admin username & password.

Run inside the project folder with the project's Python:
    python update_admin.py        (local)
    .venv/bin/python update_admin.py   (PythonAnywhere)

The credentials are asked interactively and are never stored in this file.
"""
import getpass
import os
import sqlite3

from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database', 'toko.db')


def main():
    username = input('Username baru: ').strip()
    if not username:
        print('Username tidak boleh kosong.')
        return
    password = getpass.getpass('Password baru: ')
    confirm = getpass.getpass('Ulangi password baru: ')
    if password != confirm:
        print('Password tidak sama. Dibatalkan.')
        return
    if len(password) < 8:
        print('Password minimal 8 karakter. Dibatalkan.')
        return

    db = sqlite3.connect(DB_PATH)
    db.execute(
        "UPDATE users SET username=?, password=? WHERE username IN ('admin', ?)",
        (username, generate_password_hash(password), username)
    )
    db.commit()
    users = db.execute("SELECT id, username, role FROM users ORDER BY id").fetchall()
    db.close()

    print()
    print('Selesai. Daftar user sekarang:')
    for uid, uname, role in users:
        print(f'  #{uid}  {uname}  ({role})')


if __name__ == '__main__':
    main()
