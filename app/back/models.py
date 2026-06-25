from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Enum as SAEnum
from bcrypt import hashpw, gensalt, checkpw

from permissions import all_perms, can_manage_users

db = SQLAlchemy()

class User(db.Model):
    """
    Core User model handling authentication and identity.
    """
    __tablename__ = "users"

    user_id = db.Column(db.Integer, primary_key=True)
    user_firstname = db.Column(db.String(100), nullable=False)
    user_surname = db.Column(db.String(100), nullable=False) # please ensure it is in capital letters (convention)
    user_mail = db.Column(db.String(150), nullable=False, unique=True) # MUST be hosted on tn.net for confidentiality purposes
    user_pwd = db.Column(db.String(255), nullable=False)
    user_permissions = db.Column(db.Integer, nullable=False)

    def __repr__(self):
        return f"User {self.user_surname} {self.user_firstname}"
    
    def check_password(self, pw_candidate: str) -> bool:
        """
        Verifies the password against the stored bcrypt hash.
        """
        if not self.user_pwd: 
            return False
        stored_hash = self.user_pwd

        if isinstance(stored_hash, str): 
            stored_hash = stored_hash.encode('utf-8')
        candidate_bytes = pw_candidate.encode('utf-8')
        
        try:
            return checkpw(candidate_bytes, stored_hash)
        except ValueError:
            return False
        
    def get_id(self):
        return self.user_id
    
    def can_manage_users(self) -> bool:
        return can_manage_users(self)
    
    def is_admin(self) -> bool:
        return self.user_permissions == all_perms




