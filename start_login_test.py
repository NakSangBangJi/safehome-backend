"""Prepare local practice passwords, then start the usual local server."""
import os
from pathlib import Path

import uvicorn
from app.db import connect, seed
from tools.setup_login_demo import provision

ROOT=Path(__file__).resolve().parent

if __name__=='__main__':
    db=connect(os.getenv('DATABASE_PATH',str(ROOT/'data'/'mock.sqlite3')))
    try:
        seed(db)
        path=provision(db,ROOT/'data'/'login_demo_accounts.json')
    finally:
        db.close()
    print('연습용 아이디와 비밀번호 파일:',path)
    print('로그인 확인 화면: http://127.0.0.1:8000/login-test')
    uvicorn.run('app.main:app',host='127.0.0.1',port=8000,workers=1)
