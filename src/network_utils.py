"""跨平台网络连接检查工具。"""

from dataclasses import dataclass
from typing import Callable, FrozenSet, Optional, Tuple

import psutil


@dataclass(frozen=True)
class ConnectionInspectionResult:
    """系统 TCP 连接检查结果。"""

    success: bool
    remote_ips: FrozenSet[str]
    error: Optional[str] = None


def _split_address(address) -> Tuple[Optional[str], Optional[int]]:
    """兼容 psutil 地址对象与普通二元组。"""
    if not address:
        return None, None

    host = getattr(address, 'ip', None)
    port = getattr(address, 'port', None)
    if host is not None and port is not None:
        return str(host), int(port)

    try:
        return str(address[0]), int(address[1])
    except (IndexError, TypeError, ValueError):
        return None, None


def inspect_established_remote_ips(
    local_port: int,
    connection_provider: Optional[Callable] = None,
) -> ConnectionInspectionResult:
    """取得连接到指定本机 TCP 端口的远端 IP，失败时不抛出异常。"""
    provider = connection_provider or psutil.net_connections

    try:
        connections = provider(kind='tcp')
        remote_ips = set()

        for connection in connections:
            if str(getattr(connection, 'status', '')).upper() != 'ESTABLISHED':
                continue

            _, connection_local_port = _split_address(getattr(connection, 'laddr', None))
            remote_ip, _ = _split_address(getattr(connection, 'raddr', None))
            if connection_local_port == local_port and remote_ip:
                remote_ips.add(remote_ip)

        return ConnectionInspectionResult(True, frozenset(remote_ips))
    except Exception as exc:
        return ConnectionInspectionResult(False, frozenset(), str(exc))
