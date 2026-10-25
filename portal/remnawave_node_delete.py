from dataclasses import dataclass

from django.db import transaction

from .models import RemnawaveNode
from .remnawave import RemnawaveClient, RemnawaveAPIError


class RemnawaveNodeDeleteError(Exception):
    pass


@dataclass
class RemnawaveNodeDeleteResult:
    node_name: str
    server_name: str
    address: str

    remote_node_found: bool = False
    remote_host_found: bool = False

    remote_node_deleted: bool = False
    remote_host_deleted: bool = False

    local_deleted: bool = False


def _unwrap_collection(data, key):
    """
    Нормализует ответы Remnawave:
      [...]
      {"nodes": [...]}
      {"hosts": [...]}
      {"response": [...]}
      {"response": {"nodes": [...]}}
    """
    if data is None:
        return []

    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    if key in data and isinstance(data[key], list):
        return data[key]

    response = data.get("response")

    if isinstance(response, list):
        return response

    if isinstance(response, dict):
        value = response.get(key)
        if isinstance(value, list):
            return value

    return []


def inspect_remnawave_node_delete(node):
    """
    Только проверяет, что сейчас существует в Remnawave.
    НИЧЕГО НЕ УДАЛЯЕТ.
    """

    if not isinstance(node, RemnawaveNode):
        raise RemnawaveNodeDeleteError(
            "Передан некорректный объект ноды."
        )

    rw = RemnawaveClient(node.server)

    try:
        nodes = _unwrap_collection(
            rw.get_nodes(),
            "nodes",
        )
        hosts = _unwrap_collection(
            rw.get_hosts(),
            "hosts",
        )
    except Exception as exc:
        raise RemnawaveNodeDeleteError(
            f"{node.server.name}: "
            f"не удалось получить состояние Remnawave: {exc}"
        ) from exc

    # -----------------------------------------------------
    # NODE
    # Сначала UUID.
    # Если сохранённый UUID устарел — имя + IP.
    # -----------------------------------------------------

    remote_node = None

    if node.remnawave_uuid:
        remote_node = next(
            (
                item
                for item in nodes
                if str(item.get("uuid"))
                == str(node.remnawave_uuid)
            ),
            None,
        )

    if remote_node is None:
        candidates = [
            item
            for item in nodes
            if (
                item.get("name") == node.name
                and item.get("address") == node.address
            )
        ]

        if len(candidates) > 1:
            raise RemnawaveNodeDeleteError(
                "В Remnawave найдено несколько Node "
                f"с именем {node.name!r} и IP {node.address}. "
                "Автоматическое удаление остановлено."
            )

        if candidates:
            remote_node = candidates[0]

    # -----------------------------------------------------
    # HOST
    # Сначала UUID.
    # Если UUID устарел — remark + address + port.
    # -----------------------------------------------------

    remote_host = None

    if node.host_uuid:
        remote_host = next(
            (
                item
                for item in hosts
                if str(item.get("uuid"))
                == str(node.host_uuid)
            ),
            None,
        )

    if remote_host is None:
        candidates = [
            item
            for item in hosts
            if (
                item.get("remark") == node.name
                and item.get("address") == node.address
                and int(item.get("port") or 0)
                == int(node.vpn_port)
            )
        ]

        if len(candidates) > 1:
            raise RemnawaveNodeDeleteError(
                "В Remnawave найдено несколько Host "
                f"для {node.name!r} / {node.address}:"
                f"{node.vpn_port}. "
                "Автоматическое удаление остановлено."
            )

        if candidates:
            remote_host = candidates[0]

    return {
        "node": remote_node,
        "host": remote_host,
    }


def delete_remnawave_node(node):
    """
    Полное удаление ноды из управления порталом.

    Порядок:
      1. Проверяем текущее состояние Remnawave.
      2. Удаляем Host.
      3. Удаляем Node.
      4. Только затем удаляем локальную запись Django.

    Сам VPS и установленный remnanode НЕ затрагиваются.
    """

    if not isinstance(node, RemnawaveNode):
        raise RemnawaveNodeDeleteError(
            "Передан некорректный объект ноды."
        )

    server = node.server
    node_name = node.name
    address = node.address

    state = inspect_remnawave_node_delete(node)

    remote_node = state["node"]
    remote_host = state["host"]

    result = RemnawaveNodeDeleteResult(
        node_name=node_name,
        server_name=server.name,
        address=address,
        remote_node_found=remote_node is not None,
        remote_host_found=remote_host is not None,
    )

    rw = RemnawaveClient(server)

    # -----------------------------------------------------
    # Сначала HOST
    # -----------------------------------------------------

    if remote_host is not None:
        host_uuid = remote_host.get("uuid")

        if not host_uuid:
            raise RemnawaveNodeDeleteError(
                "Host найден, но Remnawave не вернул его UUID."
            )

        try:
            rw.delete_host(host_uuid)
        except Exception as exc:
            raise RemnawaveNodeDeleteError(
                f"Host {node_name} удалить не удалось: {exc}. "
                "Локальная запись Django сохранена."
            ) from exc

        result.remote_host_deleted = True

    # -----------------------------------------------------
    # Затем NODE
    # -----------------------------------------------------

    if remote_node is not None:
        node_uuid = remote_node.get("uuid")

        if not node_uuid:
            raise RemnawaveNodeDeleteError(
                "Node найдена, но Remnawave не вернул её UUID. "
                "Локальная запись Django сохранена."
            )

        try:
            rw.delete_node(node_uuid)
        except Exception as exc:
            raise RemnawaveNodeDeleteError(
                f"Host уже обработан, но Node {node_name} "
                f"удалить не удалось: {exc}. "
                "Локальная запись Django сохранена."
            ) from exc

        result.remote_node_deleted = True

    # -----------------------------------------------------
    # LOCAL DJANGO
    # -----------------------------------------------------

    try:
        with transaction.atomic():
            node.delete()
    except Exception as exc:
        raise RemnawaveNodeDeleteError(
            "Объекты Remnawave обработаны, "
            f"но локальную запись Django удалить не удалось: {exc}"
        ) from exc

    result.local_deleted = True

    return result
