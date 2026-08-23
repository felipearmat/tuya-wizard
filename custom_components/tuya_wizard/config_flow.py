"""Config flow — one-step setup, plus an options flow to edit credentials.

The wizard's own /api/tuya_wizard/config REST endpoint can also update the
Tuya credentials (for the eventual in-panel UI), but the options flow gives
every user a working path through HA's standard "Configure" button even
before that frontend exists.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_ONVIF_DEFAULT_PASSWORD,
    CONF_TUYA_ACCESS_ID,
    CONF_TUYA_ACCESS_SECRET,
    CONF_TUYA_REGION,
    DOMAIN,
)


def _schema(defaults: dict, *, include_onvif: bool = False) -> vol.Schema:
    fields = {
        vol.Optional(
            CONF_TUYA_ACCESS_ID, default=defaults.get(CONF_TUYA_ACCESS_ID, "")
        ): str,
        vol.Optional(
            CONF_TUYA_ACCESS_SECRET,
            default=defaults.get(CONF_TUYA_ACCESS_SECRET, ""),
        ): str,
        vol.Optional(
            CONF_TUYA_REGION, default=defaults.get(CONF_TUYA_REGION, "us")
        ): vol.In(["us", "eu", "in", "cn"]),
    }
    if include_onvif:
        # Only offered once devices likely exist to configure — there's no
        # point asking for it during first-run setup, before any camera has
        # even been discovered.
        fields[
            vol.Optional(
                CONF_ONVIF_DEFAULT_PASSWORD,
                default=defaults.get(CONF_ONVIF_DEFAULT_PASSWORD, ""),
            )
        ] = str
    return vol.Schema(fields)


class TuyaWizardConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        if user_input is not None:
            return self.async_create_entry(title="Tuya Wizard", data=user_input)

        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> TuyaWizardOptionsFlow:
        return TuyaWizardOptionsFlow(config_entry)


class TuyaWizardOptionsFlow(config_entries.OptionsFlow):
    """Edit the saved Tuya credentials via Settings > Integrations > Configure."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None) -> FlowResult:
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                self._config_entry, data=user_input
            )
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=_schema(self._config_entry.data, include_onvif=True),
        )
