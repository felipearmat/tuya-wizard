"""Tuya Wizard — 1-click LocalTuya setup for all Tuya devices."""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web
from aiohttp.web_response import StreamResponse
from homeassistant.components.frontend import (
    async_register_built_in_panel,
    async_remove_panel,
)
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import card_store, discovery, provisioner, remover
from .const import (
    CONF_TUYA_ACCESS_ID,
    CONF_TUYA_ACCESS_SECRET,
    CONF_TUYA_REGION,
    DOMAIN,
)
from .models import (
    CardTemplateDeleteRequest,
    CardTemplateSaveRequest,
    DiscoverRequest,
    ProvisionRequest,
    RemoveRequest,
    TuyaCredentials,
)

_LOGGER = logging.getLogger(__name__)
_STATIC = Path(__file__).parent / "static"
PLATFORMS: list[str] = []


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["entry"] = entry

    html = await hass.async_add_executor_job(
        (_STATIC / "index.html").read_text, "utf-8"
    )

    hass.http.register_view(TuyaWizardIndexView(html))
    hass.http.register_view(TuyaWizardConfigView(hass, entry))
    hass.http.register_view(TuyaWizardDiscoverView(hass, entry))
    hass.http.register_view(TuyaWizardListView(hass))
    hass.http.register_view(TuyaWizardProvisionView(hass, entry))
    hass.http.register_view(TuyaWizardRemoveView(hass))
    hass.http.register_view(TuyaWizardCardTemplatesView(hass))
    hass.http.register_view(TuyaWizardCardTemplateSaveView(hass))
    hass.http.register_view(TuyaWizardCardTemplateDeleteView(hass))

    try:
        async_register_built_in_panel(
            hass,
            component_name="iframe",
            sidebar_title="Tuya Wizard",
            sidebar_icon="mdi:toy-brick",
            frontend_url_path="tuya-wizard",
            config={"url": "/api/tuya_wizard/"},
            require_admin=True,
        )
    except ValueError:
        pass  # panel already registered

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    async_remove_panel(hass, "tuya-wizard")
    return True


def _build_creds(data: dict) -> TuyaCredentials | None:
    if not data.get(CONF_TUYA_ACCESS_ID):
        return None
    return TuyaCredentials(
        access_id=data[CONF_TUYA_ACCESS_ID],
        access_secret=data[CONF_TUYA_ACCESS_SECRET],
        region=data.get(CONF_TUYA_REGION, "us"),
    )


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class TuyaWizardIndexView(HomeAssistantView):
    url = "/api/tuya_wizard/"
    name = "api:tuya_wizard:index"
    requires_auth = False

    def __init__(self, html: str) -> None:
        self._html = html

    async def get(self, request: web.Request) -> web.Response:
        return web.Response(text=self._html, content_type="text/html", charset="utf-8")


class TuyaWizardConfigView(HomeAssistantView):
    url = "/api/tuya_wizard/config"
    name = "api:tuya_wizard:config"
    requires_auth = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry

    async def get(self, request: web.Request) -> web.Response:
        d = self._entry.data
        return self.json(
            {
                "tuya_access_id": d.get(CONF_TUYA_ACCESS_ID, ""),
                "tuya_access_secret": d.get(CONF_TUYA_ACCESS_SECRET, ""),
                "tuya_region": d.get(CONF_TUYA_REGION, "us"),
            }
        )

    async def post(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "JSON inválido"}, status_code=400)

        new_data = dict(self._entry.data)
        for key in (CONF_TUYA_ACCESS_ID, CONF_TUYA_ACCESS_SECRET, CONF_TUYA_REGION):
            if key in body:
                new_data[key] = body[key]

        self._hass.config_entries.async_update_entry(self._entry, data=new_data)
        return self.json({"ok": True})


class TuyaWizardDiscoverView(HomeAssistantView):
    """POST /api/tuya_wizard/discover — SSE stream of discovered devices."""

    url = "/api/tuya_wizard/discover"
    name = "api:tuya_wizard:discover"
    requires_auth = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry

    async def post(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            req = DiscoverRequest(**body)
        except Exception:
            return self.json({"error": "JSON inválido"}, status_code=400)

        creds = req.credentials or _build_creds(self._entry.data)
        if not creds:
            return self.json(
                {"error": "Credenciais Tuya não configuradas"}, status_code=422
            )

        devices = await discovery.run(self._hass, creds)
        return self.json([d.model_dump() for d in devices])


class TuyaWizardListView(HomeAssistantView):
    """GET /api/tuya_wizard/devices — devices currently in LocalTuya."""

    url = "/api/tuya_wizard/devices"
    name = "api:tuya_wizard:devices"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        devices: list[dict] = []
        for entry in self._hass.config_entries.async_entries("localtuya"):
            for dev_id, dev in entry.data.get("devices", {}).items():
                devices.append(
                    {
                        "device_id": dev_id,
                        "name": dev.get("friendly_name", dev_id),
                        "ip": dev.get("host", ""),
                        "entity_count": len(dev.get("entities", [])),
                    }
                )
        return self.json(sorted(devices, key=lambda d: d["name"].lower()))


class TuyaWizardProvisionView(HomeAssistantView):
    """POST /api/tuya_wizard/provision — SSE stream provisioning devices."""

    url = "/api/tuya_wizard/provision"
    name = "api:tuya_wizard:provision"
    requires_auth = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry

    async def post(self, request: web.Request) -> StreamResponse:
        try:
            body = await request.json()
            req = ProvisionRequest(**body)
        except Exception:
            return self.json({"error": "JSON inválido"}, status_code=400)

        response = StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
        await response.prepare(request)

        async for chunk in provisioner.run(self._hass, req.devices):
            await response.write(chunk.encode())

        await response.write_eof()
        return response


class TuyaWizardRemoveView(HomeAssistantView):
    """POST /api/tuya_wizard/remove — remove a device from LocalTuya."""

    url = "/api/tuya_wizard/remove"
    name = "api:tuya_wizard:remove"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            req = RemoveRequest(**body)
        except Exception:
            return self.json({"error": "JSON inválido"}, status_code=400)

        ok, msg = await remover.run(self._hass, req.device_id)
        return self.json({"ok": ok, "detail": msg})


class TuyaWizardCardTemplatesView(HomeAssistantView):
    """GET /api/tuya_wizard/card_templates — list all card templates."""

    url = "/api/tuya_wizard/card_templates"
    name = "api:tuya_wizard:card_templates"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        templates = await card_store.load(self._hass)
        return self.json([t.model_dump() for t in templates])


class TuyaWizardCardTemplateSaveView(HomeAssistantView):
    """POST /api/tuya_wizard/card_templates/save — upsert + deploy a template."""

    url = "/api/tuya_wizard/card_templates/save"
    name = "api:tuya_wizard:card_templates:save"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            req = CardTemplateSaveRequest(**body)
        except Exception:
            return self.json({"error": "JSON inválido"}, status_code=400)

        await card_store.upsert(self._hass, req.template)
        ok, msg = await card_store.deploy_card(self._hass, req.template)
        return self.json({"ok": ok, "detail": msg})


class TuyaWizardCardTemplateDeleteView(HomeAssistantView):
    """POST /api/tuya_wizard/card_templates/delete — delete a custom template."""

    url = "/api/tuya_wizard/card_templates/delete"
    name = "api:tuya_wizard:card_templates:delete"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            req = CardTemplateDeleteRequest(**body)
        except Exception:
            return self.json({"error": "JSON inválido"}, status_code=400)

        deleted = await card_store.delete(self._hass, req.template_id)
        if not deleted:
            return self.json(
                {"error": "Template não encontrado ou é builtin"}, status_code=404
            )
        return self.json({"ok": True})
