"""Motion binary sensor per camera registered in the Tuya Proxy Companion.

The Companion turns each local camera event (Tuya DP212 "ipc_motion") into a
manual "motion" event in Frigate. This platform polls Frigate's event API for
those cameras and turns a Home Assistant motion sensor on for HOLD seconds after
each event, so automations, history and voice announcements can use them
without touching the Companion add-on.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

import aiohttp
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from . import frigate_integration
from .const import CONF_FRIGATE_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

POLL = timedelta(seconds=10)
HOLD = 30  # seconds the sensor stays on after the last event


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    sync = hass.data.get(DOMAIN, {}).get("companion")
    cams = [
        d
        for d in (sync.devices.values() if sync else [])
        if d.get("proxy_enabled") and not d.get("added_for_block") and d.get("slug")
    ]
    if not cams:
        return
    url = await frigate_integration.resolve_url(hass, entry.data.get(CONF_FRIGATE_URL))
    if not url:
        _LOGGER.info("Motion sensors skipped: Frigate not found")
        return
    entities = [CameraMotionSensor(d["slug"], d["device_id"]) for d in cams]
    async_add_entities(entities)

    session = async_get_clientsession(hass)

    async def _poll(_now=None) -> None:
        for ent in entities:
            await ent.async_poll(session, url)

    entry.async_on_unload(async_track_time_interval(hass, _poll, POLL))


class CameraMotionSensor(BinarySensorEntity):
    """On while the camera's own motion detection reported an event recently."""

    _attr_device_class = BinarySensorDeviceClass.MOTION
    _attr_has_entity_name = False
    _attr_should_poll = False

    def __init__(self, slug: str, device_id: str) -> None:
        self._slug = slug
        self._attr_unique_id = f"{DOMAIN}_{device_id}_motion"
        self._attr_name = f"Movimento da câmera {slug.replace('_', ' ')}"
        self._last_event: float | None = None
        self._seen_since = time.time()
        self._events_today = 0
        self._day = time.strftime("%Y-%m-%d")

    @property
    def is_on(self) -> bool:
        return self._last_event is not None and time.time() - self._last_event < HOLD

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "camera": self._slug,
            "ultimo_evento": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(self._last_event))
            if self._last_event
            else None,
            "eventos_hoje": self._events_today,
            "fonte": "detecção da própria câmera (Tuya Proxy Companion → Frigate)",
        }

    async def async_poll(self, session: aiohttp.ClientSession, url: str) -> None:
        was_on = self.is_on
        try:
            async with session.get(
                f"{url}/api/events",
                params={"camera": self._slug, "label": "motion", "after": int(self._seen_since), "limit": 50},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                events = await r.json() if r.status == 200 else []
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            _LOGGER.debug("Frigate poll failed for %s: %s", self._slug, exc)
            events = []
        today = time.strftime("%Y-%m-%d")
        if today != self._day:
            self._day, self._events_today = today, 0
        new = [e for e in events if e.get("start_time", 0) > self._seen_since]
        if new:
            newest = max(e["start_time"] for e in new)
            self._seen_since = newest
            self._last_event = newest
            self._events_today += len(new)
        if new or was_on != self.is_on:
            self.async_write_ha_state()
