import os
from datetime import datetime, timezone

import requests


BASE_URL = os.getenv("REMNAWAVE_BASE_URL", "").rstrip("/")
API_TOKEN = os.getenv("REMNAWAVE_API_TOKEN", "")

DEFAULT_SQUAD_UUID = "5d1b5e2f-f96b-44fe-ac9b-53670991f57d"


class RemnawaveAPIError(Exception):
    pass


def _headers():
    if not BASE_URL:
        raise RemnawaveAPIError("REMNAWAVE_BASE_URL is not configured")

    if not API_TOKEN:
        raise RemnawaveAPIError("REMNAWAVE_API_TOKEN is not configured")

    return {
        "Authorization": f"Bearer {API_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _request(method, path, **kwargs):
    url = f"{BASE_URL}/api{path}"

    try:
        response = requests.request(
            method,
            url,
            headers=_headers(),
            timeout=15,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise RemnawaveAPIError(str(exc)) from exc

    if not response.ok:
        raise RemnawaveAPIError(
            f"Remnawave API error {response.status_code}: {response.text}"
        )

    if not response.content:
        return None

    return response.json()


def _unwrap(data):
    if not isinstance(data, dict):
        return data

    if "response" in data:
        return data["response"]

    return data


def _format_datetime(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)

        value = value.astimezone(timezone.utc)

        return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    return value


def get_users():
    data = _request("GET", "/users")
    response = _unwrap(data)

    if isinstance(response, dict) and "users" in response:
        return response["users"]

    return response


def get_user_by_id(user_id):
    data = _request(
        "GET",
        f"/users/{int(user_id)}",
    )

    return _unwrap(data)


def get_user_by_username(username):
    data = _request(
        "GET",
        f"/users/by-username/{username}",
    )

    return _unwrap(data)


def create_user(username, expire_at):
    payload = {
        "username": username,
        "status": "ACTIVE",
        "trafficLimitBytes": 0,
        "trafficLimitStrategy": "NO_RESET",
        "expireAt": _format_datetime(expire_at),
        "activeInternalSquads": [
            DEFAULT_SQUAD_UUID,
        ],
    }

    data = _request(
        "POST",
        "/users",
        json=payload,
    )

    return _unwrap(data)


def update_user_expire_at(user_id, expire_at):
    payload = {
        "id": int(user_id),
        "expireAt": _format_datetime(expire_at),
    }

    data = _request(
        "PATCH",
        "/users",
        json=payload,
    )

    return _unwrap(data)


def disable_user(user_id):
    data = _request(
        "POST",
        f"/users/{int(user_id)}/actions/disable",
    )

    return _unwrap(data)


def enable_user(user_id):
    data = _request(
        "POST",
        f"/users/{int(user_id)}/actions/enable",
    )

    return _unwrap(data)


def delete_user(user_id):
    """
    Полностью удаляет пользователя из Remnawave по числовому ID.

    Используется при окончательной отвязке устройства.
    Для временной блокировки используется disable_user().
    """
    if not user_id:
        raise ValueError(
            "Не указан ID пользователя Remnawave."
        )

    data = _request(
        "DELETE",
        f"/users/{int(user_id)}",
    )

    return _unwrap(data)

def get_hosts():
    data = _request(
        "GET",
        "/hosts",
    )

    return _unwrap(data)


def get_nodes():
    data = _request(
        "GET",
        "/nodes",
    )

    return _unwrap(data)


def get_config_profile(profile_uuid):
    data = _request(
        "GET",
        f"/config-profiles/{profile_uuid}",
    )

    return _unwrap(data)



# =========================================================
# MULTI-SERVER REMNAWAVE CLIENT
# =========================================================

class RemnawaveClient:
    """
    API-клиент конкретного сервера Remnawave.

    Настройки берутся не из глобального .env,
    а из переданного объекта RemnawaveServer.
    """

    def __init__(self, server):
        if server is None:
            raise RemnawaveAPIError(
                "Не указан сервер Remnawave."
            )

        self.server = server
        self.base_url = (server.base_url or "").rstrip("/")
        self.api_token = server.api_token or ""
        self.squad_uuid = server.squad_uuid or ""

        if not self.base_url:
            raise RemnawaveAPIError(
                "У сервера Remnawave не указан адрес панели."
            )

        if not self.api_token:
            raise RemnawaveAPIError(
                "У сервера Remnawave не указан API-токен."
            )

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(self, method, path, **kwargs):
        url = f"{self.base_url}/api{path}"

        try:
            response = requests.request(
                method,
                url,
                headers=self._headers(),
                timeout=15,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise RemnawaveAPIError(
                f"{self.server.name}: {exc}"
            ) from exc

        if not response.ok:
            raise RemnawaveAPIError(
                f"{self.server.name}: "
                f"Remnawave API error "
                f"{response.status_code}: "
                f"{response.text}"
            )

        if not response.content:
            return None

        try:
            return response.json()
        except ValueError as exc:
            raise RemnawaveAPIError(
                f"{self.server.name}: "
                "Remnawave вернул некорректный JSON."
            ) from exc

    def get_users(self):
        data = self._request(
            "GET",
            "/users",
        )

        response = _unwrap(data)

        if (
            isinstance(response, dict)
            and "users" in response
        ):
            return response["users"]

        return response

    def get_user_by_id(self, user_id):
        data = self._request(
            "GET",
            f"/users/{int(user_id)}",
        )

        return _unwrap(data)

    def get_user_by_username(self, username):
        data = self._request(
            "GET",
            f"/users/by-username/{username}",
        )

        return _unwrap(data)

    def create_user(self, username, expire_at):
        if not self.squad_uuid:
            raise RemnawaveAPIError(
                f"{self.server.name}: "
                "не указан UUID внутренней группы."
            )

        payload = {
            "username": username,
            "status": "ACTIVE",
            "trafficLimitBytes": 0,
            "trafficLimitStrategy": "NO_RESET",
            "expireAt": _format_datetime(expire_at),
            "activeInternalSquads": [
                self.squad_uuid,
            ],
        }

        data = self._request(
            "POST",
            "/users",
            json=payload,
        )

        return _unwrap(data)

    def update_user_expire_at(
        self,
        user_id,
        expire_at,
    ):
        payload = {
            "id": int(user_id),
            "expireAt": _format_datetime(expire_at),
        }

        data = self._request(
            "PATCH",
            "/users",
            json=payload,
        )

        return _unwrap(data)

    def disable_user(self, user_id):
        data = self._request(
            "POST",
            f"/users/{int(user_id)}/actions/disable",
        )

        return _unwrap(data)

    def enable_user(self, user_id):
        data = self._request(
            "POST",
            f"/users/{int(user_id)}/actions/enable",
        )

        return _unwrap(data)

    def delete_user(self, user_id):
        if not user_id:
            raise ValueError(
                "Не указан ID пользователя Remnawave."
            )

        data = self._request(
            "DELETE",
            f"/users/{int(user_id)}",
        )

        return _unwrap(data)

    def get_hosts(self):
        data = self._request(
            "GET",
            "/hosts",
        )

        return _unwrap(data)

    def get_nodes(self):
        data = self._request(
            "GET",
            "/nodes",
        )

        return _unwrap(data)

    def get_config_profile(self, profile_uuid):
        data = self._request(
            "GET",
            f"/config-profiles/{profile_uuid}",
        )

        return _unwrap(data)


    def create_node(
        self,
        name,
        address,
        config_profile_uuid,
        inbound_uuid,
        country_code="",
        node_port=2222,
    ):
        """
        Создаёт Node в текущей панели Remnawave.
        """

        payload = {
            "name": name,
            "address": address,
            "port": int(node_port),
            "configProfile": {
                "activeConfigProfileUuid": str(
                    config_profile_uuid
                ),
                "activeInbounds": [
                    str(inbound_uuid),
                ],
            },
        }

        if country_code:
            payload["countryCode"] = (
                str(country_code).strip().upper()
            )

        data = self._request(
            "POST",
            "/nodes",
            json=payload,
        )

        return _unwrap(data)

    def create_host(
        self,
        remark,
        address,
        config_profile_uuid,
        inbound_uuid,
        port=443,
        fingerprint="firefox",
    ):
        """
        Создаёт Host для VLESS Reality.
        """

        payload = {
            "inbound": {
                "configProfileUuid": str(
                    config_profile_uuid
                ),
                "configProfileInboundUuid": str(
                    inbound_uuid
                ),
            },
            "remark": remark,
            "address": address,
            "port": int(port),
            "fingerprint": fingerprint,
        }

        data = self._request(
            "POST",
            "/hosts",
            json=payload,
        )

        return _unwrap(data)


# =========================================================
# REMNAWAVE NODE / HOST MANAGEMENT
# =========================================================

def _rw_get_node(self, node_uuid):
    if not node_uuid:
        raise ValueError("Не указан UUID ноды Remnawave.")

    data = self._request(
        "GET",
        f"/nodes/{node_uuid}",
    )

    return _unwrap(data)


def _rw_get_config_profiles(self):
    data = self._request(
        "GET",
        "/config-profiles",
    )

    response = _unwrap(data)

    if (
        isinstance(response, dict)
        and "configProfiles" in response
    ):
        return response["configProfiles"]

    return response


def _rw_create_node(
    self,
    name,
    address,
    config_profile_uuid,
    inbound_uuid,
    country_code="",
    node_port=2222,
):
    if not name:
        raise ValueError("Не указано название ноды.")

    if not address:
        raise ValueError("Не указан IP-адрес ноды.")

    if not config_profile_uuid:
        raise ValueError("Не указан UUID Config Profile.")

    if not inbound_uuid:
        raise ValueError("Не указан UUID Inbound.")

    payload = {
        "name": name,
        "address": address,
        "port": int(node_port),
        "configProfile": {
            "activeConfigProfileUuid": str(
                config_profile_uuid
            ),
            "activeInbounds": [
                str(inbound_uuid),
            ],
        },
    }

    if country_code:
        payload["countryCode"] = (
            str(country_code).strip().upper()
        )

    data = self._request(
        "POST",
        "/nodes",
        json=payload,
    )

    return _unwrap(data)

def _rw_create_host(
    self,
    remark,
    address,
    config_profile_uuid,
    inbound_uuid,
    port=443,
    fingerprint="firefox",
):
    if not remark:
        raise ValueError("Не указано название Host.")

    if not address:
        raise ValueError("Не указан адрес Host.")

    if not config_profile_uuid:
        raise ValueError("Не указан UUID Config Profile.")

    if not inbound_uuid:
        raise ValueError("Не указан UUID Inbound.")

    payload = {
        "inbound": {
            "configProfileUuid": config_profile_uuid,
            "configProfileInboundUuid": inbound_uuid,
        },
        "remark": remark,
        "address": address,
        "port": int(port),
        "fingerprint": fingerprint,
    }

    data = self._request(
        "POST",
        "/hosts",
        json=payload,
    )

    return _unwrap(data)


def _rw_delete_node(self, node_uuid):
    if not node_uuid:
        raise ValueError("Не указан UUID ноды Remnawave.")

    data = self._request(
        "DELETE",
        f"/nodes/{node_uuid}",
    )

    return _unwrap(data)


def _rw_delete_host(self, host_uuid):
    if not host_uuid:
        raise ValueError("Не указан UUID Host.")

    data = self._request(
        "DELETE",
        f"/hosts/{host_uuid}",
    )

    return _unwrap(data)


# Добавляем методы к существующему RemnawaveClient.
#
# Так мы пока не переписываем большой рабочий класс
# и не затрагиваем уже работающий provisioning пользователей.
RemnawaveClient.get_node = _rw_get_node
RemnawaveClient.get_config_profiles = _rw_get_config_profiles
RemnawaveClient.create_node = _rw_create_node
RemnawaveClient.create_host = _rw_create_host
RemnawaveClient.delete_node = _rw_delete_node
RemnawaveClient.delete_host = _rw_delete_host


# =========================================================
# REMNAWAVE NODE / HOST ENABLE / DISABLE
# =========================================================

def _rw_enable_node(self, node_uuid):
    if not node_uuid:
        raise ValueError("Не указан UUID ноды Remnawave.")

    data = self._request(
        "POST",
        f"/nodes/{node_uuid}/actions/enable",
    )

    return _unwrap(data)


def _rw_disable_node(self, node_uuid):
    if not node_uuid:
        raise ValueError("Не указан UUID ноды Remnawave.")

    data = self._request(
        "POST",
        f"/nodes/{node_uuid}/actions/disable",
    )

    return _unwrap(data)


def _rw_enable_hosts(self, host_uuids):
    uuids = [
        str(value)
        for value in host_uuids
        if value
    ]

    if not uuids:
        raise ValueError("Не указаны UUID Host.")

    data = self._request(
        "POST",
        "/hosts/bulk/enable",
        json={
            "uuids": uuids,
        },
    )

    return _unwrap(data)


def _rw_disable_hosts(self, host_uuids):
    uuids = [
        str(value)
        for value in host_uuids
        if value
    ]

    if not uuids:
        raise ValueError("Не указаны UUID Host.")

    data = self._request(
        "POST",
        "/hosts/bulk/disable",
        json={
            "uuids": uuids,
        },
    )

    return _unwrap(data)


RemnawaveClient.enable_node = _rw_enable_node
RemnawaveClient.disable_node = _rw_disable_node
RemnawaveClient.enable_hosts = _rw_enable_hosts
RemnawaveClient.disable_hosts = _rw_disable_hosts
