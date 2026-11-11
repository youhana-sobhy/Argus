"""Connector registry - picks the connector from configuration.

To add a vendor (NX-OS, FortiGate, Juniper, Palo Alto, VMware), implement
:class:`autohero.connectors.base.Connector` and register a factory here.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

from autohero.config import Settings, get_settings
from autohero.connectors.base import Connector, ConnectorError


def _mock(s: Settings) -> Connector:
    from autohero.connectors.aci_mock import MockAciConnector

    return MockAciConnector(state_file=s.runtime_dir / "aci_mock_state.json")


def _apic(s: Settings) -> Connector:
    from autohero.connectors.aci_apic import ApicConnector

    return ApicConnector(
        url=s.apic_url, username=s.apic_username, password=s.apic_password,
        verify_tls=s.apic_verify_tls, allow_execution=s.allow_live_execution,
    )


FACTORIES: dict[str, Callable[[Settings], Connector]] = {
    "mock": _mock,
    "apic": _apic,
    # "nxos": ..., "fortigate": ..., "junos": ..., "panos": ..., "vmware": ...  (roadmap)
}


@lru_cache
def get_connector() -> Connector:
    s = get_settings()
    try:
        return FACTORIES[s.connector](s)
    except KeyError as exc:
        raise ConnectorError(f"Unknown connector '{s.connector}'. Options: {', '.join(FACTORIES)}") from exc
