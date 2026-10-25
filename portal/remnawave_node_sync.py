from django.utils import timezone

from .models import RemnawaveNode, RemnawaveServer
from .remnawave import RemnawaveClient


def sync_remnawave_nodes(server=None):
    """
    Синхронизирует ноды Remnawave -> Django.

    Если server передан — синхронизируется одна панель.
    Если server=None — все активные панели.

    Ошибка одной панели не останавливает остальные.
    """

    if server is None:
        servers = (
            RemnawaveServer.objects
            .filter(is_active=True)
            .order_by("priority", "id")
        )
    else:
        servers = [server]

    result = {
        "servers": 0,
        "created": 0,
        "updated": 0,
        "errors": [],
        "nodes": [],
    }

    for current_server in servers:
        result["servers"] += 1

        try:
            rw = RemnawaveClient(current_server)
            remote_nodes = rw.get_nodes() or []
            remote_hosts = rw.get_hosts() or []

            for remote in remote_nodes:
                node_uuid = remote.get("uuid")

                if not node_uuid:
                    continue

                config = remote.get("configProfile") or {}

                profile_uuid = config.get(
                    "activeConfigProfileUuid"
                )

                active_inbounds = (
                    config.get("activeInbounds") or []
                )

                inbound_uuid = None
                vpn_port = 443

                # Сначала ищем VLESS Reality.
                for inbound in active_inbounds:
                    if (
                        inbound.get("type") == "vless"
                        and inbound.get("security") == "reality"
                    ):
                        inbound_uuid = inbound.get("uuid")
                        vpn_port = inbound.get("port") or 443
                        break

                # Fallback на первый активный inbound.
                if not inbound_uuid and active_inbounds:
                    inbound = active_inbounds[0]
                    inbound_uuid = inbound.get("uuid")
                    vpn_port = inbound.get("port") or 443

                versions = remote.get("versions") or {}

                # -----------------------------------------
                # Host <-> Node
                # -----------------------------------------

                host_uuid = None

                node_address = (
                    remote.get("address") or ""
                ).strip()

                for host in remote_hosts:
                    host_address = (
                        host.get("address") or ""
                    ).strip()

                    if host_address != node_address:
                        continue

                    host_inbound = (
                        host.get("inbound") or {}
                    )

                    host_profile_uuid = (
                        host_inbound.get(
                            "configProfileUuid"
                        )
                    )

                    host_inbound_uuid = (
                        host_inbound.get(
                            "configProfileInboundUuid"
                        )
                    )

                    # Host должен относиться к тому же
                    # Config Profile / Inbound.
                    if (
                        profile_uuid
                        and host_profile_uuid
                        and host_profile_uuid
                        != profile_uuid
                    ):
                        continue

                    if (
                        inbound_uuid
                        and host_inbound_uuid
                        and host_inbound_uuid
                        != inbound_uuid
                    ):
                        continue

                    host_port = (
                        host.get("port") or 443
                    )

                    if int(host_port) != int(vpn_port):
                        continue

                    host_uuid = host.get("uuid")
                    break

                defaults = {
                    "server": current_server,
                    "name": (
                        remote.get("name")
                        or f"Node-{remote.get('id')}"
                    ),
                    "address": remote.get("address"),
                    "country_code": (
                        remote.get("countryCode") or ""
                    )[:2].upper(),
                    "node_port": remote.get("port") or 2222,
                    "vpn_port": vpn_port,
                    "remnawave_id": remote.get("id"),
                    "config_profile_uuid": profile_uuid,
                    "inbound_uuid": inbound_uuid,
                    "host_uuid": host_uuid,
                    "is_enabled": not bool(
                        remote.get("isDisabled")
                    ),
                    "is_connected": bool(
                        remote.get("isConnected")
                    ),
                    "last_status_message": (
                        remote.get("lastStatusMessage") or ""
                    ),
                    "xray_uptime": (
                        remote.get("xrayUptime") or 0
                    ),
                    "xray_version": (
                        versions.get("xray") or ""
                    ),
                    "node_version": (
                        versions.get("node") or ""
                    ),
                    "users_online": (
                        remote.get("usersOnline") or 0
                    ),
                    "last_sync_at": timezone.now(),
                }

                node, created = (
                    RemnawaveNode.objects.update_or_create(
                        remnawave_uuid=node_uuid,
                        defaults=defaults,
                    )
                )

                if created:
                    result["created"] += 1
                else:
                    result["updated"] += 1

                result["nodes"].append(
                    {
                        "server": current_server.code,
                        "name": node.name,
                        "address": node.address,
                        "connected": node.is_connected,
                        "created": created,
                    }
                )

        except Exception as exc:
            result["errors"].append(
                {
                    "server": current_server.code,
                    "error": (
                        f"{type(exc).__name__}: {exc}"
                    ),
                }
            )

    return result
