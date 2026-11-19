"""Per-device Tuya cloud block through the Tuya Proxy Companion, plus a leak check.

How the block works: the Companion makes the device route its traffic through
Home Assistant (ARP) and drops/redirects what goes to the Tuya cloud. Limits the
page must show to the user:

* It can leak: the router still answers the device now and then, and in those
  windows a cloud message gets out. A firewall rule on the router (block the
  device's IP to the internet) is the firm way; see the help text in the page.
* Tuya cameras validate the cloud TLS certificate. The Companion cannot
  impersonate the cloud, and many cameras stop detecting motion without it.
* If Home Assistant or the Companion is down, the block stops (fails open) and
  the wizard re-applies it when they come back.

The leak check reads the device's cloud log: anything the cloud received after
the block started means the block is leaking.
"""

from __future__ import annotations

import re
import time

import tinytuya
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .models import TuyaCredentials


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_") or "device"


def list_devices(hass: HomeAssistant) -> list[dict]:
    """LocalTuya devices with their current cloud-block state."""
    sync = hass.data.get(DOMAIN, {}).get("companion")
    stored = sync.devices if sync else {}
    out = []
    for entry in hass.config_entries.async_entries("localtuya"):
        for dev_id, dev in entry.data.get("devices", {}).items():
            s = stored.get(dev_id, {})
            out.append(
                {
                    "device_id": dev_id,
                    "name": dev.get("friendly_name", dev_id),
                    "ip": dev.get("host", ""),
                    "registered": bool(s),
                    "blocked": bool(s.get("privacy_blocked")),
                    "blocked_since": s.get("blocked_since"),
                }
            )
    return sorted(out, key=lambda d: d["name"].lower())


async def set_block(hass: HomeAssistant, device_id: str, blocked: bool) -> tuple[bool, str]:
    sync = hass.data.get(DOMAIN, {}).get("companion")
    if not sync or not await sync.async_available():
        return False, "Tuya Proxy Companion não está instalado ou não está rodando"
    dev = None
    for entry in hass.config_entries.async_entries("localtuya"):
        dev = entry.data.get("devices", {}).get(device_id) or dev
    if not dev:
        return False, "Aparelho não encontrado no LocalTuya"
    if not dev.get("host"):
        return False, "Aparelho sem IP: reserve um IP fixo para ele no roteador primeiro"
    previous = sync.devices.get(device_id, {})
    # Devices added to the Companion only for the block (not cameras registered
    # for local events) leave it again when unblocked: every Companion entry
    # keeps one local connection open, and lights/switches accept only a few.
    if not blocked and previous.get("added_for_block"):
        await sync.async_remove(device_id)
        return True, "Nuvem liberada"
    ok, msg = await sync.async_upsert(
        slug=previous.get("slug") or _slug(dev.get("friendly_name", "")),
        ip=dev["host"],
        device_id=device_id,
        local_key=dev.get("local_key", ""),
        proxy_enabled=previous.get("proxy_enabled", True),
        privacy_blocked=blocked,
    )
    # Remember when the block started, for the leak check.
    entry = sync.devices.get(device_id)
    if entry is not None:
        entry["blocked_since"] = int(time.time() * 1000) if blocked else None
        if blocked and not previous:
            entry["added_for_block"] = True
        await sync._store.async_save({"devices": sync.devices})  # noqa: SLF001
    if not ok:
        return False, msg
    return True, "Nuvem bloqueada" if blocked else "Nuvem liberada"


def leak_check(creds: TuyaCredentials, device_id: str, since_ms: int) -> dict:
    """Blocking. Count cloud log entries received after `since_ms`."""
    c = tinytuya.Cloud(
        apiRegion=creds.region, apiKey=creds.access_id, apiSecret=creds.access_secret
    )
    now_ms = int(time.time() * 1000)
    res = c.cloudrequest(
        f"/v1.0/devices/{device_id}/logs",
        query={"type": "7", "start_time": since_ms, "end_time": now_ms, "size": 20},
    ) or {}
    logs = (res.get("result") or {}).get("logs") or []
    return {
        "ok": bool(res.get("success", True)),
        "leaking": bool(logs),
        "count": len(logs),
        "last": max((log.get("event_time", 0) for log in logs), default=None),
        "codes": sorted({log.get("code", "") for log in logs})[:10],
    }
