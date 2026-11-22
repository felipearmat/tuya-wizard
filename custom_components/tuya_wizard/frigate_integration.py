"""Frigate/go2rtc integration — configures camera audio+video streaming.

Optional and skipped entirely when no Frigate URL is configured (it varies
per installation and can't be guessed). When set, adds/updates a go2rtc
stream + Frigate camera block for a provisioned camera device using the
"ffmpeg:...#video=copy#audio=opus" recipe: several Tuya IPC cameras' raw
RTSP audio (PCM mu-law) isn't tagged correctly by the container formats
go2rtc's ffmpeg producer uses internally and gets silently dropped unless
told to transcode to a properly-tagged codec — Opus, which also happens to
be WebRTC's native audio codec, so the browser needs no further
transcoding to play it back live.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time

import aiohttp
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import ONVIF_USERNAME
from .models import DeviceInfo

_LOGGER = logging.getLogger(__name__)

# Common default when Frigate runs as a HA OS add-on with host networking
# on the same machine as HA Core — tried only when no URL was configured.
DEFAULT_FRIGATE_URL = "http://localhost:5000"


async def resolve_url(hass: HomeAssistant, configured_url: str | None) -> str | None:
    """Return the Frigate URL to use, or None to skip Frigate integration.

    An explicitly configured URL is trusted as-is (no health check — if
    it's wrong, the caller should see a real error, not a silent skip).
    With nothing configured, probe the common HA-OS-addon default and use
    it only if it actually answers: most installs have no Frigate at all,
    and that must stay a silent no-op, not surface as an error.
    """
    if configured_url:
        return configured_url.rstrip("/")

    session = async_get_clientsession(hass)
    try:
        async with session.get(
            f"{DEFAULT_FRIGATE_URL}/api/version",
            timeout=aiohttp.ClientTimeout(total=3),
        ) as resp:
            if resp.status == 200:
                return DEFAULT_FRIGATE_URL
    except Exception as exc:
        _LOGGER.debug("No Frigate found at default URL: %s", exc)
    return None

# (port, path) conventions seen across Tuya-ecosystem IPC cameras so far —
# tried in order until one responds with a real video stream. Different
# camera models/firmwares use different conventions; there's no single
# universal one, so this list is expected to grow as more models are seen.
_RTSP_CANDIDATES: list[tuple[int, str]] = [
    (8554, "stream0"),
    (554, "onvif1"),
    (554, "stream1"),
]


def _ffprobe_codec_types(url: str) -> set[str]:
    """Blocking. Return the set of codec_types (video/audio) ffprobe finds at url."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-rtsp_transport", "tcp",
                "-timeout", "5000000",
                "-show_entries", "stream=codec_type",
                "-of", "json",
                url,
            ],
            capture_output=True,
            timeout=10,
        )
        # Some cameras (e.g. EKAZA T5293) send non-UTF-8 bytes in the SDP that ffprobe echoes on
        # stderr; text=True made subprocess raise UnicodeDecodeError and the probe wrongly
        # reported "no stream". Decode leniently and only parse stdout (the JSON).
        data = json.loads(result.stdout.decode("utf-8", "replace") or "{}")
        return {s.get("codec_type") for s in data.get("streams", [])}
    except Exception as exc:
        _LOGGER.debug("ffprobe failed for %s: %s", url, exc)
        return set()


def _probe_rtsp(ip: str, password: str, attempts: int = 4, wait_s: float = 5.0) -> tuple[int, str] | None:
    """Blocking. Try known port/path conventions; return the first with video.

    Retries a few times: right after the local-enable step writes the ONVIF
    password, many cameras restart their RTSP server for a few seconds, and a
    single probe in that window wrongly reports "no stream".
    """
    for attempt in range(attempts):
        for port, path in _RTSP_CANDIDATES:
            url = f"rtsp://{ONVIF_USERNAME}:{password}@{ip}:{port}/{path}"
            if "video" in _ffprobe_codec_types(url):
                return port, path
        if attempt < attempts - 1:
            time.sleep(wait_s)
    return None


async def configure_camera(
    hass: HomeAssistant, frigate_url: str, device: DeviceInfo, password: str
) -> tuple[bool, str]:
    """Add/update this device's go2rtc stream + Frigate camera block.

    Only touches the two things needed for a working audio+video card;
    every other Frigate setting (recording retention, detection objects,
    other cameras) is left alone. Saves but does not restart Frigate — a
    restart briefly interrupts every camera's recording, so that's left to
    the user to do deliberately once they're ready.
    """
    if not device.ip:
        return False, "IP do dispositivo desconhecido"

    probed = await hass.async_add_executor_job(_probe_rtsp, device.ip, password)
    if probed is None:
        return False, "Nenhuma porta/caminho RTSP conhecido respondeu nessa câmera"
    port, path = probed

    session = async_get_clientsession(hass)
    try:
        async with session.get(f"{frigate_url}/api/config/raw") as resp:
            # Frigate 0.18 answers a JSON-encoded string but labels it text/plain; older versions
            # used application/json. Accept both (and plain YAML) instead of trusting the header.
            body = await resp.text()
            try:
                decoded = json.loads(body)
                raw_text = decoded if isinstance(decoded, str) else body
            except ValueError:
                raw_text = body
    except Exception as exc:
        return False, f"Falha ao ler config do Frigate: {exc}"

    config = yaml.safe_load(raw_text) or {}
    slug = device.slug

    stream_url = (
        f"ffmpeg:rtsp://{ONVIF_USERNAME}:{password}@{device.ip}:{port}/{path}"
        "#video=copy#audio=opus"
    )
    config.setdefault("go2rtc", {}).setdefault("streams", {})[slug] = [stream_url]

    cameras = config.setdefault("cameras", {})
    cameras.setdefault(slug, {}).update(
        {
            "ffmpeg": {
                "inputs": [
                    {
                        "path": f"rtsp://127.0.0.1:8554/{slug}",
                        "roles": ["record", "detect"],
                    }
                ]
            },
            "detect": {"width": 640, "height": 360, "fps": 5},
            "record": {"enabled": True},
        }
    )

    new_raw = yaml.safe_dump(config, sort_keys=False)
    try:
        async with session.post(
            f"{frigate_url}/api/config/save",
            params={"save_option": "saveonly"},
            data=new_raw.encode("utf-8"),
            headers={"Content-Type": "text/plain"},
        ) as resp:
            if resp.status != 200:
                detail = await resp.text()
                return False, f"Frigate rejeitou a configuração: {detail}"
    except Exception as exc:
        return False, f"Falha ao salvar configuração do Frigate: {exc}"

    return True, (
        f"Câmera '{slug}' configurada no Frigate (porta {port}/{path}) — "
        "reinicie o Frigate para aplicar"
    )
