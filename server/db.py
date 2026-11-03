"""SQLite 存储：学生状态、告警流水、上传记录、审计事件。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS student (
        uname TEXT PRIMARY KEY, ip TEXT, first_seen INTEGER, last_seen INTEGER,
        endpoint TEXT, health TEXT, alert_count INTEGER DEFAULT 0,
        last_alerts TEXT, client_time INTEGER)""",
    """CREATE TABLE IF NOT EXISTS alert (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uname TEXT, ip TEXT, ts INTEGER, alert_id TEXT, text TEXT, meta TEXT)""",
    """CREATE TABLE IF NOT EXISTS event (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER, kind TEXT, uname TEXT, ip TEXT, detail TEXT)""",
    """CREATE TABLE IF NOT EXISTS upload (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uname TEXT, seq INTEGER, kind TEXT, name TEXT,
        size INTEGER, sha256 TEXT, ts INTEGER, storage TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_alert_uname ON alert(uname, ts)",
    "CREATE INDEX IF NOT EXISTS idx_event_ts ON event(ts)",
    "CREATE INDEX IF NOT EXISTS idx_upload_uname ON upload(uname, seq)",
]


class Store:
    EXTRA_COLUMNS = {
        "student": [("hb_seq", "INTEGER DEFAULT 0"), ("hb_last", "INTEGER DEFAULT 0"),
                    ("hb_gaps", "INTEGER DEFAULT 0"), ("fp", "TEXT"),
                    ("fp_ok", "INTEGER DEFAULT -1")],
    }

    def __init__(self, path: str):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(p)
        self._lock = threading.Lock()
        self._local = threading.local()
        with self._conn() as con:
            for sql in SCHEMA:
                con.execute(sql)
            self._migrate(con)

    def _migrate(self, con) -> None:
        for table, cols in self.EXTRA_COLUMNS.items():
            have = {r["name"] for r in con.execute("PRAGMA table_info(%s)" % table)}
            for name, decl in cols:
                if name not in have:
                    con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, decl))

    def _conn(self):
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
            con.row_factory = sqlite3.Row
            try:
                con.execute("PRAGMA journal_mode=WAL")
                con.execute("PRAGMA synchronous=NORMAL")
                con.execute("PRAGMA busy_timeout=15000")
            except Exception:
                pass
            self._local.con = con
        return con

    # -- 学生 -------------------------------------------------------
    def touch_student(self, uname: str, ip: str, endpoint: str = "") -> None:
        now = int(time.time())
        with self._lock, self._conn() as con:
            row = con.execute("SELECT uname FROM student WHERE uname=?", (uname,)).fetchone()
            if row:
                con.execute("UPDATE student SET ip=?, last_seen=?, endpoint=? WHERE uname=?",
                            (ip, now, endpoint, uname))
            else:
                con.execute("INSERT INTO student(uname, ip, first_seen, last_seen, endpoint)"
                            " VALUES(?,?,?,?,?)", (uname, ip, now, now, endpoint))

    def save_alerts(self, uname, ip, alerts, health, client_time=0) -> int:
        now = int(time.time())
        with self._lock, self._conn() as con:
            for item in alerts or []:
                con.execute("INSERT INTO alert(uname, ip, ts, alert_id, text, meta)"
                            " VALUES(?,?,?,?,?,?)",
                            (uname, ip, now, str(item.get("id") or ""),
                             str(item.get("text") or ""),
                             json.dumps(item.get("meta") or {}, ensure_ascii=False)))
            con.execute("UPDATE student SET last_seen=?, ip=?, health=?,"
                        " alert_count=alert_count+?, last_alerts=?, client_time=?"
                        " WHERE uname=?",
                        (now, ip, json.dumps(health or {}, ensure_ascii=False),
                         len(alerts or []),
                         json.dumps(alerts or [], ensure_ascii=False), client_time, uname))
        return len(alerts or [])

    def list_students(self) -> list:
        with self._conn() as con:
            rows = con.execute(
                "SELECT uname, ip, first_seen, last_seen, alert_count, health, last_alerts,"
                " hb_seq, hb_last, hb_gaps, fp, fp_ok FROM student ORDER BY uname").fetchall()
        out, now = [], int(time.time())
        for r in rows:
            d = dict(r)
            d["silent"] = now - int(d["last_seen"] or 0) if d["last_seen"] else -1
            d["online"] = 0 <= d["silent"] < 30
            d["health"] = _safe_json(d.get("health"), {})
            d["last_alerts"] = _safe_json(d.get("last_alerts"), [])
            out.append(d)
        return out

    def recent_alerts(self, limit=200, uname="") -> list:
        with self._conn() as con:
            if uname:
                rows = con.execute("SELECT * FROM alert WHERE uname=? ORDER BY id DESC LIMIT ?",
                                   (uname, limit)).fetchall()
            else:
                rows = con.execute("SELECT * FROM alert ORDER BY id DESC LIMIT ?",
                                   (limit,)).fetchall()
        return [dict(r) for r in rows]

    def alert_summary(self) -> list:
        with self._conn() as con:
            rows = con.execute(
                "SELECT substr(alert_id,1,instr(alert_id||'-','-')-1) kind, count(*) n"
                " FROM alert GROUP BY kind ORDER BY n DESC").fetchall()
        return [dict(r) for r in rows]

    def same_ip_students(self) -> list:
        with self._conn() as con:
            rows = con.execute(
                "SELECT ip, count(*) n, group_concat(uname) users FROM student"
                " GROUP BY ip HAVING n > 1 ORDER BY n DESC").fetchall()
        return [dict(r) for r in rows]

    # -- 心跳 / 指纹 -------------------------------------------------
    def record_heartbeat(self, uname: str, seq: int, ip: str) -> int:
        now, gap = int(time.time()), 0
        with self._lock, self._conn() as con:
            row = con.execute("SELECT hb_seq, hb_gaps FROM student WHERE uname=?",
                              (uname,)).fetchone()
            prev = int(row["hb_seq"] or 0) if row else 0
            total = int(row["hb_gaps"] or 0) if row else 0
            if prev and seq and seq > prev + 1:
                gap = seq - prev - 1
            con.execute("UPDATE student SET hb_seq=?, hb_last=?, hb_gaps=?, ip=?,"
                        " last_seen=? WHERE uname=?",
                        (seq, now, total + gap, ip, now, uname))
        return gap

    def stale_students(self, seconds: int = 40) -> list:
        threshold = int(time.time()) - seconds
        with self._conn() as con:
            rows = con.execute("SELECT uname, ip, last_seen, hb_last, hb_gaps FROM student"
                               " WHERE last_seen > 0 AND last_seen < ?",
                               (threshold,)).fetchall()
        return [dict(r) for r in rows]

    def set_fingerprint(self, uname: str, fp: str, ok: bool) -> None:
        with self._lock, self._conn() as con:
            con.execute("UPDATE student SET fp=?, fp_ok=? WHERE uname=?",
                        (fp, 1 if ok else 0, uname))

    # -- 上传 -------------------------------------------------------
    def record_upload(self, uname, seq, kind, name, size, sha256, storage) -> None:
        with self._lock, self._conn() as con:
            con.execute("INSERT INTO upload(uname, seq, kind, name, size, sha256, ts, storage)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (uname, seq, kind, name, size, sha256, int(time.time()), storage))
    def upload_stats(self) -> list:
        with self._conn() as con:
            rows = con.execute(
                "SELECT uname, count(*) n, min(seq) lo, max(seq) hi, sum(size) bytes,"
                " max(ts) last_ts FROM upload GROUP BY uname").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            expected = (d["hi"] - d["lo"] + 1) if d["hi"] and d["lo"] else 0
            d["gaps"] = max(0, expected - d["n"])
            out.append(d)
        return out

    def upload_gaps(self, uname: str) -> list:
        with self._conn() as con:
            rows = con.execute("SELECT seq FROM upload WHERE uname=? ORDER BY seq",
                               (uname,)).fetchall()
        have = [r["seq"] for r in rows]
        if not have:
            return []
        present = set(have)
        return [i for i in range(have[0], have[-1]) if i not in present]

    def log_event(self, kind, uname="", ip="", detail="") -> None:
        with self._lock, self._conn() as con:
            con.execute("INSERT INTO event(ts, kind, uname, ip, detail) VALUES(?,?,?,?,?)",
                        (int(time.time()), kind, uname, ip, detail))

    def recent_events(self, limit=200) -> list:
        with self._conn() as con:
            rows = con.execute("SELECT * FROM event ORDER BY id DESC LIMIT ?",
                               (limit,)).fetchall()
        return [dict(r) for r in rows]


def _safe_json(raw, default):
    if not raw:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default
