"""Read-only discovery of Plex Home and server-share recipient tokens."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import requests

from .clients import PlexClient
from .plex_identity import plex_headers


PLEX_TV = "https://plex.tv"


class RecipientUnsupported(ValueError):
    pass


def _plex_payload(response):
    try:
        value = response.json()
    except (ValueError, TypeError):
        try:
            root = ET.fromstring(response.content)
        except (ET.ParseError, TypeError, ValueError) as exc:
            raise RecipientUnsupported("Plex 用户接口返回格式不受支持") from exc
        root_tag = str(root.tag).rsplit("}", 1)[-1]
        if root_tag != "MediaContainer":
            raise RecipientUnsupported("Plex 用户接口返回格式不受支持")
        container = {}
        for child in root:
            tag = str(child.tag).rsplit("}", 1)[-1]
            if tag not in ("SharedServer", "User"):
                continue
            container.setdefault(tag, []).append(dict(child.attrib))
        value = {"MediaContainer": container}
    if not isinstance(value, (dict, list)):
        raise RecipientUnsupported("Plex 用户接口返回格式不受支持")
    return value


def _attr(row, name):
    if not isinstance(row, dict):
        return ""
    return row.get(name) if row.get(name) is not None else row.get("@" + name, "")


def _rows(data, name):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    direct = data.get(name) or data.get(name.lower())
    if isinstance(direct, list):
        return direct
    container = data.get("MediaContainer") or {}
    value = container.get(name) if isinstance(container, dict) else None
    if isinstance(value, list):
        return value
    return [value] if isinstance(value, dict) else []


def _recipient_profile_id(kind, source_id):
    safe_id = re.sub(r"[^a-z0-9_-]", "-", str(source_id).lower()).strip("-")[:32]
    if not safe_id:
        raise ValueError("接收用户标识无效")
    return f"{kind}-{safe_id}"


class PlexRecipientService:
    def __init__(self, store, registry, session=None, client_factory=None):
        self.store = store
        self.registry = registry
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.client_factory = client_factory or (
            lambda url, token: PlexClient(url, token, store=self.store)
        )

    def _owner(self, profile_id):
        owner = self.registry.get(profile_id)
        if owner.get("kind") != "owner" or not owner.get("token"):
            raise RecipientUnsupported("请选择已经完成官方授权的所有者档案")
        server = owner.get("server") or {}
        if not server.get("machine") or not server.get("url"):
            raise RecipientUnsupported("所有者档案还没有可验证的 Plex 服务器")
        return owner

    def _json(self, owner, method, path):
        try:
            response = self.session.request(
                method,
                PLEX_TV + path,
                headers=plex_headers(self.store, owner["token"]),
                timeout=(8, 25),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise RecipientUnsupported("暂时无法连接 Plex 用户服务") from exc
        if response.status_code not in (200, 201):
            if response.status_code in (401, 403):
                raise RecipientUnsupported("Plex 所有者授权无效或没有管理权限")
            raise RecipientUnsupported("当前 Plex 服务不支持这个用户操作")
        if len(response.content) > 2 * 1024 * 1024:
            raise RecipientUnsupported("Plex 用户响应超过安全限制")
        return _plex_payload(response)

    def list_home_users(self, owner_profile_id):
        owner = self._owner(owner_profile_id)
        data = self._json(owner, "GET", "/api/v2/home/users")
        result = []
        for row in _rows(data, "users") if isinstance(data, dict) else data:
            if not isinstance(row, dict):
                continue
            user_id = str(_attr(row, "id") or _attr(row, "uuid") or "").strip()
            title = str(_attr(row, "title") or _attr(row, "username") or "").strip()[:120]
            if user_id and title:
                result.append({
                    "id": user_id,
                    "title": title,
                    "username": str(_attr(row, "username") or title)[:120],
                    "admin": bool(_attr(row, "admin")),
                    "restricted": bool(_attr(row, "restricted")),
                    "guest": bool(_attr(row, "guest")),
                })
        return result

    def _switch_home(self, owner, user_id):
        user_id = str(user_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", user_id):
            raise ValueError("Plex Home 用户标识无效")
        data = self._json(owner, "POST", f"/api/v2/home/users/{user_id}/switch")
        token = str(data.get("authToken") or "").strip() if isinstance(data, dict) else ""
        if len(token) < 8:
            raise RecipientUnsupported("Plex Home 没有返回成员授权")
        account = {
            "id": str(data.get("id") or user_id),
            "username": str(data.get("username") or data.get("title") or user_id)[:120],
        }
        return token, account

    def _shared_records(self, owner):
        machine = str(owner["server"]["machine"])
        shares = self._json(owner, "GET", f"/api/servers/{machine}/shared_servers")
        users = self._json(owner, "GET", "/api/users")
        user_rows = _rows(users, "User")
        by_id = {
            str(_attr(row, "id")): {
                "id": str(_attr(row, "id")),
                "username": str(_attr(row, "username") or _attr(row, "title") or "")[:120],
                "title": str(_attr(row, "title") or _attr(row, "username") or "")[:120],
            }
            for row in user_rows if str(_attr(row, "id"))
        }
        result = []
        for row in _rows(shares, "SharedServer"):
            user_id = str(_attr(row, "userID") or "")
            token = str(_attr(row, "accessToken") or "")
            user = by_id.get(user_id)
            if user and len(token) >= 8:
                result.append({**user, "token": token})
        return result

    def list_shared_users(self, owner_profile_id):
        owner = self._owner(owner_profile_id)
        return [{key: row[key] for key in ("id", "username", "title")}
                for row in self._shared_records(owner)]

    def list_people(self, owner_profile_id):
        """Combine the two Plex relationship types without making the UI teach Plex internals."""
        profiles = {row["id"]: row for row in self.registry.list_public()}
        owner = self.registry.get(owner_profile_id)
        owner_account = owner.get("account") or {}
        owner_id = str(owner_account.get("id") or "").strip()
        owner_name = str(owner_account.get("username") or "").strip().casefold()
        items = []
        warnings = []
        sources = (
            ("home", "家庭成员", self.list_home_users),
            ("shared", "共享朋友", self.list_shared_users),
        )
        for kind, label, loader in sources:
            try:
                rows = loader(owner_profile_id)
            except RecipientUnsupported as exc:
                warnings.append(f"{label}：{exc}")
                continue
            for row in rows:
                row_id = str(row.get("id") or "").strip()
                row_name = str(row.get("username") or row.get("title") or "").strip().casefold()
                if (owner_id and row_id == owner_id) or (owner_name and row_name == owner_name):
                    continue
                profile_id = _recipient_profile_id(kind, row.get("id"))
                profile = profiles.get(profile_id) or {}
                enabled = bool(profile) and profile.get("enabled") is not False
                items.append({
                    "id": row_id,
                    "title": str(row.get("title") or row.get("username") or "Plex 用户")[:120],
                    "username": str(row.get("username") or row.get("title") or "")[:120],
                    "kind": kind,
                    "kind_label": label,
                    "existing_profile_id": profile_id if enabled else "",
                    "archived_profile_id": profile_id if profile and not enabled else "",
                })
        return {"items": items, "warnings": warnings}

    def _validate(self, owner, token, library_id):
        library_id = str(library_id or "")
        if not library_id.isdigit():
            raise ValueError("音乐资料库无效")
        plex = self.client_factory(owner["server"]["url"], token)
        identity = plex.identity()
        if str(identity.get("machine") or "") != str(owner["server"]["machine"]):
            raise RecipientUnsupported("接收用户令牌对应的不是所选 Plex 服务器")
        sections = plex.sections()
        library = next((row for row in sections if str(row.get("id")) == library_id), None)
        if not library:
            raise RecipientUnsupported("接收用户无权访问所选音乐资料库")
        plex.playlists()
        return {
            "id": library_id,
            "name": str(library.get("title") or library.get("name") or "")[:160],
        }

    def _create(self, owner, kind, source_id, account, token, library_id):
        library = self._validate(owner, token, library_id)
        return self.registry.create(
            name=account.get("username") or ("Plex " + kind),
            kind=kind,
            profile_id=_recipient_profile_id(kind, source_id),
            token=token,
            account=account,
            server=dict(owner["server"]),
            library=library,
        )

    def import_home_user(self, owner_profile_id, user_id, library_id):
        owner = self._owner(owner_profile_id)
        token, account = self._switch_home(owner, user_id)
        return self._create(owner, "home", user_id, account, token, library_id)

    def import_shared_user(self, owner_profile_id, user_id, library_id):
        owner = self._owner(owner_profile_id)
        user_id = str(user_id or "")
        row = next((item for item in self._shared_records(owner) if item["id"] == user_id), None)
        if not row:
            raise RecipientUnsupported("该用户没有所选服务器的共享访问令牌")
        account = {"id": row["id"], "username": row["username"] or row["title"]}
        return self._create(owner, "shared", user_id, account, row["token"], library_id)
