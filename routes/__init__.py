"""Routes package for SkillChain Backend — updated to include switch_role"""
from flask import Blueprint

from .auth         import auth_bp
from .worker       import worker_bp
from .client       import client_bp
from .jobs         import jobs_bp
from .verification import verification_bp
from .media        import media_bp
from .profile      import profile_bp
from .webhook      import webhook_bp
from .banks        import banks_bp
from .switch_role  import switch_bp   
from .chat import chat_bp
from .heartbeat import heartbeat_bp

blueprints = [
    auth_bp,
    worker_bp,
    client_bp,
    jobs_bp,
    verification_bp,
    media_bp,
    profile_bp,
    webhook_bp,
    banks_bp,
    switch_bp,  
    chat_bp,
    heartbeat_bp
    
]