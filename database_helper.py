"""Database helper functions"""
import platform
import mysql.connector
from config import DB_CONFIG

def get_db():
    """Get database connection"""
    try:
        # Log connection attempt (for debugging on Render)
        print(f"\n[DB] Attempting connection to: {DB_CONFIG.get('host')}:{DB_CONFIG.get('port')} | DB: {DB_CONFIG.get('database')}")
        
        # Point to Render's Linux certificate path, but leave it empty for your local Windows machine
        ca_path = "/etc/ssl/certs/ca-certificates.crt" if platform.system() == "Linux" else None

        # --- TIDB SSL CONNECTION FIX ---
        conn = mysql.connector.connect(
            **DB_CONFIG,
            ssl_disabled=False,
            ssl_verify_identity=True,
            ssl_ca=ca_path
        )
        
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