"""Remove a Tuya device from LocalTuya and clean up registry entries."""

from __future__ import annotations

import logging

from .const import DOMAIN

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

_LOGGER = logging.getLogger(__name__)


async def _remove_from_localtuya(
    hass: HomeAssistant, device_id: str
) -> tuple[bool, str]:
    removed: list[str] = []
    for entry in hass.config_entries.async_entries("localtuya"):
        devices = dict(entry.data.get("devices", {}))
        if device_id not in devices:
            continue
        name = devices[device_id].get("friendly_name", device_id[:8])
        del devices[device_id]
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "devices": devices}
        )
        try:
            await hass.config_entries.async_reload(entry.entry_id)
        except Exception as exc:
            _LOGGER.warning("LocalTuya reload failed: %s", exc)
        removed.append(name)

    if removed:
        return True, f"Removido do LocalTuya: {', '.join(removed)}"
    return True, "Dispositivo não encontrado no LocalTuya (já removido)"


async def _cleanup_registries(hass: HomeAssistant, device_id: str) -> str:
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    removed: list[str] = []

    for entry in list(ent_reg.entities.values()):
        if (
            entry.platform == "localtuya"
            and entry.unique_id
            and device_id in entry.unique_id
        ):
            ent_reg.async_remove(entry.entity_id)
            removed.append(entry.entity_id)

    for device in list(dev_reg.devices.values()):
        for identifier in device.identifiers:
            if device_id in str(identifier):
                dev_reg.async_remove_device(device.id)
                removed.append(f"device:{device.id[:8]}")
                break

    return f"Removidas {len(removed)} entidades/dispositivos órfãos do registry"


async def run(hass: HomeAssistant, device_id: str) -> tuple[bool, str]:
    """Remove a device from LocalTuya and orphan registry entries."""
    ok, msg = await _remove_from_localtuya(hass, device_id)
    if ok:
        try:
            await _cleanup_registries(hass, device_id)
        except Exception as exc:
            _LOGGER.warning("Registry cleanup failed: %s", exc)
        # A camera registered in the Tuya Proxy Companion must leave it too, otherwise the
        # Companion keeps proxying/blocking a device that no longer exists in Home Assistant.
        sync = hass.data.get(DOMAIN, {}).get("companion")
        if sync is not None and device_id in getattr(sync, "devices", {}):
            try:
                await sync.async_remove(device_id)
                msg += " · removida do Tuya Proxy Companion"
            except Exception as exc:
                _LOGGER.warning("Companion removal failed: %s", exc)
    return ok, msg
