"""Administrator authentication HTTP routes."""
from fastapi import Request
from fastapi.responses import JSONResponse

from .auth import COOKIE_NAME


def attach_auth_routes(app, auth, store, body, cookie_factory, rate_limiter) -> None:
    @app.get('/api/auth/status')
    def auth_status(req: Request):
        user = auth.session_user(req.cookies.get(COOKIE_NAME))
        credentials = auth.credentials()
        return {
            'authenticated': bool(user),
            'username': user or (credentials.get('username') if credentials else None),
            'setup_required': credentials is None,
            'bootstrap_required': False,
            'legacy_upgrade_required': False,
        }

    @app.post('/api/auth/setup')
    async def auth_setup(req: Request):
        rate_limiter(req)
        if not auth.setup_required():
            raise ValueError('管理员账户已经建立')
        data = await body(req)
        if str(data.get('password', '')) != str(data.get('confirm_password', '')):
            raise ValueError('两次输入的密码不一致')
        username = auth.create_account(data.get('username', 'admin'), data.get('password', ''))
        token, _ = auth.create_session(username)
        store.log('管理员账户已建立；旧版验证信息不再用于日常登录')
        return cookie_factory(
            JSONResponse({
                'authenticated': True,
                'username': username,
                'message': '管理员账户已建立',
            }),
            token,
            req,
        )

    @app.post('/api/auth/login')
    async def auth_login(req: Request):
        rate_limiter(req)
        data = await body(req)
        username = str(data.get('username', ''))
        password = str(data.get('password', ''))
        if not auth.verify(username, password):
            return JSONResponse({'error': '用户名或密码不正确'}, status_code=401)
        token, _ = auth.create_session(username)
        return cookie_factory(
            JSONResponse({'authenticated': True, 'username': username}), token, req
        )

    @app.post('/api/auth/logout')
    def auth_logout(req: Request):
        auth.revoke_session(req.cookies.get(COOKIE_NAME))
        response = JSONResponse({'message': '已退出'})
        response.delete_cookie(COOKIE_NAME, path='/')
        return response

    @app.post('/api/auth/password')
    async def auth_password(req: Request):
        user = auth.session_user(req.cookies.get(COOKIE_NAME))
        if not user:
            return JSONResponse({'error': '请先登录'}, status_code=401)
        data = await body(req)
        if str(data.get('new_password', '')) != str(data.get('confirm_password', '')):
            raise ValueError('两次输入的新密码不一致')
        token, _ = auth.change_password_with_session(
            user, data.get('current_password', ''), data.get('new_password', '')
        )
        store.log('管理员密码已修改，旧登录会话已撤销')
        return cookie_factory(
            JSONResponse({'message': '密码已更新', 'username': user}), token, req
        )
