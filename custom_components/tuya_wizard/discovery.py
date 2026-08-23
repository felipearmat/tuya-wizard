"""Discover Tuya devices from the cloud project and the local network."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncGenerator
from typing import Any

import tinytuya
from homeassistant.core import HomeAssistant

from .dp_mapper import map_dp
from .models import DeviceInfo, DpInfo, TuyaCredentials

_LOGGER = logging.getLogger(__name__)

# Try the device's broadcasted version first (if known), then fall back
# through the protocol versions Tuya devices commonly speak.
_FALLBACK_PROTOCOL_VERSIONS: tuple[float, ...] = (3.5, 3.4, 3.3, 3.2, 3.1)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _cloud_devices(creds: TuyaCredentials) -> list[dict[str, Any]]:
    c = tinytuya.Cloud(
        apiRegion=creds.region,
        apiKey=creds.access_id,
        apiSecret=creds.access_secret,
    )
    # include_map=True batches in the full cloud DP schema (all "functions" +
    # "status" entries, keyed by numeric dp_id) per device, deduplicated by
    # product_id — one extra call per distinct product model, not per device.
    return c.getdevices(include_map=True) or []


def _candidate_versions(hint_version: float | None) -> list[float]:
    if hint_version is None:
        return list(_FALLBACK_PROTOCOL_VERSIONS)
    return [hint_version] + [
        v for v in _FALLBACK_PROTOCOL_VERSIONS if v != hint_version
    ]


def _local_status(
    ip: str, device_id: str, local_key: str, hint_version: float | None = None
) -> tuple[dict[str, object], float | None]:
    """Fetch live DP values, probing protocol versions until one connects.

    Tuya devices speak different local protocol versions (3.1-3.5) depending
    on firmware age; a hardcoded version silently fails to connect to a lot
    of real-world devices, so `hint_version` (from the UDP broadcast) is
    tried first and the rest are used as fallbacks.
    """
    for version in _candidate_versions(hint_version):
        try:
            dev = tinytuya.Device(device_id, ip, local_key, version=version)
            dev.set_socketTimeout(5)
            result = dev.status()
        except Exception as exc:
            _LOGGER.debug(
                "Local status failed for %s at %s (v%s): %s",
                device_id[:8],
                ip,
                version,
                exc,
            )
            continue

        if isinstance(result, dict) and "dps" in result:
            return result["dps"], version
        _LOGGER.debug(
            "Local status failed for %s at %s (v%s): %s",
            device_id[:8],
            ip,
            version,
            result.get("Error") if isinstance(result, dict) else result,
        )

    return {}, None


def _scan_local_ips() -> dict[str, dict[str, object]]:
    """Return {gwId: {"ip": ip, "version": float|None}} from a UDP broadcast scan."""
    try:
        found = tinytuya.deviceScan(verbose=False, maxretry=4)
    except Exception as exc:
        _LOGGER.debug("Local scan failed: %s", exc)
        return {}

    result: dict[str, dict[str, object]] = {}
    for ip, dev in found.items():
        gw_id = dev.get("gwId", "")
        if not gw_id:
            continue
        raw_version = dev.get("version") or dev.get("ver")
        try:
            version = float(raw_version) if raw_version else None
        except (TypeError, ValueError):
            version = None
        result[gw_id] = {"ip": ip, "version": version}
    return result


def _build_dps(
    schema_map: dict[str, dict],
    dps_status: dict[str, object],
) -> list[DpInfo]:
    """Merge the cloud DP schema with live local values into DpInfo entries.

    Only numeric dp_ids are usable here: LocalTuya's local protocol addresses
    DPs by their numeric index, so cloud-only entries that `getmapping` could
    only key by string `code` (no local dp_id) aren't controllable this way
    and are dropped rather than passed on as bogus LocalTuya DPs.
    """
    numeric_schema_ids = {dp_id for dp_id in schema_map if dp_id.isdigit()}
    all_ids = numeric_schema_ids | {str(k) for k in dps_status}

    dps: list[DpInfo] = []
    for dp_id in sorted(all_ids, key=int):
        schema = schema_map.get(dp_id)
        # status dict may use int or str keys
        value = dps_status.get(dp_id) or dps_status.get(int(dp_id))
        tuya_type = schema.get("type") if schema else None
        dps.append(map_dp(dp_id, tuya_type=tuya_type, value=value, schema=schema))

    return dps


def _build_device(
    raw: dict,
    local_scan: dict[str, dict[str, object]],
) -> DeviceInfo:
    device_id = raw.get("id", "")
    name = raw.get("name", device_id)
    local_key = raw.get("key", "")
    category = raw.get("category", "")

    scan_info = local_scan.get(device_id, {})
    ip: str | None = raw.get("ip") or scan_info.get("ip") or None
    hint_version = scan_info.get("version")

    schema_map = raw.get("mapping") or {}
    dps_status, detected_version = (
        _local_status(ip, device_id, local_key, hint_version) if ip else ({}, None)
    )
    dps = _build_dps(schema_map, dps_status)

    device_kwargs: dict = dict(
        device_id=device_id,
        name=name,
        slug=_slug(name),
        ip=ip,
        local_key=local_key,
        category=category,
        dps=dps,
        online=bool(raw.get("online", True)),
    )
    protocol_version = detected_version or hint_version
    if protocol_version is not None:
        device_kwargs["protocol_version"] = str(protocol_version)

    return DeviceInfo(**device_kwargs)


async def run(hass: HomeAssistant, creds: TuyaCredentials) -> list[DeviceInfo]:
    """Discover all project devices from cloud + local network in parallel."""
    raw_devices, local_scan = await asyncio.gather(
        hass.async_add_executor_job(_cloud_devices, creds),
        hass.async_add_executor_job(_scan_local_ips),
    )

    return list(
        await asyncio.gather(
            *(
                hass.async_add_executor_job(_build_device, raw, local_scan)
                for raw in raw_devices
            )
        )
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream(hass: HomeAssistant, creds: TuyaCredentials) -> AsyncGenerator[str, None]:
    """Same discovery as run(), but yields SSE progress as each device resolves.

    Cloud device listing + the DP schema batch it carries can be slow with
    many devices, and each device's local status probe adds its own
    round-trip; without this, the caller gets nothing until every device has
    resolved, and the caller who asked for progress (SSE, like provision/
    remove already have) had none.
    """
    yield _sse("start", {"step": "cloud_scan"})
    raw_devices, local_scan = await asyncio.gather(
        hass.async_add_executor_job(_cloud_devices, creds),
        hass.async_add_executor_job(_scan_local_ips),
    )
    yield _sse("step", {"step": "cloud_scan", "ok": True, "total": len(raw_devices)})

    devices: list[DeviceInfo] = []
    tasks = [
        hass.async_add_executor_job(_build_device, raw, local_scan)
        for raw in raw_devices
    ]
    for task in asyncio.as_completed(tasks):
        try:
            device = await task
        except Exception as exc:
            _LOGGER.warning("Device build failed during discovery: %s", exc)
            yield _sse("step", {"step": "device", "ok": False, "detail": str(exc)})
            continue
        devices.append(device)
        yield _sse("device", {"device": device.model_dump()})

    yield _sse("done", {"devices": [d.slug for d in devices]})
