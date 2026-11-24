"""Password verification and revocable, database-backed bearer sessions."""
import hashlib
import hmac
import secrets
from datetime import timedelta

from .db import iso, now, one, transaction, uid
from .errors import ApiError

ITERATIONS = 600_000
WINDOW = timedelta(minutes=15)


def hash_password(password):
    salt = secrets.token_bytes(16)
    value = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ITERATIONS)
    return f'pbkdf2_sha256${ITERATIONS}${salt.hex()}${value.hex()}'


def verify_password(password, encoded):
    try:
        algorithm, rounds, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256' or not 100_000 <= int(rounds) <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, AttributeError, TypeError):
        return False


# Used for unknown accounts too, without revealing whether an account exists.
DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def login(db, account_id, password, peer, ttl=3600):
    t = now()
    scopes = [('account:' + token_hash(account_id), 5), ('peer:' + token_hash(peer), 30)]
    for key, limit in scopes:
        row = one(db, 'SELECT * FROM auth_login_attempts WHERE scope_key=?', (key,))
        if row and row['window_started_at'] > iso(t-WINDOW) and row['fail_count'] >= limit:
            raise ApiError(429, 'LOGIN_RATE_LIMITED', '로그인 시도가 많습니다. 최대 15분 후 다시 시도하세요.')
    user = one(db, 'SELECT * FROM users WHERE account_id=?', (account_id,))
    encoded = user['password_hash'] if user and user['password_hash'] else DUMMY_HASH
    valid = verify_password(password, encoded)
    if not user or not user['password_hash'] or not valid:
        with transaction(db):
            db.execute('DELETE FROM auth_login_attempts WHERE window_started_at<=?', (iso(t-WINDOW),))
            for key, _ in scopes:
                db.execute('INSERT INTO auth_login_attempts VALUES (?,?,?) ON CONFLICT(scope_key) DO UPDATE SET fail_count=fail_count+1', (key, 1, iso(t)))
        raise ApiError(401, 'INVALID_CREDENTIALS', '아이디 또는 비밀번호를 확인하세요.')
    token = secrets.token_urlsafe(32)
    expires = iso(t+timedelta(seconds=ttl))
    with transaction(db):
        db.execute('DELETE FROM auth_login_attempts WHERE scope_key=?', (scopes[0][0],))
        db.execute('DELETE FROM auth_sessions WHERE expires_at<=?', (iso(t),))
        db.execute('INSERT INTO auth_sessions VALUES (?,?,?,?,?,NULL)', (uid(), user['user_id'], token_hash(token), iso(t), expires))
    return dict(access_token=token, token_type='bearer', user_id=user['user_id'], role=user['role'], mock=False, expires_in=ttl, expires_at=expires)


def session_user(db, token):
    if not isinstance(token, str) or not token or len(token) > 256:
        raise ApiError(401, 'UNAUTHENTICATED', '다시 로그인하세요.')
    session = one(db, 'SELECT * FROM auth_sessions WHERE token_hash=?', (token_hash(token),))
    if not session or session['revoked_at'] or session['expires_at'] <= iso():
        raise ApiError(401, 'SESSION_EXPIRED', '로그인이 만료되었거나 로그아웃되었습니다. 다시 로그인하세요.')
    user = one(db, 'SELECT * FROM users WHERE user_id=?', (session['user_id'],))
    if not user:
        raise ApiError(401, 'UNAUTHENTICATED', '다시 로그인하세요.')
    return user


def logout(db, token):
    session = one(db, 'SELECT session_id FROM auth_sessions WHERE token_hash=?', (token_hash(token),))
    if not session:
        raise ApiError(401, 'UNAUTHENTICATED', '비밀번호로 로그인한 계정에서 사용하세요.')
    db.execute('UPDATE auth_sessions SET revoked_at=COALESCE(revoked_at,?) WHERE session_id=?', (iso(), session['session_id']))
