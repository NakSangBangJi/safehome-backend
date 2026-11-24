"""Create passwords only for the three existing local practice accounts."""
import json
import os
import secrets
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from app.auth import hash_password, verify_password
from app.db import ADMIN, GUARDIAN, SENIOR_USER, connect, seed, transaction


def provision(db, destination):
    destination=Path(destination)
    saved=json.loads(destination.read_text(encoding='utf-8')) if destination.exists() else {}
    credentials={}
    with transaction(db):
        for account,user_id in [('guardian-demo',GUARDIAN),('senior-demo',SENIOR_USER),('admin-demo',ADMIN)]:
            row=db.execute('SELECT * FROM users WHERE user_id=? AND account_id=?',(user_id,account)).fetchone()
            if row is None:
                raise RuntimeError('Expected local demo account was not found')
            if not row['password_hash']:
                password=secrets.token_urlsafe(18)
                db.execute('UPDATE users SET password_hash=? WHERE user_id=?',(hash_password(password),user_id))
                credentials[account]=password
            elif account in saved and verify_password(saved[account],row['password_hash']):
                credentials[account]=saved[account]
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(credentials,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(destination)
    text=destination.with_suffix('.txt')
    text.write_text('로컬 연습용 계정입니다. 팀 공유·발표 자료에 첨부하지 마세요.\n\n'+ '\n\n'.join(f'아이디: {account}\n비밀번호: {password}' for account,password in credentials.items())+'\n',encoding='utf-8-sig')
    return text


if __name__=='__main__':
    path=os.getenv('DATABASE_PATH',str(ROOT/'data'/'mock.sqlite3'))
    db=connect(path)
    try:
        seed(db)
        print('Practice account file:',provision(db,ROOT/'data'/'login_demo_accounts.json'))
    finally:
        db.close()
