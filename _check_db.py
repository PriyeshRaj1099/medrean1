"""Temporary helper: report row counts in the dev SQLite database."""

import sqlite3

con = sqlite3.connect("backend/var/medilens_dev.db")
cur = con.cursor()
tables = cur.execute(
    "select name from sqlite_master where type='table' "
    "and name in ('users','reports','analyses','knowledge_documents','knowledge_chunks')"
).fetchall()
for (name,) in tables:
    count = cur.execute(f"select count(*) from {name}").fetchone()[0]
    print(f"{name}: {count}")
con.close()
