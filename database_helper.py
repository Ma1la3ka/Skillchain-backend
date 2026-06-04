"""Database helper functions"""
import mysql.connector
from config import DB_CONFIG


def get_db():
    """Get database connection"""
    try:
        # Log connection attempt (for debugging on Render)
        print(f"\n[DB] Attempting connection to: {DB_CONFIG.get('host')}:{DB_CONFIG.get('port')} | DB: {DB_CONFIG.get('database')}")
        
        conn = mysql.connector.connect(**DB_CONFIG)
        print(f"[DB] ✓ Connected successfully")
        return conn
    except mysql.connector.Error as err:
        print(f"\n[DB] ✗ Connection failed!")
        print(f"[DB] Host: {DB_CONFIG.get('host')}")
        print(f"[DB] Port: {DB_CONFIG.get('port')}")
        print(f"[DB] User: {DB_CONFIG.get('user')}")
        print(f"[DB] Database: {DB_CONFIG.get('database')}")
        print(f"[DB] Error: {err}\n")
        raise
