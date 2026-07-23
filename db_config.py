"""
EcoWise - 数据库路径统一配置
=============================
所有需要访问 SQLite 数据库的模块都应从此文件导入 DB_PATH，
避免各模块单独维护路径导致不一致。
"""
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "energy_log.db")
DATA_DIR = os.path.dirname(DB_PATH)

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)
