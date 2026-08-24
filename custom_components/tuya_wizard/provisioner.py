"""Add Tuya devices to the existing LocalTuya config entry."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator

from homeassistant.core import HomeAssistant

from . import card_store, frigate_integration, local_enable
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
    """Upsert a device in a LocalTuya config entry.

    Multiple LocalTuya entries are checked for an existing copy of the
    device before adding — otherwise a device already present in the second
    entry would get silently duplicated into the first.
    """
    lt_entries = list(hass.config_entries.async_entries("localtuya"))
    if not lt_entries:
        return False, "LocalTuya não instalado — instale primeiro via HACS"

    for entry in lt_entries:
        if device.device_id in entry.data.get("devices", {}):
            return True, f"{device.name} já existe no LocalTuya ({entry.title})"

    entry = lt_entries[0]
    devices: dict = dict(entry.data.get("devices", {}))
    devices[device.device_id] = _build_localtuya_device(device)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "devices": devices}
    )

    suffix = f" (entry '{entry.title}')" if len(lt_entries) > 1 else ""
    try:
        await hass.config_entries.async_reload(entry.entry_id)
        return True, f"Adicionado ao LocalTuya: {device.name}{suffix}"
    except Exception as exc:
        _LOGGER.warning("LocalTuya reload failed: %s", exc)
        return (
            True,
            f"Adicionado (reload manual pode ser necessário): {device.name}{suffix}",
        )


async def run(
    hass: HomeAssistant,
    devices: list[DeviceInfo],
    default_local_password: str | None = None,
    frigate_url: str | None = None,
) -> AsyncGenerator[str, None]:
    yield _sse("start", {"devices": len(devices), "total": len(devices)})

    resolved_frigate_url = await frigate_integration.resolve_url(hass, frigate_url)

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

        if local_enable.has_profile(device.category):
            # A password set on this specific device (frontend per-row
            # override) wins over the batch-wide default.
            secret = device.onvif_password or default_local_password
            enable_results = await hass.async_add_executor_job(
                local_enable.apply, device, secret
            )
            for result in enable_results:
                yield _sse(
                    "step",
                    {
                        "device": device.slug,
                        "step": f"local_enable:{result.step}",
                        "ok": result.status != "error",
                        "detail": result.detail,
                    },
                )

            if resolved_frigate_url and secret:
                frigate_ok, frigate_msg = await frigate_integration.configure_camera(
                    hass, resolved_frigate_url, device, secret
                )
                yield _sse(
                    "step",
                    {
                        "device": device.slug,
                        "step": "frigate",
                        "ok": frigate_ok,
                        "detail": frigate_msg,
                    },
                )

        ok, msg = await add_to_localtuya(hass, device)
        yield _sse(
            "step",
            {"device": device.slug, "step": "localtuya", "ok": ok, "detail": msg},
        )

        if ok:
            card_ok, card_msg = await card_store.auto_deploy_for_device(hass, device)
            yield _sse(
                "step",
                {
                    "device": device.slug,
                    "step": "card",
                    "ok": card_ok,
                    "detail": card_msg,
                },
            )

    yield _sse("done", {"devices": [d.slug for d in devices]})
