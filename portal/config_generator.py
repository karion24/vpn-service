import os
import base64
import copy
import json
from pathlib import Path

from .remnawave import _request


ROUTING_DIR = Path("/opt/remnawave/caddy/routing")

CONFIG_TEMPLATES = {
    "full": ROUTING_DIR / "auto-full.json",
    "selective": ROUTING_DIR / "auto-selective.json",
}


class ConfigGenerationError(Exception):
    pass


def _unwrap(data):
    if isinstance(data, dict) and "response" in data:
        return data["response"]
    return data


def _decode_base64url(value):
    padding = "=" * ((4 - len(value) % 4) % 4)

    return base64.urlsafe_b64decode(
        value + padding
    )


def _encode_base64url(value):
    return (
        base64.urlsafe_b64encode(value)
        .decode("ascii")
        .rstrip("=")
    )


def _x25519_public_key(private_key):
    """
    Вычисляет X25519 public key из Reality private key.

    Реализация не требует установленного Xray
    или сторонних Python-библиотек.
    """

    try:
        scalar_bytes = bytearray(
            _decode_base64url(private_key)
        )
    except Exception as exc:
        raise ConfigGenerationError(
            f"Некорректный Reality privateKey: {exc}"
        ) from exc

    if len(scalar_bytes) != 32:
        raise ConfigGenerationError(
            "Reality privateKey должен содержать 32 байта"
        )

    # X25519 scalar clamping
    scalar_bytes[0] &= 248
    scalar_bytes[31] &= 127
    scalar_bytes[31] |= 64

    scalar = int.from_bytes(
        scalar_bytes,
        "little",
    )

    prime = 2**255 - 19
    a24 = 121665

    x1 = 9

    x2 = 1
    z2 = 0

    x3 = 9
    z3 = 1

    swap = 0

    for bit_number in range(254, -1, -1):
        bit = (scalar >> bit_number) & 1

        swap ^= bit

        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2

        swap = bit

        a = (x2 + z2) % prime
        aa = (a * a) % prime

        b = (x2 - z2) % prime
        bb = (b * b) % prime

        e = (aa - bb) % prime

        c = (x3 + z3) % prime
        d = (x3 - z3) % prime

        da = (d * a) % prime
        cb = (c * b) % prime

        x3 = ((da + cb) ** 2) % prime
        z3 = (
            x1 *
            ((da - cb) ** 2)
        ) % prime

        x2 = (aa * bb) % prime

        z2 = (
            e *
            (aa + a24 * e)
        ) % prime

    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2

    public_value = (
        x2 *
        pow(z2, prime - 2, prime)
    ) % prime

    public_bytes = public_value.to_bytes(
        32,
        "little",
    )

    return _encode_base64url(
        public_bytes
    )


def _load_template(mode):
    if mode not in CONFIG_TEMPLATES:
        raise ConfigGenerationError(
            f"Неизвестный режим конфигурации: {mode}"
        )

    template_path = CONFIG_TEMPLATES[mode]

    if not template_path.exists():
        raise ConfigGenerationError(
            f"Не найден шаблон конфигурации: "
            f"{template_path}"
        )

    try:
        with template_path.open(
            "r",
            encoding="utf-8",
        ) as file:
            return json.load(file)

    except Exception as exc:
        raise ConfigGenerationError(
            f"Не удалось прочитать шаблон "
            f"{template_path}: {exc}"
        ) from exc


def _get_remnawave_data(server=None):
    try:
        if server is not None:
            from .remnawave import RemnawaveClient

            rw = RemnawaveClient(server)

            hosts = rw.get_hosts()
            nodes = rw.get_nodes()

        else:
            # Обратная совместимость со старым режимом.
            hosts = _unwrap(
                _request(
                    "GET",
                    "/hosts",
                )
            )

            nodes = _unwrap(
                _request(
                    "GET",
                    "/nodes",
                )
            )

    except Exception as exc:
        raise ConfigGenerationError(
            f"Не удалось получить Hosts/Nodes "
            f"из Remnawave: {exc}"
        ) from exc

    if not isinstance(hosts, list):
        raise ConfigGenerationError(
            "Remnawave вернул некорректный список Hosts"
        )

    if not isinstance(nodes, list):
        raise ConfigGenerationError(
            "Remnawave вернул некорректный список Nodes"
        )

    return hosts, nodes


def _get_profile(profile_uuid, rw=None):
    try:
        if rw is not None:
            data = _unwrap(
                rw._request(
                    "GET",
                    f"/config-profiles/{profile_uuid}",
                )
            )
        else:
            # Старый режим оставляем для обратной совместимости.
            data = _unwrap(
                _request(
                    "GET",
                    f"/config-profiles/{profile_uuid}",
                )
            )

    except Exception as exc:
        raise ConfigGenerationError(
            "Не удалось получить Config Profile "
            f"{profile_uuid}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ConfigGenerationError(
            "Remnawave вернул некорректный Config Profile"
        )

    return data

def _find_inbound(profile, inbound_uuid):
    for inbound in profile.get(
        "inbounds",
        []
    ):
        if inbound.get("uuid") == inbound_uuid:
            return inbound

    raise ConfigGenerationError(
        "В Config Profile не найден inbound "
        f"{inbound_uuid}"
    )


def _get_reality_parameters(inbound):
    raw_inbound = inbound.get(
        "rawInbound",
        {}
    )

    stream_settings = raw_inbound.get(
        "streamSettings",
        {}
    )

    reality = stream_settings.get(
        "realitySettings",
        {}
    )

    private_key = reality.get(
        "privateKey"
    )

    server_names = reality.get(
        "serverNames",
        []
    )

    short_ids = reality.get(
        "shortIds",
        []
    )

    if not private_key:
        raise ConfigGenerationError(
            "В inbound отсутствует Reality privateKey"
        )

    if not server_names:
        raise ConfigGenerationError(
            "В inbound отсутствует Reality serverName"
        )

    if not short_ids:
        raise ConfigGenerationError(
            "В inbound отсутствует Reality shortId"
        )

    public_key = _x25519_public_key(
        private_key
    )

    return {
        "network": (
            stream_settings.get("network")
            or inbound.get("network")
            or "raw"
        ),

        "security": (
            stream_settings.get("security")
            or inbound.get("security")
            or "reality"
        ),

        "server_name": server_names[0],

        "short_id": short_ids[0],

        "public_key": public_key,
    }


def _get_active_nodes(nodes):
    result = []

    for node in nodes:
        if node.get("isDisabled"):
            continue

        if not node.get("isConnected"):
            continue

        result.append(node)

    return result


def _host_matches_node(host, node):
    """
    Сейчас в нашей конфигурации Host.address
    совпадает с Node.address.

    Это позволяет однозначно связать
    пользовательский Host с рабочей Node.
    """

    return (
        str(host.get("address", "")).strip()
        ==
        str(node.get("address", "")).strip()
    )


def _get_healthcheck_down_addresses():
    """
    Возвращает адреса нод, которые health-check
    подтвердил как DOWN.

    Fail-open:
    - если state.json отсутствует;
    - повреждён;
    - имеет неожиданную структуру;

    то никакие ноды по health-check не исключаются.
    """

    state_file = Path(
        "/opt/karion-healthcheck/state.json"
    )

    try:
        if not state_file.exists():
            return set()

        data = json.loads(
            state_file.read_text(
                encoding="utf-8"
            )
        )

        nodes_state = data.get(
            "nodes",
            {}
        )

        if not isinstance(
            nodes_state,
            dict,
        ):
            return set()

        result = set()

        for address, state in nodes_state.items():
            if not isinstance(
                state,
                dict,
            ):
                continue

            if (
                str(
                    state.get(
                        "status",
                        ""
                    )
                ).upper()
                == "DOWN"
            ):
                result.add(
                    str(address).strip()
                )

        return result

    except Exception:
        return set()


def _get_available_hosts(hosts, nodes):
    active_nodes = _get_active_nodes(
        nodes
    )

    healthcheck_down = (
        _get_healthcheck_down_addresses()
    )

    result = []

    for host in hosts:
        if host.get("isDisabled"):
            continue

        if host.get("isHidden"):
            continue

        address = str(
            host.get("address", "")
        ).strip()

        # DOWN подтверждён нашим независимым
        # VLESS/Reality health-check.
        if address in healthcheck_down:
            continue

        matching_node = None

        for node in active_nodes:
            if _host_matches_node(
                host,
                node,
            ):
                matching_node = node
                break

        if matching_node is None:
            continue

        result.append(
            {
                "host": host,
                "node": matching_node,
            }
        )

    result.sort(
        key=lambda item: (
            item["host"].get(
                "viewPosition",
                999999,
            ),
            item["host"].get(
                "remark",
                "",
            ),
        )
    )

    return result


def _make_tag(host, index, mode):
    country_code = (
        host.get("_node", {})
        .get("countryCode")
        or ""
    ).strip().lower()

    # Первый доступный сервер является AUTO/fallback.
    if index == 0:
        if mode == "full":
            return "⚡ Karion AUTO Full"

        return "⚡ Karion AUTO Selective"

    # Остальные серверы получают стабильные теги,
    # не зависящие от их текущей позиции в списке.
    if country_code:
        return f"proxy-{country_code}"

    # Резервный вариант, если countryCode отсутствует.
    return f"proxy-{index + 1}"

def _get_fingerprint(host):
    """
    Fingerprint для клиентских Reality-конфигураций Karion.

    Задаётся через CLIENT_FINGERPRINT в .env.
    Если переменная отсутствует или пустая,
    используется firefox.
    """
    fingerprint = (
        os.getenv(
            "CLIENT_FINGERPRINT",
            "firefox",
        ).strip().lower()
        or "firefox"
    )

    allowed = {
        "firefox",
        "chrome",
        "safari",
        "ios",
        "android",
        "edge",
        "random",
        "randomized",
    }

    if fingerprint not in allowed:
        raise ConfigGenerationError(
            "Некорректный CLIENT_FINGERPRINT: "
            f"{fingerprint}"
        )

    return fingerprint

def _build_outbound(
    client,
    host,
    node,
    reality,
    tag,
):
    return {
        "tag": tag,

        "protocol": "vless",

        "settings": {
            "address": host["address"],
            "port": host["port"],
            "id": client.remnawave_uuid,
            "encryption": "none",
            "flow": "xtls-rprx-vision",
        },

        "streamSettings": {
            "network": reality["network"],
            "security": reality["security"],

            "realitySettings": {
                "serverName": reality[
                    "server_name"
                ],

                "fingerprint": (
                    _get_fingerprint(host)
                ),

                "password": reality[
                    "public_key"
                ],

                "shortId": reality[
                    "short_id"
                ],

                "spiderX": "/",
            },
        },
    }


def _build_dynamic_outbounds(
    client,
    mode,
    hosts,
    nodes,
    rw=None,
):
    available_hosts = (
        _get_available_hosts(
            hosts,
            nodes,
        )
    )

    if not available_hosts:
        raise ConfigGenerationError(
            "Нет доступных VPN-узлов"
        )

    profile_cache = {}

    outbounds = []
    selectors = []

    for index, item in enumerate(
        available_hosts
    ):
        host = copy.deepcopy(
            item["host"]
        )

        node = item["node"]

        host["_node"] = node

        inbound_info = host.get(
            "inbound",
            {}
        )

        profile_uuid = inbound_info.get(
            "configProfileUuid"
        )

        inbound_uuid = inbound_info.get(
            "configProfileInboundUuid"
        )

        if not profile_uuid:
            continue

        if not inbound_uuid:
            continue

        if profile_uuid not in profile_cache:
            profile_cache[
                profile_uuid
            ] = _get_profile(
                profile_uuid,
                rw=rw,
            )

        profile = profile_cache[
            profile_uuid
        ]

        inbound = _find_inbound(
            profile,
            inbound_uuid,
        )

        reality = _get_reality_parameters(
            inbound
        )

        tag = _make_tag(
            host,
            len(outbounds),
            mode,
        )

        outbound = _build_outbound(
            client=client,
            host=host,
            node=node,
            reality=reality,
            tag=tag,
        )

        outbounds.append(
            outbound
        )

        selectors.append(
            tag
        )

    if not outbounds:
        raise ConfigGenerationError(
            "Не удалось сформировать "
            "ни одного VPN outbound"
        )

    return outbounds, selectors


def _replace_vless_outbounds(
    config,
    new_outbounds,
):
    system_outbounds = []

    for outbound in config.get(
        "outbounds",
        []
    ):
        if outbound.get(
            "protocol"
        ) == "vless":
            continue

        system_outbounds.append(
            outbound
        )

    config["outbounds"] = (
        new_outbounds
        +
        system_outbounds
    )


def _update_balancer(
    config,
    selectors,
):
    routing = config.get(
        "routing",
        {}
    )

    balancers = routing.get(
        "balancers",
        []
    )

    if not balancers:
        raise ConfigGenerationError(
            "В шаблоне отсутствует balancer"
        )

    balancer = balancers[0]

    balancer["selector"] = (
        selectors
    )

    balancer["fallbackTag"] = (
        selectors[0]
    )


def _update_observatory(
    config,
    selectors,
):
    observatory = config.get(
        "burstObservatory"
    )

    if not isinstance(
        observatory,
        dict,
    ):
        raise ConfigGenerationError(
            "В шаблоне отсутствует burstObservatory"
        )

    observatory[
        "subjectSelector"
    ] = selectors


def generate_client_config(
    client,
    mode,
):
    if not client.remnawave_uuid:
        raise ConfigGenerationError(
            "У клиента отсутствует "
            "Remnawave VLESS UUID"
        )

    config = _load_template(
        mode
    )

    # Определяем RemnawaveServer.
    # generate_client_config может получить как Client,
    # так и Device.
    server = getattr(
        client,
        "remnawave_server",
        None,
    )

    if server is None:
        parent_client = getattr(
            client,
            "client",
            None,
        )

        if parent_client is not None:
            server = getattr(
                parent_client,
                "remnawave_server",
                None,
            )

    if server is None:
        raise ConfigGenerationError(
            "Для клиента не указан сервер Remnawave"
        )

    from .remnawave import RemnawaveClient

    rw = RemnawaveClient(
        server
    )

    hosts, nodes = (
        _get_remnawave_data(
            server=server,
        )
    )

    new_outbounds, selectors = (
        _build_dynamic_outbounds(
            client=client,
            mode=mode,
            hosts=hosts,
            nodes=nodes,
            rw=rw,
        )
    )

    _replace_vless_outbounds(
        config,
        new_outbounds,
    )

    if len(selectors) == 1:
        # Если осталась только одна доступная VPN-нода,
        # balancer/leastPing не нужен. Направляем трафик
        # непосредственно в единственный outbound.
        single_tag = selectors[0]

        routing = config.get(
            "routing",
            {},
        )

        for rule in routing.get(
            "rules",
            [],
        ):
            if rule.get("balancerTag") == "karion-auto":
                rule.pop(
                    "balancerTag",
                    None,
                )
                rule["outboundTag"] = single_tag

        routing.pop(
            "balancers",
            None,
        )

        config.pop(
            "burstObservatory",
            None,
        )

        config.pop(
            "observatory",
            None,
        )

    else:
        _update_balancer(
            config,
            selectors,
        )

        _update_observatory(
            config,
            selectors,
        )

    return config


def get_probe_nodes(client):
    """
    Возвращает VPN-ноды для независимого health-check.

    В отличие от клиентской генерации:
    - disconnected Node не исключается;
    - disabled Host не исключается;
    - состояние Remnawave возвращается отдельно;
    - для доступной конфигурации строится полноценный
      VLESS/Reality outbound для реального теста.
    """

    if not client.remnawave_uuid:
        raise ConfigGenerationError(
            "У клиента отсутствует Remnawave VLESS UUID"
        )

    hosts, nodes = _get_remnawave_data()

    nodes_by_address = {}

    for node in nodes:
        address = str(
            node.get("address", "")
        ).strip()

        if address:
            nodes_by_address[address] = node

    profile_cache = {}
    result = []

    sorted_hosts = sorted(
        hosts,
        key=lambda host: (
            host.get("viewPosition", 999999),
            host.get("remark", ""),
        ),
    )

    for index, original_host in enumerate(sorted_hosts):
        host = copy.deepcopy(original_host)

        address = str(
            host.get("address", "")
        ).strip()

        if not address:
            continue

        node = nodes_by_address.get(address)

        if node is None:
            result.append(
                {
                    "name": host.get("remark") or address,
                    "address": address,
                    "country_code": "",
                    "admin_disabled": bool(
                        host.get("isDisabled")
                    ),
                    "hidden": bool(
                        host.get("isHidden")
                    ),
                    "node_found": False,
                    "node_connected": False,
                    "node_disabled": False,
                    "outbound": None,
                }
            )
            continue

        host["_node"] = node

        inbound_info = host.get(
            "inbound",
            {},
        )

        profile_uuid = inbound_info.get(
            "configProfileUuid"
        )

        inbound_uuid = inbound_info.get(
            "configProfileInboundUuid"
        )

        outbound = None

        if profile_uuid and inbound_uuid:
            if profile_uuid not in profile_cache:
                profile_cache[profile_uuid] = _get_profile(
                    profile_uuid
                )

            profile = profile_cache[profile_uuid]

            inbound = _find_inbound(
                profile,
                inbound_uuid,
            )

            reality = _get_reality_parameters(
                inbound
            )

            country_code = (
                node.get("countryCode")
                or ""
            ).lower()

            if country_code:
                tag = (
                    f"health-{country_code}-{index + 1}"
                )
            else:
                tag = f"health-{index + 1}"

            outbound = _build_outbound(
                client=client,
                host=host,
                node=node,
                reality=reality,
                tag=tag,
            )

        result.append(
            {
                "name": (
                    host.get("remark")
                    or node.get("name")
                    or address
                ),
                "address": address,
                "country_code": (
                    node.get("countryCode")
                    or ""
                ).upper(),
                "admin_disabled": bool(
                    host.get("isDisabled")
                ),
                "hidden": bool(
                    host.get("isHidden")
                ),
                "node_found": True,
                "node_connected": bool(
                    node.get("isConnected")
                ),
                "node_disabled": bool(
                    node.get("isDisabled")
                ),
                "outbound": outbound,
            }
        )

    return result


# =========================================================
# MULTI-SERVER HEALTHCHECK
# =========================================================

def get_probe_nodes_for_server(server):
    """
    Возвращает VPN-ноды одной конкретной панели Remnawave
    для независимого health-check.

    Для каждой панели используется её собственный
    технический пользователь karion-monitor.

    В отличие от старого get_probe_nodes():
    - API-запросы выполняются через RemnawaveClient(server);
    - данные разных Remnawave-панелей не смешиваются;
    - disconnected Node остаётся в результате;
    - disabled Host/Node остаётся в результате;
    - каждая запись содержит идентификатор сервера.
    """

    from types import SimpleNamespace
    from .remnawave import RemnawaveClient

    rw = RemnawaveClient(server)

    try:
        monitor_user = rw.get_user_by_username(
            "karion-monitor"
        )
    except Exception as exc:
        raise ConfigGenerationError(
            f"{server.name}: не удалось получить "
            f"karion-monitor: {exc}"
        ) from exc

    if not isinstance(monitor_user, dict):
        raise ConfigGenerationError(
            f"{server.name}: karion-monitor не найден"
        )

    if monitor_user.get("username") != "karion-monitor":
        raise ConfigGenerationError(
            f"{server.name}: karion-monitor не найден"
        )

    if monitor_user.get("status") != "ACTIVE":
        raise ConfigGenerationError(
            f"{server.name}: karion-monitor не активен "
            f"(status={monitor_user.get('status')})"
        )

    vless_uuid = str(
        monitor_user.get("vlessUuid") or ""
    ).strip()

    if not vless_uuid:
        raise ConfigGenerationError(
            f"{server.name}: у karion-monitor "
            "отсутствует vlessUuid"
        )

    # _build_outbound() уже умеет работать с объектом,
    # содержащим remnawave_uuid. Не меняем старую
    # рабочую функцию ради healthcheck.
    client = SimpleNamespace(
        remnawave_uuid=vless_uuid
    )

    try:
        hosts = rw.get_hosts()
        nodes = rw.get_nodes()
    except Exception as exc:
        raise ConfigGenerationError(
            f"{server.name}: не удалось получить Hosts/Nodes: {exc}"
        ) from exc

    if not isinstance(hosts, list):
        raise ConfigGenerationError(
            f"{server.name}: Remnawave вернул "
            "некорректный список Hosts"
        )

    if not isinstance(nodes, list):
        raise ConfigGenerationError(
            f"{server.name}: Remnawave вернул "
            "некорректный список Nodes"
        )

    nodes_by_address = {}

    for node in nodes:
        address = str(
            node.get("address", "")
        ).strip()

        if address:
            nodes_by_address[address] = node

    profile_cache = {}
    result = []

    sorted_hosts = sorted(
        hosts,
        key=lambda host: (
            host.get("viewPosition", 999999),
            host.get("remark", ""),
        ),
    )

    for index, original_host in enumerate(sorted_hosts):
        host = copy.deepcopy(original_host)

        address = str(
            host.get("address", "")
        ).strip()

        if not address:
            continue

        node = nodes_by_address.get(address)

        base_item = {
            "server_id": server.pk,
            "server_code": server.code,
            "server_name": server.name,
            "address": address,
            "host_uuid": host.get("uuid"),
        }

        if node is None:
            result.append(
                {
                    **base_item,
                    "probe_key": (
                        f"{server.pk}:host:"
                        f"{host.get('uuid') or address}"
                    ),
                    "name": (
                        host.get("remark")
                        or address
                    ),
                    "node_uuid": None,
                    "country_code": "",
                    "admin_disabled": bool(
                        host.get("isDisabled")
                    ),
                    "hidden": bool(
                        host.get("isHidden")
                    ),
                    "node_found": False,
                    "node_connected": False,
                    "node_disabled": False,
                    "outbound": None,
                }
            )
            continue

        host["_node"] = node

        inbound_info = host.get(
            "inbound",
            {},
        )

        profile_uuid = inbound_info.get(
            "configProfileUuid"
        )

        inbound_uuid = inbound_info.get(
            "configProfileInboundUuid"
        )

        outbound = None

        if profile_uuid and inbound_uuid:
            if profile_uuid not in profile_cache:
                try:
                    profile = rw.get_config_profile(
                        profile_uuid
                    )
                except Exception as exc:
                    raise ConfigGenerationError(
                        f"{server.name}: не удалось получить "
                        f"Config Profile {profile_uuid}: {exc}"
                    ) from exc

                if not isinstance(profile, dict):
                    raise ConfigGenerationError(
                        f"{server.name}: Remnawave вернул "
                        "некорректный Config Profile"
                    )

                profile_cache[profile_uuid] = profile

            profile = profile_cache[profile_uuid]

            inbound = _find_inbound(
                profile,
                inbound_uuid,
            )

            reality = _get_reality_parameters(
                inbound
            )

            country_code = (
                node.get("countryCode")
                or ""
            ).lower()

            if country_code:
                tag = (
                    f"health-{server.code}-"
                    f"{country_code}-{index + 1}"
                )
            else:
                tag = (
                    f"health-{server.code}-{index + 1}"
                )

            outbound = _build_outbound(
                client=client,
                host=host,
                node=node,
                reality=reality,
                tag=tag,
            )

        node_uuid = node.get("uuid")

        result.append(
            {
                **base_item,
                "probe_key": (
                    f"{server.pk}:node:"
                    f"{node_uuid or address}"
                ),
                "node_uuid": node_uuid,
                "name": (
                    host.get("remark")
                    or node.get("name")
                    or address
                ),
                "country_code": (
                    node.get("countryCode")
                    or ""
                ).upper(),
                "admin_disabled": bool(
                    host.get("isDisabled")
                ),
                "hidden": bool(
                    host.get("isHidden")
                ),
                "node_found": True,
                "node_connected": bool(
                    node.get("isConnected")
                ),
                "node_disabled": bool(
                    node.get("isDisabled")
                ),
                "outbound": outbound,
            }
        )

    return result
