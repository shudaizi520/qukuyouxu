"""Read-only discovery of Plex Home and server-share recipient tokens."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import uuid

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
        profiles = self.registry.list_public()
        owner = self.registry.get(owner_profile_id)
        owner_account = owner.get("account") or {}
        owner_id = str(owner_account.get("id") or "").strip()
        owner_name = str(owner_account.get("username") or "").strip().casefold()
        display_name = str(owner_account.get("username") or owner.get("name") or "Plex 管理员")[:120]
        items = [{
            "id": owner_id, "title": display_name, "username": display_name,
            "kind": "owner", "kind_label": "管理员",
            "existing_profile_id": owner_profile_id, "archived_profile_id": "",
        }]
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
                matches = [item for item in profiles
                                if item.get("kind") == kind
                                and str((item.get("account") or {}).get("id") or "") == row_id
                                and str((item.get("server") or {}).get("machine") or "")
                                == str((owner.get("server") or {}).get("machine") or "")]
                profile = next((item for item in matches if item.get("enabled") is not False), None)
                if profile is None:
                    profile = matches[0] if matches else {}
                if not profile:
                    legacy_id = _recipient_profile_id(kind, row.get("id"))
                    profile = next((item for item in profiles if item.get("id") == legacy_id), {})
                profile_id = profile.get("id") or _recipient_profile_id(kind, row.get("id"))
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

    def _libraries(self, profile, token):
        token = str(token or "").strip()
        if len(token) < 8:
            raise RecipientUnsupported("Plex 用户授权无效")
        server = profile.get("server") or {}
        plex = self.client_factory(server.get("url"), token)
        identity = plex.identity()
        if str(identity.get("machine") or "") != str(server.get("machine") or ""):
            raise RecipientUnsupported("用户令牌对应的不是所选 Plex 服务器")
        rows = []
        for section in plex.sections():
            section_type = str(section.get("type") or "").strip().casefold()
            if section_type and section_type != "artist":
                continue
            section_id = str(section.get("id") or "").strip()
            if not section_id.isdigit():
                continue
            rows.append({
                "id": section_id,
                "name": str(section.get("title") or section.get("name") or "")[:160],
            })
        plex.playlists()
        return rows

    def _library_states(self, kind, account_id, machine, libraries):
        result = []
        for library in libraries:
            existing = self.registry.find_identity(kind, account_id, machine, library["id"])
            status = ("cleanup" if existing and not existing["enabled"] else
                      "added" if existing else "available")
            result.append({**library, "status": status})
        return result

    def list_profile_libraries(self, profile_id):
        profile = self.registry.get(profile_id)
        libraries = self._libraries(profile, profile.get("token"))
        return self._library_states(
            profile["kind"], (profile.get("account") or {}).get("id"),
            (profile.get("server") or {}).get("machine"), libraries,
        )

    def _recipient_access(self, owner, kind, user_id):
        user_id = str(user_id or "").strip()
        if kind == "home":
            return self._switch_home(owner, user_id)
        if kind == "shared":
            row = next((item for item in self._shared_records(owner)
                        if item["id"] == user_id), None)
            if not row:
                raise RecipientUnsupported("该用户没有所选服务器的共享访问令牌")
            return row["token"], {
                "id": row["id"],
                "username": row["username"] or row["title"],
            }
        raise ValueError("Plex 用户类型无效")

    def list_recipient_libraries(self, owner_profile_id, kind, user_id):
        owner = self._owner(owner_profile_id)
        token, account = self._recipient_access(owner, kind, user_id)
        candidate = {
            "kind": kind,
            "account": account,
            "server": dict(owner.get("server") or {}),
        }
        libraries = self._libraries(candidate, token)
        return {"account": dict(account), "libraries": self._library_states(
            kind, account.get("id"), (owner.get("server") or {}).get("machine"), libraries,
        )}

    def _validate(self, owner, token, library_id):
        library_id = str(library_id or "")
        if not library_id.isdigit():
            raise ValueError("音乐资料库无效")
        library = next((row for row in self._libraries(owner, token)
                        if row["id"] == library_id), None)
        if not library:
            raise RecipientUnsupported("接收用户无权访问所选音乐资料库")
        return library

    def _create(self, owner, kind, source_id, account, token, library_id):
        library = self._validate(owner, token, library_id)
        machine = str((owner.get("server") or {}).get("machine") or "")
        existing = self.registry.find_identity(kind, account.get("id"), machine, library["id"])
        if existing:
            if not existing["enabled"]:
                raise ValueError("该用户和曲库的旧档案尚未清理完成")
            return self.registry.refresh_access(existing["id"], token, enabled=True)

        sibling = next((row for row in self.registry.list_public(enabled_only=True)
                        if row.get("kind") == kind
                        and str((row.get("account") or {}).get("id") or "") == str(account.get("id") or "")
                        and str((row.get("server") or {}).get("machine") or "") == machine), None)
        if sibling:
            self.registry.refresh_access(sibling["id"], token)
            return self.registry.create_for_library(sibling["id"], library)

        profile_id = _recipient_profile_id(kind, source_id)
        try:
            current = self.registry.get(profile_id)
        except ValueError:
            current = None
        if current:
            profile_id = "p-" + uuid.uuid4().hex[:20]
        return self.registry.create(
            name=account.get("username") or ("Plex " + kind),
            kind=kind,
            profile_id=profile_id,
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
        token, account = self._recipient_access(owner, "shared", user_id)
        return self._create(owner, "shared", user_id, account, token, library_id)

    def select_profile_library(self, profile_id, library_id):
        from .profile_web import connection_is_protected
        from .scoped_store import ScopedStore

        profile = self.registry.get(profile_id)
        if profile.get("enabled") is False:
            raise ValueError("该用户已停用或正在移除")
        library_id = str(library_id or "").strip()
        library = next((row for row in self._libraries(profile, profile.get("token"))
                        if row["id"] == library_id), None)
        if not library:
            raise ValueError("当前 Plex 用户无权访问这个音乐资料库")
        if str((profile.get("library") or {}).get("id") or "") == library_id:
            refreshed = self.registry.refresh_access(profile_id, profile.get("token"))
            return {"profile": refreshed, "mode": "unchanged"}

        identity = (
            profile.get("kind"),
            (profile.get("account") or {}).get("id"),
            (profile.get("server") or {}).get("machine"),
            library_id,
        )
        existing = self.registry.find_identity(*identity)
        if existing:
            if not existing["enabled"]:
                raise ValueError("该用户和曲库的旧档案尚未清理完成")
            refreshed = self.registry.refresh_access(
                existing["id"], profile.get("token"), enabled=True
            )
            return {"profile": refreshed, "mode": "switched"}

        scoped = ScopedStore(self.store, profile_id)
        if str((profile.get("library") or {}).get("id") or "") or connection_is_protected(scoped):
            created = self.registry.create_for_library(profile_id, library)
            return {"profile": created, "mode": "created"}
        switched = self.registry.switch_unmanaged_library(profile_id, library)
        return {"profile": switched, "mode": "switched"}
