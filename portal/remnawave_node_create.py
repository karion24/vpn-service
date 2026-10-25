from dataclasses import dataclass

from .models import RemnawaveNode, RemnawaveServer
from .remnawave import RemnawaveAPIError, RemnawaveClient


class RemnawaveNodeCreateError(Exception):
    pass


@dataclass
class VlessConfig:
    profile_uuid: str
    profile_name: str
    inbound_uuid: str
    inbound_tag: str


def find_vless_reality_config(server):
    """
    Находит в выбранной панели Config Profile с VLESS + Reality.
    Ничего не изменяет.
    """
    rw = RemnawaveClient(server)
    profiles = rw.get_config_profiles() or []

    candidates = []

    for profile in profiles:
        profile_uuid = profile.get("uuid")
        profile_name = profile.get("name") or ""

        for inbound in profile.get("inbounds") or []:
            inbound_type = (inbound.get("type") or "").lower()
            security = (inbound.get("security") or "").lower()

            raw = inbound.get("rawInbound") or {}
            stream = raw.get("streamSettings") or {}

            if not security:
                security = (
                    stream.get("security") or ""
                ).lower()

            if (
                inbound_type == "vless"
                and security == "reality"
            ):
                candidates.append(
                    VlessConfig(
                        profile_uuid=str(profile_uuid),
                        profile_name=profile_name,
                        inbound_uuid=str(inbound.get("uuid")),
                        inbound_tag=inbound.get("tag") or "",
                    )
                )

    if not candidates:
        raise RemnawaveNodeCreateError(
            f"{server.name}: VLESS Reality inbound не найден."
        )

    if len(candidates) > 1:
        names = ", ".join(
            f"{x.profile_name}/{x.inbound_tag}"
            for x in candidates
        )
        raise RemnawaveNodeCreateError(
            f"{server.name}: найдено несколько VLESS Reality "
            f"inbound: {names}. Нужен однозначный выбор."
        )

    return candidates[0]


def validate_new_node(
    server,
    name,
    address,
):
    """
    Локальные проверки перед созданием.
    """
    if not isinstance(server, RemnawaveServer):
        raise RemnawaveNodeCreateError(
            "Некорректный сервер Remnawave."
        )

    if not server.is_active:
        raise RemnawaveNodeCreateError(
            f"{server.name}: сервер отключён."
        )

    if not server.node_secret_key:
        raise RemnawaveNodeCreateError(
            f"{server.name}: не задан Node Secret Key."
        )

    name = (name or "").strip()
    address = (address or "").strip()

    if not name:
        raise RemnawaveNodeCreateError(
            "Не указано название ноды."
        )

    if not address:
        raise RemnawaveNodeCreateError(
            "Не указан IP-адрес ноды."
        )

    if RemnawaveNode.objects.filter(
        server=server,
        name=name,
    ).exists():
        raise RemnawaveNodeCreateError(
            f"Нода {name} уже существует в {server.name}."
        )

    if RemnawaveNode.objects.filter(
        server=server,
        address=address,
    ).exists():
        raise RemnawaveNodeCreateError(
            f"IP {address} уже используется в {server.name}."
        )


def build_install_script(server):
    """
    Генерирует проверяемый install.sh для Remnawave Node.
    """

    import base64
    import json

    secret = (server.node_secret_key or "").strip()

    if not secret:
        raise RemnawaveNodeCreateError(
            f"{server.name}: Node Secret Key отсутствует."
        )

    # Проверяем Secret Key ещё на стороне портала.
    try:
        decoded = base64.urlsafe_b64decode(
            secret + "=" * (-len(secret) % 4)
        )
        secret_data = json.loads(decoded)
    except Exception as exc:
        raise RemnawaveNodeCreateError(
            f"{server.name}: Node Secret Key повреждён: {exc}"
        ) from exc

    required = {
        "nodeCertPem",
        "nodeKeyPem",
        "caCertPem",
        "jwtPublicKey",
    }

    missing = required - set(secret_data)

    if missing:
        raise RemnawaveNodeCreateError(
            f"{server.name}: Node Secret Key неполный. "
            f"Отсутствуют: {', '.join(sorted(missing))}"
        )

    # Base64 не содержит одинарных кавычек, но всё равно
    # экранируем значение безопасно.
    escaped_secret = secret.replace("'", "'\"'\"'")

    return f"""#!/usr/bin/env bash
set -euo pipefail

export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: запустите скрипт от root."
    exit 1
fi

echo "===== REMNAWAVE NODE INSTALL ====="

apt-get update
apt-get install -y ca-certificates curl

if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sh
fi

systemctl enable --now docker

mkdir -p /opt/remnanode

# Если конфигурация уже существует — сохраняем копию.
if [ -f /opt/remnanode/docker-compose.yml ]; then
    cp /opt/remnanode/docker-compose.yml \
       "/opt/remnanode/docker-compose.yml.bak-$(date +%Y%m%d-%H%M%S)"
fi

cat > /opt/remnanode/docker-compose.yml <<'COMPOSE'
services:
  remnanode:
    container_name: remnanode
    hostname: remnanode
    image: remnawave/node:latest
    network_mode: host
    restart: always
    cap_add:
      - NET_ADMIN
    ulimits:
      nofile:
        soft: 1048576
        hard: 1048576
    environment:
      - NODE_PORT=2222
      - SECRET_KEY={escaped_secret}
COMPOSE

cd /opt/remnanode

echo
echo "===== PULL ====="
docker compose pull

echo
echo "===== START ====="
docker compose up -d --force-recreate

echo
echo "===== WAIT ====="

OK=0

for i in $(seq 1 120); do
    if ss -lnt 2>/dev/null | grep -q ':2222 '; then
        OK=1
        echo "Remnawave Node API готов (${{i}}s)."
        break
    fi

    # Если контейнер действительно умер — нет смысла ждать 120 секунд.
    if ! docker inspect remnanode         --format '{{{{.State.Running}}}}' 2>/dev/null         | grep -q '^true$'; then

        echo "ERROR: контейнер remnanode остановился."
        docker logs --tail 100 remnanode 2>&1 || true
        exit 1
    fi

    sleep 1
done

echo
echo "===== CONTAINER ====="
docker ps -a --filter name=remnanode \
  --format 'table {{{{.Names}}}}\\t{{{{.Image}}}}\\t{{{{.Status}}}}'

echo
echo "===== PORTS ====="
ss -lntp | grep -E ':2222|:443' || true

echo
echo "===== LAST LOGS ====="
docker logs --tail 30 remnanode 2>&1 || true

if [ "$OK" -ne 1 ]; then
    echo
    echo "ERROR: Remnawave Node не открыл порт 2222."
    echo "Установка не считается успешной."
    exit 1
fi

if docker logs --tail 100 remnanode 2>&1 \
    | grep -q 'SECRET_KEY contains invalid JSON'; then

    echo
    echo "ERROR: Remnawave отклонил SECRET_KEY."
    exit 1
fi

echo
echo "========================================"
echo "Installation complete."
echo "Remnawave Node слушает порт 2222."
echo "========================================"
"""

def create_remnawave_node(
    server,
    name,
    address,
    country_code="",
    node_port=2222,
    vpn_port=443,
):
    """
    Полный цикл создания новой ноды:

    1. Проверка входных данных.
    2. Поиск VLESS Reality Profile/Inbound.
    3. Создание Node в Remnawave.
    4. Создание Host в Remnawave.
    5. Создание локальной RemnawaveNode.
    6. Генерация install.sh.

    Установка remnanode на удалённый сервер здесь
    НЕ выполняется.
    """

    name = (name or "").strip()
    address = (address or "").strip()
    country_code = (country_code or "").strip().upper()

    validate_new_node(
        server=server,
        name=name,
        address=address,
    )

    if country_code and len(country_code) != 2:
        raise RemnawaveNodeCreateError(
            "Код страны должен состоять из двух символов."
        )

    cfg = find_vless_reality_config(server)
    rw = RemnawaveClient(server)

    remote_node = None
    remote_host = None

    # -----------------------------------------------------
    # CREATE NODE
    # -----------------------------------------------------

    try:
        remote_node = rw.create_node(
            name=name,
            address=address,
            config_profile_uuid=cfg.profile_uuid,
            inbound_uuid=cfg.inbound_uuid,
            country_code=country_code,
            node_port=node_port,
        )
    except Exception as exc:
        raise RemnawaveNodeCreateError(
            f"{server.name}: не удалось создать Node: {exc}"
        ) from exc

    node_uuid = remote_node.get("uuid")
    node_id = remote_node.get("id")

    if not node_uuid:
        raise RemnawaveNodeCreateError(
            f"{server.name}: Node создан, но API не вернул UUID. "
            f"Ответ: {remote_node!r}"
        )

    # -----------------------------------------------------
    # CREATE HOST
    # -----------------------------------------------------

    try:
        remote_host = rw.create_host(
            remark=name,
            address=address,
            config_profile_uuid=cfg.profile_uuid,
            inbound_uuid=cfg.inbound_uuid,
            port=vpn_port,
        )
    except Exception as exc:
        raise RemnawaveNodeCreateError(
            f"{server.name}: Node уже создан "
            f"(UUID {node_uuid}), но Host создать не удалось: "
            f"{exc}"
        ) from exc

    host_uuid = remote_host.get("uuid")

    if not host_uuid:
        raise RemnawaveNodeCreateError(
            f"{server.name}: Node {node_uuid} и Host созданы, "
            "но API Host не вернул UUID."
        )

    # -----------------------------------------------------
    # LOCAL DATABASE
    # -----------------------------------------------------

    try:
        local_node = RemnawaveNode.objects.create(
            server=server,
            name=name,
            address=address,
            country_code=country_code,
            node_port=int(node_port),
            vpn_port=int(vpn_port),
            remnawave_uuid=node_uuid,
            remnawave_id=node_id,
            config_profile_uuid=cfg.profile_uuid,
            inbound_uuid=cfg.inbound_uuid,
            host_uuid=host_uuid,
            is_enabled=True,
            is_connected=bool(
                remote_node.get("isConnected", False)
            ),
            last_status_message=(
                remote_node.get("lastStatusMessage") or ""
            ),
            xray_uptime=remote_node.get("xrayUptime") or 0,
            users_online=remote_node.get("usersOnline") or 0,
        )
    except Exception as exc:
        raise RemnawaveNodeCreateError(
            f"{server.name}: Node {node_uuid} и Host "
            f"{host_uuid} уже созданы в Remnawave, "
            f"но локальную запись создать не удалось: {exc}"
        ) from exc

    install_script = build_install_script(server)

    return CreatedRemnawaveNode(
        node=local_node,
        remote_node=remote_node,
        remote_host=remote_host,
        install_script=install_script,
    )


# =========================================================
# RECONCILE EXISTING DJANGO NODE -> REMNAWAVE
# =========================================================

@dataclass
class ReconcileRemnawaveNodeResult:
    node: RemnawaveNode
    node_created: bool
    host_created: bool
    node_found_by: str
    host_found_by: str


def _normalize_uuid(value):
    if value is None:
        return ""
    return str(value).strip().lower()


def reconcile_remnawave_node(node):
    """
    Проверяет существование локальной Django-ноды в Remnawave.

    Node:
      1. UUID
      2. address
      3. name
      4. создать, только если ничего не найдено

    Host:
      1. UUID
      2. address + port
      3. remark/name
      4. создать, только если ничего не найдено

    Локальную запись НЕ создаёт и НЕ удаляет.
    install.sh НЕ запускает.
    """

    if not isinstance(node, RemnawaveNode):
        raise RemnawaveNodeCreateError(
            "Некорректная локальная нода."
        )

    server = node.server

    if not server.is_active:
        raise RemnawaveNodeCreateError(
            f"{server.name}: сервер Remnawave отключён."
        )

    rw = RemnawaveClient(server)
    cfg = find_vless_reality_config(server)

    # -----------------------------------------------------
    # REMOTE NODE
    # -----------------------------------------------------

    try:
        remote_nodes = rw.get_nodes() or []
    except Exception as exc:
        raise RemnawaveNodeCreateError(
            f"{server.name}: не удалось получить список Node: {exc}"
        ) from exc

    if isinstance(remote_nodes, dict):
        remote_nodes = (
            remote_nodes.get("nodes")
            or remote_nodes.get("response")
            or []
        )

    wanted_uuid = _normalize_uuid(node.remnawave_uuid)
    wanted_name = (node.name or "").strip().casefold()
    wanted_address = (node.address or "").strip()

    remote_node = None
    node_found_by = ""

    # 1. Старый UUID.
    if wanted_uuid:
        for item in remote_nodes:
            if _normalize_uuid(item.get("uuid")) == wanted_uuid:
                remote_node = item
                node_found_by = "UUID"
                break

    # 2. IP.
    if remote_node is None:
        matches = [
            item for item in remote_nodes
            if (item.get("address") or "").strip() == wanted_address
        ]

        if len(matches) > 1:
            raise RemnawaveNodeCreateError(
                f"{server.name}: найдено несколько Node "
                f"с IP {node.address}. Создание остановлено."
            )

        if matches:
            remote_node = matches[0]
            node_found_by = "IP"

    # 3. Имя.
    if remote_node is None:
        matches = [
            item for item in remote_nodes
            if (item.get("name") or "").strip().casefold()
            == wanted_name
        ]

        if len(matches) > 1:
            raise RemnawaveNodeCreateError(
                f"{server.name}: найдено несколько Node "
                f"с именем {node.name}. Создание остановлено."
            )

        if matches:
            remote_node = matches[0]
            node_found_by = "NAME"

    node_created = False

    # 4. Только теперь создаём.
    if remote_node is None:
        try:
            remote_node = rw.create_node(
                name=node.name,
                address=node.address,
                config_profile_uuid=cfg.profile_uuid,
                inbound_uuid=cfg.inbound_uuid,
                country_code=node.country_code,
                node_port=node.node_port,
            )
        except Exception as exc:
            raise RemnawaveNodeCreateError(
                f"{server.name}: Node отсутствует, "
                f"но создать её не удалось: {exc}"
            ) from exc

        node_created = True
        node_found_by = "CREATED"

    remote_node_uuid = remote_node.get("uuid")

    if not remote_node_uuid:
        raise RemnawaveNodeCreateError(
            f"{server.name}: Node найдена/создана, "
            "но Remnawave не вернул UUID."
        )

    # -----------------------------------------------------
    # REMOTE HOST
    # -----------------------------------------------------

    try:
        remote_hosts = rw.get_hosts() or []
    except Exception as exc:
        raise RemnawaveNodeCreateError(
            f"{server.name}: не удалось получить список Host: {exc}"
        ) from exc

    if isinstance(remote_hosts, dict):
        remote_hosts = (
            remote_hosts.get("hosts")
            or remote_hosts.get("response")
            or []
        )

    wanted_host_uuid = _normalize_uuid(node.host_uuid)

    remote_host = None
    host_found_by = ""

    # 1. Старый UUID.
    if wanted_host_uuid:
        for item in remote_hosts:
            if _normalize_uuid(item.get("uuid")) == wanted_host_uuid:
                remote_host = item
                host_found_by = "UUID"
                break

    # 2. IP + VPN port.
    if remote_host is None:
        matches = [
            item for item in remote_hosts
            if (
                (item.get("address") or "").strip() == wanted_address
                and int(item.get("port") or 0) == int(node.vpn_port)
            )
        ]

        if len(matches) > 1:
            raise RemnawaveNodeCreateError(
                f"{server.name}: найдено несколько Host "
                f"{node.address}:{node.vpn_port}. "
                "Создание остановлено."
            )

        if matches:
            remote_host = matches[0]
            host_found_by = "IP:PORT"

    # 3. remark/name.
    if remote_host is None:
        matches = [
            item for item in remote_hosts
            if (item.get("remark") or "").strip().casefold()
            == wanted_name
        ]

        if len(matches) > 1:
            raise RemnawaveNodeCreateError(
                f"{server.name}: найдено несколько Host "
                f"с именем {node.name}. Создание остановлено."
            )

        if matches:
            remote_host = matches[0]
            host_found_by = "NAME"

    host_created = False

    # 4. Только теперь создаём Host.
    if remote_host is None:
        try:
            remote_host = rw.create_host(
                remark=node.name,
                address=node.address,
                config_profile_uuid=cfg.profile_uuid,
                inbound_uuid=cfg.inbound_uuid,
                port=node.vpn_port,
            )
        except Exception as exc:
            raise RemnawaveNodeCreateError(
                f"{server.name}: Host отсутствует, "
                f"но создать его не удалось: {exc}"
            ) from exc

        host_created = True
        host_found_by = "CREATED"

    remote_host_uuid = remote_host.get("uuid")

    if not remote_host_uuid:
        raise RemnawaveNodeCreateError(
            f"{server.name}: Host найден/создан, "
            "но Remnawave не вернул UUID."
        )

    # -----------------------------------------------------
    # UPDATE LOCAL DJANGO RECORD
    # -----------------------------------------------------

    node.remnawave_uuid = remote_node_uuid
    node.remnawave_id = remote_node.get("id")
    node.host_uuid = remote_host_uuid

    node.config_profile_uuid = (
        remote_node.get("configProfileUuid")
        or cfg.profile_uuid
    )

    node.inbound_uuid = cfg.inbound_uuid

    node.is_connected = bool(
        remote_node.get("isConnected", False)
    )

    node.is_enabled = not bool(
        remote_node.get("isDisabled", False)
    )

    node.last_status_message = (
        remote_node.get("lastStatusMessage") or ""
    )

    node.xray_uptime = (
        remote_node.get("xrayUptime") or 0
    )

    node.users_online = (
        remote_node.get("usersOnline") or 0
    )

    versions = remote_node.get("versions") or {}

    node.xray_version = (
        versions.get("xray")
        or remote_node.get("xrayVersion")
        or ""
    )

    node.node_version = (
        versions.get("node")
        or remote_node.get("nodeVersion")
        or ""
    )

    node.save()

    return ReconcileRemnawaveNodeResult(
        node=node,
        node_created=node_created,
        host_created=host_created,
        node_found_by=node_found_by,
        host_found_by=host_found_by,
    )
