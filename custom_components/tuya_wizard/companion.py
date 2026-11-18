"""Tuya Proxy Companion integration: keep the camera list registered.

The Companion add-on keeps its camera list only in memory. If the add-on (or
Home Assistant) restarts, the list is gone: local motion events stop reaching
Frigate and, when the cloud block is on, the camera talks to the Tuya cloud
again. This module stores the list in Home Assistant (.storage) and pushes it
back to the Companion whenever it is missing or out of date.

Each entry: slug, ip, device_id, local_key, proxy_enabled (local event
listener), privacy_blocked (cloud block via ARP + iptables).

Note on the cloud block: Tuya cameras validate the cloud TLS certificate, so
the Companion cannot impersonate the cloud. With the block on, many cameras
stop generating motion events at all (they need the cloud round trip first).
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta

import aiohttp
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = f"{DOMAIN}.companion"
STORAGE_VERSION = 1
SYNC_INTERVAL = timedelta(minutes=5)
_SLUG_SUFFIX = "tuya_proxy_companion"
_DEFAULT_PORT = 8765


class CompanionSync:
    """Owns the stored camera list and keeps the add-on in sync with it."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY, private=True)
        self.devices: dict[str, dict] = {}
        self._unsub = None
        self._base_url: str | None = None

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.devices = dict(data.get("devices", {}))
        if not self.devices:
            # First run: adopt whatever the add-on already has (e.g. a camera
            # registered by hand or by the older ekaza_wizard).
            await self._adopt_current()
        self._unsub = async_track_time_interval(self.hass, self._tick, SYNC_INTERVAL)
        # The add-on may start after Home Assistant; try again shortly.
        async_call_later(self.hass, 30, self._tick)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    # -- add-on discovery -------------------------------------------------

    async def _find_base_url(self) -> str | None:
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            return None
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                "http://supervisor/addons",
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                addons = (await r.json()).get("data", {}).get("addons", [])
            for addon in addons:
                slug = addon.get("slug", "")
                if slug.endswith(_SLUG_SUFFIX) and addon.get("state") == "started":
                    async with session.get(
                        f"http://supervisor/addons/{slug}/info",
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as r:
                        info = (await r.json()).get("data", {})
                    port = (info.get("options") or {}).get("api_port", _DEFAULT_PORT)
                    host = info.get("hostname") or slug.replace("_", "-")
                    return f"http://{host}:{port}"
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            _LOGGER.debug("Companion lookup failed: %s", exc)
        return None

    async def async_available(self) -> bool:
        self._base_url = await self._find_base_url()
        return self._base_url is not None

    async def _get_status(self) -> dict | None:
        if not self._base_url and not await self.async_available():
            return None
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                f"{self._base_url}/status", timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                return await r.json()
        except (aiohttp.ClientError, TimeoutError, ValueError):
            self._base_url = None
            return None

    async def _adopt_current(self) -> None:
        status = await self._get_status()
        cams = (status or {}).get("cameras") or []
        adopted = {
            c["device_id"]: {k: c.get(k) for k in (
                "slug", "ip", "device_id", "local_key", "proxy_enabled", "privacy_blocked")}
            for c in cams
            if c.get("device_id") and c.get("local_key")
        }
        if adopted:
            self.devices = adopted
            await self._store.async_save({"devices": self.devices})
            _LOGGER.info("Companion: adopted %d camera(s) already registered", len(adopted))

    # -- sync ---------------------------------------------------------------

    async def async_push(self) -> bool:
        if not self._base_url and not await self.async_available():
            return False
        session = async_get_clientsession(self.hass)
        try:
            async with session.post(
                f"{self._base_url}/cameras",
                json={"cameras": list(self.devices.values())},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                ok = r.status == 200 and bool((await r.json()).get("ok"))
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            _LOGGER.warning("Companion: push failed: %s", exc)
            self._base_url = None
            return False
        if ok:
            _LOGGER.info("Companion: %d camera(s) registered", len(self.devices))
        return ok

    def _in_sync(self, status: dict) -> bool:
        current = {
            c.get("device_id"): (c.get("ip"), bool(c.get("proxy_enabled")), bool(c.get("privacy_blocked")))
            for c in status.get("cameras") or []
        }
        wanted = {
            d["device_id"]: (d.get("ip"), bool(d.get("proxy_enabled")), bool(d.get("privacy_blocked")))
            for d in self.devices.values()
        }
        return current == wanted

    async def _tick(self, _now=None) -> None:
        status = await self._get_status()
        if status is None or self._in_sync(status):
            return
        _LOGGER.info("Companion: camera list differs from the stored one, re-sending")
        await self.async_push()

    # -- edits (used by the provisioner and the API) ------------------------

    async def async_upsert(
        self,
        *,
        slug: str,
        ip: str,
        device_id: str,
        local_key: str,
        proxy_enabled: bool = True,
        privacy_blocked: bool | None = None,
    ) -> tuple[bool, str]:
        previous = self.devices.get(device_id, {})
        self.devices[device_id] = {
            "slug": slug,
            "ip": ip,
            "device_id": device_id,
            "local_key": local_key,
            "proxy_enabled": proxy_enabled,
            # Keep an existing block decision on re-provision unless told otherwise.
            "privacy_blocked": bool(previous.get("privacy_blocked", False))
            if privacy_blocked is None
            else privacy_blocked,
        }
        await self._store.async_save({"devices": self.devices})
        if await self.async_push():
            return True, "Registrada no Tuya Proxy Companion (eventos locais de movimento)"
        return False, "Companion indisponível: registro salvo e reenviado quando ele voltar"

    async def async_remove(self, device_id: str) -> None:
        if self.devices.pop(device_id, None) is not None:
            await self._store.async_save({"devices": self.devices})
            await self.async_push()
