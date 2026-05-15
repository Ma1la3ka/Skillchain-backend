"""Database helper functions"""
import mysql.connector
from config import DB_CONFIG


def get_db():
    """Get database connection"""
    return mysql.connector.connect(**DB_CONFIG)
