"""Password hashes for the independent operator login; no WeChat/admin secret reuse."""
import hashlib
import hmac
import secrets

def password_hash(password):
    salt=secrets.token_hex(16)
    value=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
    return 'scrypt:'+salt+':'+value

def verify_password(password,encoded):
    try:
        scheme,salt,value=encoded.split(':')
        if scheme!='scrypt': return False
        actual=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
        return hmac.compare_digest(value,actual)
    except (ValueError,TypeError): return False
