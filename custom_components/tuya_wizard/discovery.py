"""Discover Tuya devices from the cloud project and the local network."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import tinytuya
from homeassistant.core import HomeAssistant

from .dp_mapper import map_dp
from .models import DeviceInfo, DpInfo, TuyaCredentials

_LOGGER = logging.getLogger(__name__)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _cloud_devices(creds: TuyaCredentials) -> list[dict[str, Any]]:
    c = tinytuya.Cloud(
        apiRegion=creds.region,
        apiKey=creds.access_id,
        apiSecret=creds.access_secret,
    )
    return c.getdevices() or []


def _cloud_dp_schema(creds: TuyaCredentials, device_id: str) -> list[dict]:
    c = tinytuya.Cloud(
        apiRegion=creds.region,
        apiKey=creds.access_id,
        apiSecret=creds.access_secret,
    )
    try:
        result = c.getdpnames(device_id)
        return result if isinstance(result, list) else []
    except Exception as exc:
        _LOGGER.debug("DP schema fetch failed for %s: %s", device_id[:8], exc)
        return []


def _local_status(ip: str, device_id: str, local_key: str) -> dict[str, object]:
    try:
        dev = tinytuya.Device(device_id, ip, local_key, version=3.5)
        dev.set_socketTimeout(5)
        result = dev.status()
        return result.get("dps", {}) if isinstance(result, dict) else {}
    except Exception as exc:
        _LOGGER.debug("Local status failed for %s at %s: %s", device_id[:8], ip, exc)
        return {}


def _scan_local_ips() -> dict[str, str]:
    """Return {gwId: ip} from UDP broadcast scan."""
    try:
        found = tinytuya.deviceScan(verbose=False, maxretry=4)
        return {dev.get("gwId", ""): ip for ip, dev in found.items() if dev.get("gwId")}
    except Exception as exc:
        _LOGGER.debug("Local scan failed: %s", exc)
        return {}


def _build_dps(
    schema_list: list[dict],
    dps_status: dict[str, object],
) -> list[DpInfo]:
    schema_by_id = {str(s.get("dp_id") or s.get("dp", "")): s for s in schema_list}
    all_ids = set(schema_by_id) | {str(k) for k in dps_status}

    dps: list[DpInfo] = []
    for dp_id in sorted(all_ids, key=lambda x: int(x) if x.isdigit() else 9999):
        schema = schema_by_id.get(dp_id)
        # status dict may use int or str keys
        value = dps_status.get(dp_id) or dps_status.get(
            int(dp_id) if dp_id.isdigit() else dp_id
        )
        tuya_type: str | None = None
        if schema:
            tuya_type = schema.get("type") or schema.get("data_type") or None
        dps.append(map_dp(dp_id, tuya_type=tuya_type, value=value, schema=schema))

    return dps


def _build_device(
    raw: dict,
    local_ips: dict[str, str],
    creds: TuyaCredentials,
) -> DeviceInfo:
    device_id = raw.get("id", "")
    name = raw.get("name", device_id)
    local_key = raw.get("key", "")
    category = raw.get("category", "")

    ip: str | None = raw.get("ip") or local_ips.get(device_id) or None

    schema_list = _cloud_dp_schema(creds, device_id)
    dps_status = _local_status(ip, device_id, local_key) if ip else {}
    dps = _build_dps(schema_list, dps_status)

    return DeviceInfo(
        device_id=device_id,
        name=name,
        slug=_slug(name),
        ip=ip,
        local_key=local_key,
        category=category,
        dps=dps,
        online=bool(raw.get("online", True)),
    )


async def run(hass: HomeAssistant, creds: TuyaCredentials) -> list[DeviceInfo]:
    """Discover all project devices from cloud + local network in parallel."""
    raw_devices, local_ips = await asyncio.gather(
        hass.async_add_executor_job(_cloud_devices, creds),
        hass.async_add_executor_job(_scan_local_ips),
    )

    devices = []
    for raw in raw_devices:
        device = await hass.async_add_executor_job(_build_device, raw, local_ips, creds)
        devices.append(device)

    return devices
