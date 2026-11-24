"""Isolated synthetic demo: encrypted, size-bounded browser workspace."""
import base64
import hashlib
import json
import sqlite3
import zlib
from cryptography.fernet import Fernet, InvalidToken
from flask import g, session
from . import config

MAX_REPORTS=4
MAX_TOKEN=2600

def _cipher():
    key=hashlib.sha256(("tangguh-demo-v1:"+config.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))

def _rows(con):
    return {r["id"]:dict(r) for r in con.execute("SELECT * FROM reports")}

def open_workspace():
    con=sqlite3.connect(":memory:")
    con.row_factory=sqlite3.Row
    with sqlite3.connect(config.DB_PATH) as source:
        source.backup(con)
    g.demo_db=con
    g.demo_baseline=_rows(con)
    token=session.get("demo_workspace")
    if not token:
        return
    try:
        state=json.loads(zlib.decompress(_cipher().decrypt(token.encode(),ttl=7*86400)))
        for rid in state["removed"]:
            con.execute("DELETE FROM reports WHERE id=?",(rid,))
        allowed={r[1] for r in con.execute("PRAGMA table_info(reports)")}
        for row in state["rows"]:
            if set(row)!=allowed:
                raise ValueError("old schema")
            cols=list(row)
            con.execute("INSERT OR REPLACE INTO reports ("+",".join(cols)+") VALUES ("+",".join("?" for _ in cols)+")",[row[k] for k in cols])
        con.commit()
    except (InvalidToken, ValueError, KeyError, TypeError, zlib.error, sqlite3.Error):
        con.rollback()
        session.pop("demo_workspace",None)

def encode_workspace():
    con=g.demo_db
    base=g.demo_baseline
    rows=_rows(con)
    if sum(k not in base for k in rows)>MAX_REPORTS:
        raise ValueError("Maksimal 4 laporan tambahan per browser. Hapus draft atau laporan demo yang tidak diperlukan.")
    state={"rows":[v for k,v in rows.items() if base.get(k)!=v],"removed":[k for k in base if k not in rows]}
    if not state["rows"] and not state["removed"]:
        return None
    token=_cipher().encrypt(zlib.compress(json.dumps(state,ensure_ascii=False,separators=(",",":")).encode(),9)).decode()
    if len(token)>MAX_TOKEN:
        raise ValueError("Kapasitas demo penuh. Hapus laporan atau ringkas deskripsi sebelum menyimpan.")
    return token

def close_workspace():
    con=g.pop("demo_db",None)
    if con is not None:
        con.close()
