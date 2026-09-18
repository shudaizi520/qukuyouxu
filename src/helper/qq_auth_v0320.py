"""QQ Music QR login based on the maintained QQMusicApi flow."""
from __future__ import annotations

import re
import threading
import time
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import requests

QR_URL = "https://ssl.ptlogin2.qq.com/ptqrshow"
POLL_URL = "https://ssl.ptlogin2.qq.com/ptqrlogin"
CHECK_SIG_URL = "https://ssl.ptlogin2.graph.qq.com/check_sig"
AUTHORIZE_URL = "https://graph.qq.com/oauth2.0/authorize"
MUSICU_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
LOGIN_JUMP = "https://graph.qq.com/oauth2.0/login_jump"
REDIRECT_URI = "https://y.qq.com/portal/wx_redirect.html?login_type=1&surl=https://y.qq.com/"
APP_ID = "716027609"
DAID = "383"
THIRD_APP_ID = "100497308"
CALLBACK_RE = re.compile(r"ptuiCB\((.*?)\)")
ARG_RE = re.compile(r"'((?:\\.|[^'])*)'")

def _hash33(value, seed=0):
    result = int(seed)
    for char in str(value or ""):
        result += (result << 5) + ord(char)
    return result & 0x7FFFFFFF

class QQAuthError(RuntimeError):
    def __init__(self, stage, message):
        self.stage = str(stage)
        super().__init__(str(message)[:240])

def _http_error(stage, label, response=None):
    status = getattr(response, "status_code", None)
    suffix = f"（HTTP {status}）" if status is not None else ""
    return QQAuthError(stage, label + suffix)

class QQAuthManager:
    def __init__(self, store, qq_client, session_factory=requests.Session, start_background=True):
        self.store = store
        self.qq_client = qq_client
        self.session_factory = session_factory
        self.start_background = bool(start_background)
        self.lock = threading.RLock()
        self.session = None
        self.image = b""
        self.phase = "idle"
        self.message = "尚未连接 QQ 音乐"
        self.nickname = ""
        self.expires_at = 0.0
        self.generation = 0
        self._load_saved()

    def _load_saved(self):
        saved = self.store.get("qq_auth_credentials", {}) or {}
        credential = saved.get("credential", {}) if isinstance(saved, dict) else {}
        if self._valid_credential(credential):
            self._apply_credential(credential)
            self.phase = "connected"
            self.message = "QQ 音乐已授权"
            self.nickname = str(saved.get("nickname") or "")[:120]

    @staticmethod
    def _valid_credential(credential):
        if not isinstance(credential, dict):
            return False
        musicid = credential.get("musicid") or credential.get("str_musicid") or credential.get("strMusicid")
        return bool(str(musicid or "").isdigit() and credential.get("musickey"))

    def _apply_credential(self, credential):
        musicid = str(credential.get("str_musicid") or credential.get("strMusicid") or credential.get("musicid"))
        musickey = str(credential.get("musickey"))
        self.qq_client.session.cookies.update({
            "uin": musicid, "qqmusic_uin": musicid,
            "qm_keyst": musickey, "qqmusic_key": musickey,
        })

    def public_status(self):
        with self.lock:
            logged_in = self.phase == "connected"
            return {
                "logged_in": logged_in,
                "phase": self.phase,
                "message": self.message,
                "nickname": self.nickname if logged_in else "",
                "expires_at": self.expires_at if self.phase in {"qr_ready", "scanned", "confirming"} else 0,
            }

    def start(self):
        session = self.session_factory()
        if hasattr(session, "trust_env"):
            session.trust_env = False
        if hasattr(session, "headers"):
            session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://xui.ptlogin2.qq.com/"})
        response = None
        try:
            response = session.get(QR_URL, params={
                "appid": APP_ID, "e": "2", "l": "M", "s": "3", "d": "72", "v": "4",
                "t": str(time.time()), "daid": DAID, "pt_3rd_aid": THIRD_APP_ID,
            }, timeout=(8, 20))
            response.raise_for_status()
        except Exception:
            if hasattr(session, "close"):
                session.close()
            raise _http_error("qrcode", "QQ 音乐二维码服务不可用", response) from None
        image = bytes(response.content or b"")
        qrsig = response.cookies.get("qrsig") or session.cookies.get("qrsig")
        if not image.startswith(b"\x89PNG\r\n\x1a\n") or not qrsig or len(image) > 1024 * 1024:
            if hasattr(session, "close"):
                session.close()
            raise QQAuthError("qrcode", "QQ 音乐没有返回有效二维码")
        with self.lock:
            old = self.session
            self.session = session
            self.image = image
            self.phase = "qr_ready"
            self.message = "请使用手机 QQ 扫码并确认"
            self.nickname = ""
            self.expires_at = time.time() + 120
            self.generation += 1
            generation = self.generation
        if old is not None and old is not session and hasattr(old, "close"):
            old.close()
        if self.start_background:
            threading.Thread(target=self._poll, args=(generation,), daemon=True, name="qqmusic-qr-login").start()
        return self.public_status()

    def _qqmusic_login(self, session, code):
        payload = {
            "comm": {
                "ct": 24, "cv": 4747474, "platform": "yqq.json", "chid": "0", "uin": 0,
                "g_tk": 5381, "g_tk_new_20200303": 5381, "format": "json",
                "inCharset": "utf-8", "outCharset": "utf-8", "notice": 0,
                "needNewCode": 1, "tmeLoginType": 2,
            },
            "req_0": {"module": "QQConnectLogin.LoginServer", "method": "QQLogin", "param": {"code": code}},
        }
        response = None
        try:
            response = session.post(MUSICU_URL, json=payload, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://y.qq.com/", "Accept": "application/json",
            }, timeout=(8, 25), allow_redirects=False)
            response.raise_for_status()
            document = response.json()
        except Exception:
            raise _http_error("qqmusic", "QQ 音乐登录凭据交换失败", response) from None
        result = document.get("req_0", {}) if isinstance(document, dict) else {}
        if document.get("code", 0) != 0 or result.get("code", 0) != 0:
            code_value = result.get("code", document.get("code", "未知"))
            raise QQAuthError("qqmusic", f"QQ 音乐拒绝登录（代码 {code_value}）")
        data = result.get("data", {})
        if not self._valid_credential(data):
            raise QQAuthError("qqmusic", "QQ 音乐登录成功响应缺少有效凭据")
        allowed = (
            "openid", "refresh_token", "access_token", "expired_at", "expired_in", "musicid",
            "musickey", "unionid", "str_musicid", "strMusicid", "refresh_key", "loginType",
            "musickeyCreateTime", "keyExpiresIn", "encryptUin",
        )
        return {key: data[key] for key in allowed if key in data and len(str(data[key])) <= 4096}

    def logout(self):
        with self.lock:
            self.generation += 1
            self.phase, self.message, self.nickname = "idle", "尚未连接 QQ 音乐", ""
            self.image, self.expires_at = b"", 0.0
            session, self.session = self.session, None
        self.store.set("qq_auth_credentials", None)
        for name in ("uin", "qqmusic_uin", "qm_keyst", "qqmusic_key"):
            try:
                self.qq_client.session.cookies.pop(name, None)
            except Exception:
                pass
        if session is not None and hasattr(session, "close"):
            session.close()
        return self.public_status()

    def _authorize(self, session, uin, sigx):
        response = None
        try:
            response = session.get(CHECK_SIG_URL, params={
                "uin": uin, "pttype": "1", "service": "ptqrlogin", "nodirect": "0",
                "ptsigx": sigx, "s_url": LOGIN_JUMP, "ptlang": "2052", "ptredirect": "100",
                "aid": APP_ID, "daid": DAID, "j_later": "0", "low_login_hour": "0",
                "regmaster": "0", "pt_login_type": "3", "pt_aid": "0", "pt_aaid": "16",
                "pt_light": "0", "pt_3rd_aid": THIRD_APP_ID,
            }, headers={"Referer": "https://xui.ptlogin2.qq.com/"}, timeout=(8, 20), allow_redirects=False)
            response.raise_for_status()
        except Exception:
            raise _http_error("check_sig", "QQ 登录确认失败", response) from None
        cookies = response.cookies
        p_skey = None
        for name in ("p_skey", "p-skey", "pskey", "ptsigx", "skey"):
            p_skey = cookies.get(name) or session.cookies.get(name)
            if p_skey:
                break
        if not p_skey:
            raise QQAuthError("check_sig", "QQ 登录确认未返回授权凭据")
        response = None
        try:
            response = session.post(AUTHORIZE_URL, data={
                "response_type": "code", "client_id": THIRD_APP_ID, "redirect_uri": REDIRECT_URI,
                "scope": "get_user_info,get_app_friends", "state": "state", "switch": "",
                "from_ptlogin": "1", "src": "1", "update_auth": "1", "openapi": "1010_1030",
                "g_tk": str(_hash33(p_skey, 5381)), "auth_time": str(int(time.time() * 1000)),
                "ui": str(uuid4()),
            }, headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": "https://xui.ptlogin2.qq.com/"}, timeout=(8, 20), allow_redirects=False)
            response.raise_for_status()
        except Exception:
            raise _http_error("oauth", "QQ 音乐授权请求失败", response) from None
        location = response.headers.get("Location") or response.headers.get("location") or ""
        code = (parse_qs(urlparse(location).query).get("code") or [""])[0]
        if not code:
            raise QQAuthError("oauth", "QQ 音乐授权没有返回登录代码")
        return self._qqmusic_login(session, code)

    def qrcode(self):
        with self.lock:
            if self.phase not in {"qr_ready", "scanned", "confirming"} or not self.image:
                raise ValueError("QQ 音乐二维码不存在或已经过期")
            return self.image

    def _poll(self, generation):
        while True:
            with self.lock:
                if generation != self.generation or self.phase not in {"qr_ready", "scanned", "confirming"}:
                    return
                if time.time() >= self.expires_at:
                    self.phase, self.message = "expired", "二维码已过期，请重新扫码"
                    return
            try:
                self.check_once()
            except QQAuthError as exc:
                with self.lock:
                    if generation == self.generation:
                        self.phase, self.message = "error", str(exc)
                return
            except Exception:
                with self.lock:
                    if generation == self.generation:
                        self.phase, self.message = "error", "QQ 音乐授权发生未知错误，请重新扫码"
                return
            time.sleep(2.5)

    def check_once(self):
        with self.lock:
            session = self.session
            qrsig = session.cookies.get("qrsig") if session is not None else ""
            generation = self.generation
        if not session or not qrsig:
            raise QQAuthError("poll", "请先生成 QQ 音乐二维码")
        response = None
        try:
            response = session.get(POLL_URL, params={
                "u1": LOGIN_JUMP, "ptqrtoken": str(_hash33(qrsig)), "ptredirect": "0",
                "h": "1", "t": "1", "g": "1", "from_ui": "1", "ptlang": "2052",
                "action": "0-0-" + str(int(time.time() * 1000)), "js_ver": "20102616",
                "js_type": "1", "pt_uistyle": "40", "aid": APP_ID, "daid": DAID,
                "pt_3rd_aid": THIRD_APP_ID, "has_onekey": "1",
            }, headers={"Referer": "https://xui.ptlogin2.qq.com/"}, cookies={"qrsig": qrsig}, timeout=(8, 20), allow_redirects=False)
            response.raise_for_status()
        except Exception:
            raise _http_error("poll", "QQ 音乐扫码状态查询失败", response) from None
        match = CALLBACK_RE.search(str(response.text or ""))
        args = ARG_RE.findall(match.group(1)) if match else []
        if not args or not args[0].isdigit():
            raise QQAuthError("poll", "QQ 音乐扫码状态响应无法识别")
        code = args[0]
        states = {
            "66": ("qr_ready", "请使用手机 QQ 扫码并确认"),
            "67": ("scanned", "已扫码，请在手机 QQ 上确认"),
            "65": ("expired", "二维码已过期，请重新扫码"),
            "68": ("expired", "授权已取消，请重新扫码"),
        }
        if code != "0":
            fallback = args[4] if len(args) > 4 else "QQ 音乐授权失败"
            phase, message = states.get(code, ("error", fallback[:160]))
            with self.lock:
                if generation == self.generation:
                    self.phase, self.message = phase, message
            return self.public_status()
        if len(args) < 3:
            raise QQAuthError("poll", "QQ 音乐登录参数不完整")
        query = parse_qs(urlparse(args[2]).query)
        sigx = (query.get("ptsigx") or [""])[0]
        uin = (query.get("uin") or [""])[0]
        if not sigx or not uin:
            raise QQAuthError("poll", "QQ 音乐登录参数无法识别")
        with self.lock:
            if generation == self.generation:
                self.phase, self.message = "confirming", "正在完成 QQ 音乐授权"
        credential = self._authorize(session, uin, sigx)
        nickname = args[5] if len(args) > 5 else ""
        saved = {"credential": credential, "nickname": str(nickname)[:120], "saved_at": time.time()}
        self.store.set("qq_auth_credentials", saved)
        self._apply_credential(credential)
        with self.lock:
            if generation == self.generation:
                self.phase, self.message = "connected", "QQ 音乐已授权"
                self.nickname, self.image, self.expires_at = saved["nickname"], b"", 0.0
        return self.public_status()
