"""Add Tuya devices to the existing LocalTuya config entry."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator

from homeassistant.core import HomeAssistant

from .models import DeviceInfo, DpInfo

_LOGGER = logging.getLogger(__name__)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _build_entity(dp: DpInfo) -> dict:
    ent: dict = {
        "platform": dp.platform,
        "friendly_name": dp.name,
        "id": str(dp.id),
    }
    if dp.platform == "switch":
        ent["is_passive_entity"] = dp.is_passive
    elif dp.platform == "select":
        ent["select_options"] = dp.select_options
    elif dp.platform == "number":
        ent["min_value"] = dp.min_value
        ent["max_value"] = dp.max_value
        ent["step_size"] = dp.step_size
    return ent


def _build_localtuya_device(device: DeviceInfo) -> dict:
    return {
        "friendly_name": device.name,
        "host": device.ip or "",
        "device_id": device.device_id,
        "local_key": device.local_key,
        "protocol_version": device.protocol_version,
        "manual_dps_strings": ",".join(str(dp.id) for dp in device.dps),
        "entities": [_build_entity(dp) for dp in device.dps],
        "node_id": None,
    }


async def add_to_localtuya(hass: HomeAssistant, device: DeviceInfo) -> tuple[bool, str]:
    """Upsert a device in the LocalTuya config entry."""
    lt_entries = [e for e in hass.config_entries.async_entries("localtuya")]
    if not lt_entries:
        return False, "LocalTuya não instalado — instale primeiro via HACS"

    entry = lt_entries[0]
    devices: dict = dict(entry.data.get("devices", {}))

    if device.device_id in devices:
        return True, f"{device.name} já existe no LocalTuya"

    devices[device.device_id] = _build_localtuya_device(device)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "devices": devices}
    )

    try:
        await hass.config_entries.async_reload(entry.entry_id)
        return True, f"Adicionado ao LocalTuya: {device.name}"
    except Exception as exc:
        _LOGGER.warning("LocalTuya reload failed: %s", exc)
        return True, f"Adicionado (reload manual pode ser necessário): {device.name}"


async def run(
    hass: HomeAssistant,
    devices: list[DeviceInfo],
) -> AsyncGenerator[str, None]:
    yield _sse("start", {"devices": len(devices), "total": len(devices)})

    for device in devices:
        if not device.ip:
            yield _sse(
                "step",
                {
                    "device": device.slug,
                    "step": "localtuya",
                    "ok": False,
                    "detail": f"IP desconhecido para '{device.name}' — verifique a rede",
                },
            )
            continue

        ok, msg = await add_to_localtuya(hass, device)
        yield _sse(
            "step",
            {"device": device.slug, "step": "localtuya", "ok": ok, "detail": msg},
        )

    yield _sse("done", {"devices": [d.slug for d in devices]})
