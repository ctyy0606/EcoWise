import sqlite3
import os

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'energy_log.db')
conn = sqlite3.connect(db_path)

conn.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, nickname TEXT NOT NULL DEFAULT "", password_hash TEXT NOT NULL, created_at TEXT NOT NULL)')
conn.execute('CREATE TABLE IF NOT EXISTS energy_records (id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, energy_wh REAL NOT NULL, recorded_at TEXT NOT NULL, record_date TEXT NOT NULL, record_hour INTEGER NOT NULL, power_w REAL, light_level REAL)')
conn.execute('CREATE TABLE IF NOT EXISTS schedule_events (id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, user_phone TEXT, event_type TEXT NOT NULL, event_time TEXT NOT NULL, record_date TEXT NOT NULL)')
conn.execute('CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, user_phone TEXT NOT NULL, notify_type TEXT NOT NULL, title TEXT NOT NULL, message TEXT NOT NULL, device_id TEXT, created_at TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0)')
conn.commit()
conn.close()
print('Database tables created successfully!')
