"""
股票监控系统 - 数据库模块
SQLite 数据库，管理股票列表、分组、预警规则、历史数据
"""
import sqlite3
import json
import os
import hashlib
import secrets
from datetime import datetime, date

try:
    from config import DB_PATH as CONFIG_DB_PATH
    DB_PATH = CONFIG_DB_PATH
except ImportError:
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_monitor.db")


def get_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """初始化数据库表"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.executescript("""
        -- 股票列表
        CREATE TABLE IF NOT EXISTS stocks (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            market TEXT NOT NULL DEFAULT 'A',
            tags TEXT DEFAULT '',
            added_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            is_active INTEGER NOT NULL DEFAULT 1
        );

        -- 股票分组
        CREATE TABLE IF NOT EXISTS stock_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            color TEXT DEFAULT '#3b82f6',
            sort_order INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        );

        -- 分组-股票关联
        CREATE TABLE IF NOT EXISTS group_members (
            group_id INTEGER NOT NULL,
            stock_code TEXT NOT NULL,
            added_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            PRIMARY KEY (group_id, stock_code),
            FOREIGN KEY (group_id) REFERENCES stock_groups(id) ON DELETE CASCADE,
            FOREIGN KEY (stock_code) REFERENCES stocks(code) ON DELETE CASCADE
        );

        -- 预警规则
        CREATE TABLE IF NOT EXISTS alert_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            rule_type TEXT NOT NULL,
            params TEXT NOT NULL DEFAULT '{}',
            enabled INTEGER NOT NULL DEFAULT 1,
            notify_feishu INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        );

        -- 预警规则-股票关联
        CREATE TABLE IF NOT EXISTS alert_stocks (
            rule_id INTEGER NOT NULL,
            stock_code TEXT NOT NULL,
            PRIMARY KEY (rule_id, stock_code),
            FOREIGN KEY (rule_id) REFERENCES alert_rules(id) ON DELETE CASCADE,
            FOREIGN KEY (stock_code) REFERENCES stocks(code) ON DELETE CASCADE
        );

        -- 历史行情数据 (日K)
        CREATE TABLE IF NOT EXISTS price_history (
            code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            amount REAL,
            change_pct REAL,
            PRIMARY KEY (code, trade_date)
        );

        -- 分钟级行情快照（用于实时监控）
        CREATE TABLE IF NOT EXISTS price_snapshots (
            code TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            price REAL,
            change_pct REAL,
            volume REAL,
            amount REAL,
            turnover REAL,
            PRIMARY KEY (code, timestamp)
        );

        -- 预警触发日志
        CREATE TABLE IF NOT EXISTS alert_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id INTEGER NOT NULL,
            rule_name TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            alert_msg TEXT NOT NULL,
            triggered_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            is_read INTEGER DEFAULT 0,
            FOREIGN KEY (rule_id) REFERENCES alert_rules(id) ON DELETE SET NULL
        );

        -- 用户表
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            last_login TEXT
        );

        -- JWT 黑名单 (登出后失效)
        CREATE TABLE IF NOT EXISTS jwt_blacklist (
            token_jti TEXT PRIMARY KEY,
            expired_at TEXT NOT NULL
        );

        -- 创建索引
        CREATE INDEX IF NOT EXISTS idx_price_history_code_date ON price_history(code, trade_date);
        CREATE INDEX IF NOT EXISTS idx_snapshots_code_ts ON price_snapshots(code, timestamp);
        CREATE INDEX IF NOT EXISTS idx_alert_logs_time ON alert_logs(triggered_at);
        CREATE INDEX IF NOT EXISTS idx_alert_logs_read ON alert_logs(is_read);
    """)

    conn.commit()
    
    # 为旧数据库添加 tags 列 (兼容迁移)
    try:
        conn.execute("ALTER TABLE stocks ADD COLUMN tags TEXT DEFAULT ''")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    conn.close()


# ============ 股票管理 ============

def get_all_stocks():
    """获取所有股票"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT code, name, market, added_at, is_active, tags FROM stocks WHERE is_active=1 ORDER BY code"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_stock_tags(code, tags):
    """更新股票标签"""
    conn = get_connection()
    conn.execute("UPDATE stocks SET tags=? WHERE code=?", (tags, code))
    conn.commit()
    conn.close()


def add_stock(code, name, market='A'):
    """添加股票"""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stocks (code, name, market, added_at, is_active) VALUES (?, ?, ?, datetime('now', 'localtime'), 1)",
            (code, name, market)
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"添加股票失败: {e}")
        return False
    finally:
        conn.close()


def remove_stock(code):
    """删除股票（软删除）"""
    conn = get_connection()
    conn.execute("UPDATE stocks SET is_active=0 WHERE code=?", (code,))
    conn.execute("DELETE FROM group_members WHERE stock_code=?", (code,))
    conn.execute("DELETE FROM alert_stocks WHERE stock_code=?", (code,))
    conn.commit()
    conn.close()


# ============ 分组管理 ============

def get_all_groups():
    """获取所有分组及成员"""
    conn = get_connection()
    groups = conn.execute(
        "SELECT id, name, color, sort_order FROM stock_groups ORDER BY sort_order, id"
    ).fetchall()
    result = []
    for g in groups:
        gd = dict(g)
        members = conn.execute(
            "SELECT gm.stock_code, s.name as stock_name, s.market "
            "FROM group_members gm JOIN stocks s ON gm.stock_code=s.code "
            "WHERE gm.group_id=? AND s.is_active=1 ORDER BY gm.stock_code",
            (gd['id'],)
        ).fetchall()
        gd['members'] = [dict(m) for m in members]
        result.append(gd)
    conn.close()
    return result


def create_group(name, color='#3b82f6'):
    """创建分组"""
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO stock_groups (name, color) VALUES (?, ?)", (name, color)
        )
        conn.commit()
        return cur.lastrowid
    except Exception as e:
        print(f"创建分组失败: {e}")
        return None
    finally:
        conn.close()


def delete_group(group_id):
    """删除分组"""
    conn = get_connection()
    conn.execute("DELETE FROM stock_groups WHERE id=?", (group_id,))
    conn.commit()
    conn.close()


def add_to_group(group_id, stock_code):
    """将股票加入分组"""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO group_members (group_id, stock_code) VALUES (?, ?)",
            (group_id, stock_code)
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"加入分组失败: {e}")
        return False
    finally:
        conn.close()


def remove_from_group(group_id, stock_code):
    """从分组移除股票"""
    conn = get_connection()
    conn.execute(
        "DELETE FROM group_members WHERE group_id=? AND stock_code=?", (group_id, stock_code)
    )
    conn.commit()
    conn.close()


def update_group(group_id, name=None, color=None):
    """更新分组信息"""
    conn = get_connection()
    if name:
        conn.execute("UPDATE stock_groups SET name=? WHERE id=?", (name, group_id))
    if color:
        conn.execute("UPDATE stock_groups SET color=? WHERE id=?", (color, group_id))
    conn.commit()
    conn.close()


# ============ 预警规则管理 ============

def get_all_alert_rules():
    """获取所有预警规则"""
    conn = get_connection()
    rules = conn.execute(
        "SELECT * FROM alert_rules WHERE enabled=1 ORDER BY id"
    ).fetchall()
    result = []
    for r in rules:
        rd = dict(r)
        rd['params'] = json.loads(rd['params']) if rd['params'] else {}
        stocks = conn.execute(
            "SELECT as2.stock_code, s.name as stock_name FROM alert_stocks as2 "
            "JOIN stocks s ON as2.stock_code=s.code WHERE as2.rule_id=?",
            (rd['id'],)
        ).fetchall()
        rd['stocks'] = [dict(s) for s in stocks]
        result.append(rd)
    conn.close()
    return result


def get_all_alert_rules_including_disabled():
    """获取所有预警规则（含已禁用的）"""
    conn = get_connection()
    rules = conn.execute(
        "SELECT * FROM alert_rules ORDER BY id"
    ).fetchall()
    result = []
    for r in rules:
        rd = dict(r)
        rd['params'] = json.loads(rd['params']) if rd['params'] else {}
        stocks = conn.execute(
            "SELECT as2.stock_code, s.name as stock_name FROM alert_stocks as2 "
            "JOIN stocks s ON as2.stock_code=s.code WHERE as2.rule_id=?",
            (rd['id'],)
        ).fetchall()
        rd['stocks'] = [dict(s) for s in stocks]
        result.append(rd)
    conn.close()
    return result


def get_alert_rule_by_id(rule_id):
    """获取单个预警规则"""
    conn = get_connection()
    r = conn.execute("SELECT * FROM alert_rules WHERE id=?", (rule_id,)).fetchone()
    if not r:
        conn.close()
        return None
    rd = dict(r)
    rd['params'] = json.loads(rd['params']) if rd['params'] else {}
    stocks = conn.execute(
        "SELECT as2.stock_code, s.name as stock_name FROM alert_stocks as2 "
        "JOIN stocks s ON as2.stock_code=s.code WHERE as2.rule_id=?",
        (rd['id'],)
    ).fetchall()
    rd['stocks'] = [dict(s) for s in stocks]
    conn.close()
    return rd


def create_alert_rule(name, rule_type, params, stock_codes, notify_feishu=0):
    """创建预警规则"""
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO alert_rules (name, rule_type, params, notify_feishu) VALUES (?, ?, ?, ?)",
            (name, rule_type, json.dumps(params, ensure_ascii=False), notify_feishu)
        )
        rule_id = cur.lastrowid
        for code in stock_codes:
            conn.execute(
                "INSERT OR IGNORE INTO alert_stocks (rule_id, stock_code) VALUES (?, ?)",
                (rule_id, code)
            )
        conn.commit()
        return rule_id
    except Exception as e:
        print(f"创建预警规则失败: {e}")
        return None
    finally:
        conn.close()


def update_alert_rule(rule_id, **kwargs):
    """更新预警规则"""
    conn = get_connection()
    if 'params' in kwargs:
        kwargs['params'] = json.dumps(kwargs['params'], ensure_ascii=False)
    fields = []
    values = []
    for k, v in kwargs.items():
        if k in ('name', 'rule_type', 'params', 'enabled', 'notify_feishu'):
            fields.append(f"{k}=?")
            values.append(v)
    if fields:
        values.append(rule_id)
        conn.execute(
            f"UPDATE alert_rules SET {', '.join(fields)}, updated_at=datetime('now', 'localtime') WHERE id=?",
            values
        )
        conn.commit()
    conn.close()


def update_alert_stocks(rule_id, stock_codes):
    """更新预警规则关联的股票"""
    conn = get_connection()
    # 删除旧关联
    conn.execute("DELETE FROM alert_stocks WHERE rule_id=?", (rule_id,))
    # 添加新关联
    for code in stock_codes:
        conn.execute(
            "INSERT OR IGNORE INTO alert_stocks (rule_id, stock_code) VALUES (?, ?)",
            (rule_id, code)
        )
    conn.commit()
    conn.close()


def delete_alert_rule(rule_id):
    """删除预警规则"""
    conn = get_connection()
    conn.execute("DELETE FROM alert_rules WHERE id=?", (rule_id,))
    conn.commit()
    conn.close()


def add_alert_log(rule_id, rule_name, stock_code, stock_name, alert_type, alert_msg):
    """记录预警触发"""
    conn = get_connection()
    conn.execute(
        "INSERT INTO alert_logs (rule_id, rule_name, stock_code, stock_name, alert_type, alert_msg) VALUES (?, ?, ?, ?, ?, ?)",
        (rule_id, rule_name, stock_code, stock_name, alert_type, alert_msg)
    )
    conn.commit()
    conn.close()


def get_recent_alerts(limit=50):
    """获取最近预警记录"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM alert_logs ORDER BY triggered_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_alert_read(log_id):
    """标记预警已读"""
    conn = get_connection()
    conn.execute("UPDATE alert_logs SET is_read=1 WHERE id=?", (log_id,))
    conn.commit()
    conn.close()


def get_unread_alert_count():
    """获取未读预警数量"""
    conn = get_connection()
    row = conn.execute("SELECT COUNT(*) as cnt FROM alert_logs WHERE is_read=0").fetchone()
    conn.close()
    return row['cnt'] if row else 0


# ============ 历史数据 ============

def save_price_history(records):
    """批量保存日K数据"""
    if not records:
        return
    conn = get_connection()
    conn.executemany(
        "INSERT OR REPLACE INTO price_history (code, trade_date, open, high, low, close, volume, amount, change_pct) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        records
    )
    conn.commit()
    conn.close()


def get_price_history(code, days=250):
    """获取个股历史日K数据"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT trade_date, open, high, low, close, volume, amount, change_pct "
        "FROM price_history WHERE code=? ORDER BY trade_date DESC LIMIT ?",
        (code, days)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows][::-1]  # 按时间正序返回


def save_snapshots(snapshots):
    """保存分钟级快照"""
    if not snapshots:
        return
    conn = get_connection()
    conn.executemany(
        "INSERT OR REPLACE INTO price_snapshots (code, timestamp, price, change_pct, volume, amount, turnover) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        snapshots
    )
    conn.commit()
    conn.close()


def cleanup_old_snapshots(keep_hours=24):
    """清理旧的快照数据"""
    conn = get_connection()
    conn.execute(
        "DELETE FROM price_snapshots WHERE timestamp < datetime('now', 'localtime', ?)",
        (f'-{keep_hours} hours',)
    )
    conn.commit()
    conn.close()


# ============ 用户认证 ============

def hash_password(password, salt=None):
    """密码哈希 PBKDF2-SHA256"""
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100000)
    return salt, dk.hex()


def create_user(username, password, role='user'):
    """创建用户"""
    conn = get_connection()
    try:
        salt, pw_hash = hash_password(password)
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, salt, role) VALUES (?, ?, ?, ?)",
            (username, pw_hash, salt, role)
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def verify_user(username, password):
    """验证用户密码，返回用户信息或None"""
    conn = get_connection()
    row = conn.execute(
        "SELECT id, username, password_hash, salt, role, is_active FROM users WHERE username=?",
        (username,)
    ).fetchone()
    conn.close()

    if not row:
        return None
    if not row['is_active']:
        return None

    _, pw_hash = hash_password(password, row['salt'])
    if pw_hash != row['password_hash']:
        return None

    # 更新最后登录时间
    conn = get_connection()
    conn.execute(
        "UPDATE users SET last_login=datetime('now', 'localtime') WHERE id=?",
        (row['id'],)
    )
    conn.commit()
    conn.close()

    return {
        'id': row['id'],
        'username': row['username'],
        'role': row['role'],
    }


def get_user_by_id(user_id):
    """根据ID获取用户"""
    conn = get_connection()
    row = conn.execute(
        "SELECT id, username, role, is_active, created_at, last_login FROM users WHERE id=?",
        (user_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def blacklist_token(jti, expired_at):
    """将JWT加入黑名单"""
    conn = get_connection()
    conn.execute(
        "INSERT INTO jwt_blacklist (token_jti, expired_at) VALUES (?, ?)",
        (jti, expired_at)
    )
    conn.commit()
    conn.close()
    # 清理过期黑名单
    conn = get_connection()
    conn.execute("DELETE FROM jwt_blacklist WHERE expired_at < datetime('now')")
    conn.commit()
    conn.close()


def is_token_blacklisted(jti):
    """检查JWT是否在黑名单中"""
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM jwt_blacklist WHERE token_jti=? AND expired_at > datetime('now')",
        (jti,)
    ).fetchone()
    conn.close()
    return row is not None


if __name__ == '__main__':
    init_db()
    print("数据库初始化完成")
