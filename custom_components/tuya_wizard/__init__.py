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
from homeassistant.helpers.storage import Store

from . import card_store, cloud_block, companion, discovery, provisioner, remover
from .const import (
    CONF_FRIGATE_URL,
    CONF_ONVIF_DEFAULT_PASSWORD,
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
PLATFORMS: list[str] = ["binary_sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data["entry"] = entry

    # aiohttp's router raises on a duplicate route name, so re-registering
    # these on a config-entry reload (options update, "Reload" button, a
    # second HA restart cycle) would crash setup. Views can't be unregistered
    # from the router later, so only ever register them the first time.
    if not domain_data.get("views_registered"):
        html = await hass.async_add_executor_job(
            (_STATIC / "index.html").read_text, "utf-8"
        )

        # Views that need the config entry look it up fresh per-request
        # (see _current_entry) rather than capturing this specific entry
        # object — they're only ever constructed this one time, but a full
        # remove-and-re-add of the integration (not just a reload) creates a
        # brand new entry object, which a captured reference would miss.
        hass.http.register_view(TuyaWizardIndexView(html))
        hass.http.register_view(TuyaWizardConfigView(hass))
        hass.http.register_view(TuyaWizardDiscoverView(hass))
        hass.http.register_view(TuyaWizardListView(hass))
        hass.http.register_view(TuyaWizardHomesView(hass))
        hass.http.register_view(TuyaWizardCloudView(hass))
        hass.http.register_view(TuyaWizardProvisionView(hass))
        hass.http.register_view(TuyaWizardRemoveView(hass))
        hass.http.register_view(TuyaWizardCardTemplatesView(hass))
        hass.http.register_view(TuyaWizardCardTemplateSaveView(hass))
        hass.http.register_view(TuyaWizardCardTemplateDeleteView(hass))
        domain_data["views_registered"] = True

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

    # Keep the Tuya Proxy Companion camera list registered across restarts.
    if "companion" not in domain_data:
        sync = companion.CompanionSync(hass)
        await sync.async_start()
        domain_data["companion"] = sync

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Re-run setup after the OptionsFlow updates the entry (new credentials)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    async_remove_panel(hass, "tuya-wizard")
    hass.data[DOMAIN].pop("entry", None)
    return True


def _error(message: str, code: str, status: int) -> web.Response:
    """JSON error body carrying both a display string and a stable code.

    `message` is today's human-readable (Portuguese) text, kept for backward
    compatibility; `error_code` is a stable, language-neutral identifier a
    future frontend can map through HA's own translation strings instead of
    being stuck displaying backend-hardcoded Portuguese to every locale.
    """
    return web.json_response({"error": message, "error_code": code}, status=status)


def _admin_required(request: web.Request) -> web.Response | None:
    """Return a 403 response if the caller isn't an HA admin, else None.

    Every view here manages config entries, provisions/removes devices, or
    writes JS files under /config/www — all admin-level operations, matching
    the require_admin=True sidebar panel these views back.
    """
    user = request.get("hass_user")
    if user is None or not user.is_admin:
        return _error("Admin access required", "admin_required", 403)
    return None


def _current_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """Look up the (single) Tuya Wizard config entry fresh on every call.

    Views hold onto their view instance for the lifetime of the HA process
    (see the views_registered guard above), so they can't cache a specific
    ConfigEntry object — it would go stale across a remove-and-re-add.
    """
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None


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
    """Serves the static wizard shell — no data, nothing to protect here.

    This is loaded via an `iframe` sidebar panel, so the browser navigates
    to it directly (no Authorization header, no session cookie HA would
    recognize) — requiring auth here would make the panel unloadable. The
    page's own JS reads the frontend's access token from localStorage
    (same-origin, so the iframe can see it) and attaches it to every call
    against the real data endpoints below, which all still require auth.
    """

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

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        if (resp := _admin_required(request)) is not None:
            return resp
        entry = _current_entry(self._hass)
        d = entry.data if entry else {}
        return self.json(
            {
                "tuya_access_id": d.get(CONF_TUYA_ACCESS_ID, ""),
                "tuya_access_secret": d.get(CONF_TUYA_ACCESS_SECRET, ""),
                "tuya_region": d.get(CONF_TUYA_REGION, "us"),
                "onvif_default_password": d.get(CONF_ONVIF_DEFAULT_PASSWORD, ""),
                "frigate_url": d.get(CONF_FRIGATE_URL, ""),
            }
        )

    async def post(self, request: web.Request) -> web.Response:
        if (resp := _admin_required(request)) is not None:
            return resp
        entry = _current_entry(self._hass)
        if entry is None:
            return _error("Integração não configurada", "entry_not_found", 404)
        try:
            body = await request.json()
        except Exception:
            return _error("JSON inválido", "invalid_json", 400)

        new_data = dict(entry.data)
        for key in (
            CONF_TUYA_ACCESS_ID,
            CONF_TUYA_ACCESS_SECRET,
            CONF_TUYA_REGION,
            CONF_ONVIF_DEFAULT_PASSWORD,
            CONF_FRIGATE_URL,
        ):
            if key in body:
                new_data[key] = body[key]

        self._hass.config_entries.async_update_entry(entry, data=new_data)
        return self.json({"ok": True})


class TuyaWizardDiscoverView(HomeAssistantView):
    """POST /api/tuya_wizard/discover — SSE stream of discovered devices."""

    url = "/api/tuya_wizard/discover"
    name = "api:tuya_wizard:discover"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: web.Request) -> StreamResponse:
        if (resp := _admin_required(request)) is not None:
            return resp
        try:
            body = await request.json()
            req = DiscoverRequest(**body)
        except Exception:
            return _error("JSON inválido", "invalid_json", 400)

        entry = _current_entry(self._hass)
        creds = req.credentials or (_build_creds(entry.data) if entry else None)
        if not creds:
            return _error(
                "Credenciais Tuya não configuradas", "credentials_missing", 422
            )

        response = StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
        await response.prepare(request)

        async for chunk in discovery.stream(self._hass, creds, req.home_id):
            await response.write(chunk.encode())

        await response.write_eof()
        return response


class TuyaWizardHomesView(HomeAssistantView):
    """GET /api/tuya_wizard/homes — Tuya homes (spaces) seen by the project."""

    url = "/api/tuya_wizard/homes"
    name = "api:tuya_wizard:homes"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        if (resp := _admin_required(request)) is not None:
            return resp
        entry = _current_entry(self._hass)
        creds = _build_creds(entry.data) if entry else None
        if not creds:
            return _error("Credenciais Tuya não configuradas", "credentials_missing", 422)
        try:
            homes = await self._hass.async_add_executor_job(discovery.list_homes, creds)
        except Exception as exc:  # cloud errors are shown to the user, not raised
            _LOGGER.warning("Listing Tuya homes failed: %s", exc)
            return _error("Falha ao consultar as casas na nuvem Tuya", "homes_failed", 502)
        # The default home is stored server-side so every browser starts on the same home;
        # installer accounts also see their clients' homes, which must not be picked by accident.
        prefs = await _prefs_store(self._hass).async_load() or {}
        default = prefs.get("home_id")
        for h in homes:
            h["default"] = bool(default) and h.get("id") == default
        homes.sort(key=lambda h: (not h["default"], not h.get("readable"), -int(h.get("devices") or 0)))
        return self.json(homes)

    async def post(self, request: web.Request) -> web.Response:
        """Save the default home: {"home_id": "<space id>"} (empty/null clears it)."""
        if (resp := _admin_required(request)) is not None:
            return resp
        try:
            body = await request.json()
        except ValueError:
            return _error("JSON inválido", "invalid_json", 400)
        home_id = (body or {}).get("home_id") or None
        if home_id is not None and not isinstance(home_id, str):
            return _error("home_id inválido", "invalid_home", 400)
        await _prefs_store(self._hass).async_save({"home_id": home_id})
        return self.json({"ok": True, "home_id": home_id})


def _prefs_store(hass: HomeAssistant) -> Store:
    """Small private store for user preferences of the wizard (default home)."""
    return Store(hass, 1, f"{DOMAIN}.prefs", private=True)


class TuyaWizardListView(HomeAssistantView):
    """GET /api/tuya_wizard/devices — devices currently in LocalTuya."""

    url = "/api/tuya_wizard/devices"
    name = "api:tuya_wizard:devices"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        if (resp := _admin_required(request)) is not None:
            return resp
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

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: web.Request) -> StreamResponse:
        if (resp := _admin_required(request)) is not None:
            return resp
        try:
            body = await request.json()
            req = ProvisionRequest(**body)
        except Exception:
            return _error("JSON inválido", "invalid_json", 400)

        entry = _current_entry(self._hass)
        frigate_url = entry.data.get(CONF_FRIGATE_URL) if entry else None

        response = StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
        await response.prepare(request)

        async for chunk in provisioner.run(
            self._hass, req.devices, req.default_local_password, frigate_url
        ):
            await response.write(chunk.encode())

        await response.write_eof()
        return response


class TuyaWizardCloudView(HomeAssistantView):
    """GET/POST /api/tuya_wizard/cloud — per-device cloud block (needs the Companion)."""

    url = "/api/tuya_wizard/cloud"
    name = "api:tuya_wizard:cloud"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        if (resp := _admin_required(request)) is not None:
            return resp
        sync = self._hass.data.get(DOMAIN, {}).get("companion")
        available = bool(sync and await sync.async_available())
        return self.json(
            {"companion": available, "devices": cloud_block.list_devices(self._hass)}
        )

    async def post(self, request: web.Request) -> web.Response:
        if (resp := _admin_required(request)) is not None:
            return resp
        try:
            body = await request.json()
            device_id = str(body["device_id"])
            action = body.get("action", "block")
        except Exception:
            return _error("JSON inválido", "invalid_json", 400)
        if action == "leak_check":
            entry = _current_entry(self._hass)
            creds = _build_creds(entry.data) if entry else None
            sync = self._hass.data.get(DOMAIN, {}).get("companion")
            since = ((sync.devices if sync else {}).get(device_id) or {}).get("blocked_since")
            if not creds or not since:
                return _error("Bloqueio sem data de início ou sem credenciais", "no_block", 422)
            try:
                result = await self._hass.async_add_executor_job(
                    cloud_block.leak_check, creds, device_id, since
                )
            except Exception as exc:
                _LOGGER.warning("Leak check failed: %s", exc)
                return _error("Falha ao consultar a nuvem Tuya", "leak_check_failed", 502)
            return self.json(result)
        ok, msg = await cloud_block.set_block(
            self._hass, device_id, action == "block"
        )
        return self.json({"ok": ok, "detail": msg}, status_code=200 if ok else 409)


class TuyaWizardRemoveView(HomeAssistantView):
    """POST /api/tuya_wizard/remove — remove a device from LocalTuya."""

    url = "/api/tuya_wizard/remove"
    name = "api:tuya_wizard:remove"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: web.Request) -> web.Response:
        if (resp := _admin_required(request)) is not None:
            return resp
        try:
            body = await request.json()
            req = RemoveRequest(**body)
        except Exception:
            return _error("JSON inválido", "invalid_json", 400)

        ok, msg = await remover.run(self._hass, req.device_id)
        return self.json({"ok": ok, "detail": msg})


class TuyaWizardCardTemplatesView(HomeAssistantView):
    """GET /api/tuya_wizard/card_templates — list all card templates."""

    url = "/api/tuya_wizard/card_templates"
    name = "api:tuya_wizard:card_templates"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        if (resp := _admin_required(request)) is not None:
            return resp
        templates = await card_store.load(self._hass)
        return self.json([t.model_dump() for t in templates])


class TuyaWizardCardTemplateSaveView(HomeAssistantView):
    """POST /api/tuya_wizard/card_templates/save — upsert + deploy a template."""

    url = "/api/tuya_wizard/card_templates/save"
    name = "api:tuya_wizard:card_templates:save"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: web.Request) -> web.Response:
        if (resp := _admin_required(request)) is not None:
            return resp
        try:
            body = await request.json()
            req = CardTemplateSaveRequest(**body)
        except Exception:
            return _error("JSON inválido", "invalid_json", 400)

        await card_store.upsert(self._hass, req.template)
        ok, msg = await card_store.deploy_card(self._hass, req.template)
        return self.json({"ok": ok, "detail": msg})


class TuyaWizardCardTemplateDeleteView(HomeAssistantView):
    """POST /api/tuya_wizard/card_templates/delete — delete a custom template."""

    url = "/api/tuya_wizard/card_templates/delete"
    name = "api:tuya_wizard:card_templates:delete"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: web.Request) -> web.Response:
        if (resp := _admin_required(request)) is not None:
            return resp
        try:
            body = await request.json()
            req = CardTemplateDeleteRequest(**body)
        except Exception:
            return _error("JSON inválido", "invalid_json", 400)

        deleted = await card_store.delete(self._hass, req.template_id)
        if not deleted:
            return _error(
                "Template não encontrado ou é builtin",
                "template_not_found_or_builtin",
                404,
            )
        return self.json({"ok": True})
