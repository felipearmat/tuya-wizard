"""Generic local capability enablement.

Some Tuya devices ship with local-control features (e.g. a camera's ONVIF
server) disabled by default; enabling them is itself a local DP write, not
part of the device's normal DP set exposed to LocalTuya. Ported from the DP
recipe ekaza-wizard uses for its camera line (DP 237/238/134) and made
declarative per device `category` so other device types can register their
own profile without touching the write logic.
"""

from __future__ import annotations

import json
import logging

import tinytuya

from .const import CAMERA_CATEGORIES
from .models import DeviceInfo, StepResult

_LOGGER = logging.getLogger(__name__)

# DP writes that unlock local control on Tuya's camera/IPC line — same
# profile for every category in CAMERA_CATEGORIES, since they share the same
# underlying IPC DP layout. kind="dp" writes a fixed value. kind="password_dp"
# writes a {"old": ..., "new": ...} JSON blob, trying "" first (factory
# default) then the supplied secret again (already-configured / re-provision
# case).
_CAMERA_PROFILE: list[dict] = [
    {"dp": 237, "kind": "dp", "value": True, "label": "Ativar ONVIF"},
    {"dp": 238, "kind": "password_dp", "label": "Definir senha ONVIF"},
    {"dp": 134, "kind": "dp", "value": True, "label": "Detecção de movimento nativa"},
]

# Tuya device `category` -> ordered list of DP writes that unlock local
# control. Other device types can register their own profile here without
# touching the write logic in apply()/_write_password_dp() below.
_CAPABILITY_PROFILES: dict[str, list[dict]] = {
    category: _CAMERA_PROFILE for category in CAMERA_CATEGORIES
}


def has_profile(category: str) -> bool:
    return category in _CAPABILITY_PROFILES


def requires_secret(category: str) -> bool:
    return any(
        entry["kind"] == "password_dp"
        for entry in _CAPABILITY_PROFILES.get(category, [])
    )


def _is_error(result: object) -> str | None:
    if isinstance(result, dict) and result.get("Error"):
        return str(result["Error"])
    return None


def _write_password_dp(dev: tinytuya.Device, dp_id: int, secret: str) -> None:
    last_error = "sem resposta do dispositivo"
    for old in ("", secret):
        result = dev.set_value(dp_id, json.dumps({"old": old, "new": secret}))
        error = _is_error(result)
        if error is None:
            return
        last_error = error
    raise RuntimeError(last_error)


def apply(device: DeviceInfo, secret: str | None) -> list[StepResult]:
    """Run this device's capability profile, if any.

    Blocking (opens a local socket) — call via hass.async_add_executor_job.
    """
    profile = _CAPABILITY_PROFILES.get(device.category, [])
    if not profile or not device.ip:
        return []

    dev = tinytuya.Device(
        device.device_id,
        device.ip,
        device.local_key,
        version=float(device.protocol_version),
    )
    dev.set_socketTimeout(5)

    results: list[StepResult] = []
    for entry in profile:
        label = entry["label"]
        try:
            if entry["kind"] == "password_dp":
                if not secret:
                    results.append(
                        StepResult(
                            step=label,
                            status="skip",
                            detail="Nenhuma senha padrão informada",
                        )
                    )
                    continue
                _write_password_dp(dev, entry["dp"], secret)
            else:
                error = _is_error(dev.set_value(entry["dp"], entry["value"]))
                if error:
                    raise RuntimeError(error)
            results.append(StepResult(step=label, status="ok"))
        except Exception as exc:
            _LOGGER.debug(
                "Local-enable DP %s failed for %s: %s",
                entry["dp"],
                device.device_id[:8],
                exc,
            )
            results.append(StepResult(step=label, status="error", detail=str(exc)))

    return results
