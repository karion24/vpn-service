from dataclasses import dataclass

from .models import RemnawaveNode
from .remnawave import RemnawaveClient


class RemnawaveNodeLifecycleError(Exception):
    pass


@dataclass
class NodeLifecycleResult:
    node: RemnawaveNode
    action: str
    node_changed: bool
    host_changed: bool
    node_disabled: bool
    host_disabled: bool


def _as_list(value):
    if isinstance(value, list):
        return value

    if isinstance(value, dict):
        for key in ("nodes", "hosts"):
            if isinstance(value.get(key), list):
                return value[key]

    return []


def _find_remote_node(rw, node):
    nodes = _as_list(rw.get_nodes())

    # Сначала UUID — самый надёжный вариант.
    if node.remnawave_uuid:
        wanted = str(node.remnawave_uuid)

        for item in nodes:
            if str(item.get("uuid") or "") == wanted:
                return item

    # Затем IP + имя.
    for item in nodes:
        if (
            str(item.get("address") or "") == str(node.address)
            and str(item.get("name") or "") == str(node.name)
        ):
            return item

    return None


def _find_remote_host(rw, node):
    hosts = _as_list(rw.get_hosts())

    # Сначала UUID.
    if node.host_uuid:
        wanted = str(node.host_uuid)

        for item in hosts:
            if str(item.get("uuid") or "") == wanted:
                return item

    # Затем IP + remark.
    for item in hosts:
        if (
            str(item.get("address") or "") == str(node.address)
            and str(item.get("remark") or "") == str(node.name)
        ):
            return item

    return None


def set_remnawave_node_enabled(node, enabled):
    """
    Включает/выключает существующие Node + Host.

    Ничего не создаёт и ничего не удаляет.
    """

    if not isinstance(node, RemnawaveNode):
        raise TypeError("Ожидался объект RemnawaveNode.")

    rw = RemnawaveClient(node.server)

    remote_node = _find_remote_node(rw, node)
    remote_host = _find_remote_host(rw, node)

    if remote_node is None:
        raise RemnawaveNodeLifecycleError(
            f"Node {node.name} отсутствует в "
            f"{node.server.name}. Сначала выполните восстановление."
        )

    if remote_host is None:
        raise RemnawaveNodeLifecycleError(
            f"Host {node.name} отсутствует в "
            f"{node.server.name}. Сначала выполните восстановление."
        )

    remote_node_uuid = remote_node.get("uuid")
    remote_host_uuid = remote_host.get("uuid")

    if not remote_node_uuid:
        raise RemnawaveNodeLifecycleError(
            "Remnawave не вернул UUID Node."
        )

    if not remote_host_uuid:
        raise RemnawaveNodeLifecycleError(
            "Remnawave не вернул UUID Host."
        )

    node_disabled = bool(
        remote_node.get("isDisabled", False)
    )
    host_disabled = bool(
        remote_host.get("isDisabled", False)
    )

    node_changed = False
    host_changed = False

    if enabled:
        # Сначала поднимаем Node.
        if node_disabled:
            rw.enable_node(remote_node_uuid)
            node_changed = True

        # Затем возвращаем Host в подписки.
        if host_disabled:
            rw.enable_hosts([remote_host_uuid])
            host_changed = True

    else:
        # Сначала убираем Host из выдачи клиентам.
        if not host_disabled:
            rw.disable_hosts([remote_host_uuid])
            host_changed = True

        # Затем выключаем Node.
        if not node_disabled:
            rw.disable_node(remote_node_uuid)
            node_changed = True

    # Перечитываем реальное состояние после операции.
    remote_node = _find_remote_node(rw, node)
    remote_host = _find_remote_host(rw, node)

    if remote_node is None or remote_host is None:
        raise RemnawaveNodeLifecycleError(
            "После операции Node или Host не найден в Remnawave."
        )

    final_node_disabled = bool(
        remote_node.get("isDisabled", False)
    )
    final_host_disabled = bool(
        remote_host.get("isDisabled", False)
    )

    expected_disabled = not enabled

    if (
        final_node_disabled != expected_disabled
        or final_host_disabled != expected_disabled
    ):
        raise RemnawaveNodeLifecycleError(
            "Remnawave не подтвердил требуемое состояние. "
            f"Node disabled={final_node_disabled}; "
            f"Host disabled={final_host_disabled}."
        )

    # Обновляем UUID на случай, если ранее объект восстанавливался.
    node.remnawave_uuid = remote_node_uuid
    node.host_uuid = remote_host_uuid
    node.is_enabled = bool(enabled)

    node.save(
        update_fields=[
            "remnawave_uuid",
            "host_uuid",
            "is_enabled",
            "updated_at",
        ]
    )

    return NodeLifecycleResult(
        node=node,
        action="enabled" if enabled else "disabled",
        node_changed=node_changed,
        host_changed=host_changed,
        node_disabled=final_node_disabled,
        host_disabled=final_host_disabled,
    )
