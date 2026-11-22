# Changelog

## 0.7.4

- Frigate step: the RTSP probe no longer fails on cameras that send non-UTF-8
  bytes in the SDP (ffprobe output is decoded leniently; before, the decode
  error made every candidate look like "no stream").
- Frigate step: reading `/api/config/raw` accepts the JSON-encoded string that
  Frigate 0.18 serves as `text/plain` (and plain YAML), instead of requiring an
  `application/json` content type.

## 0.7.3

- Removing a device now also removes it from the Tuya Proxy Companion when it was
  registered there (cameras), so the Companion stops proxying/blocking a device
  that no longer exists in Home Assistant.

## 0.7.2

- The home list now comes from the credentials typed in the form ("Carregar
  casas" button; `POST /api/tuya_wizard/homes` with `credentials`), so the home
  is chosen after the key and secret. Changing the credentials asks to reload
  the list; discovery uses the same credentials and requires a loaded home.
- The last home used is remembered per account (Client ID) on the server and
  comes pre-selected ("última usada"); no more global default.
- Wrong credentials show "Nenhuma casa encontrada" instead of an empty list.

## 0.7.1

- Default Tuya home saved on the server (`GET/POST /api/tuya_wizard/homes`,
  private store `tuya_wizard.prefs`): every browser starts on the same home, the
  default comes first and is marked "padrão". Choosing another home asks whether
  it becomes the default; "Todas as casas" warns that installer accounts also see
  their clients' homes.

## 0.7.0

- Motion binary sensor per camera registered in the Tuya Proxy Companion
  (`binary_sensor.movimento_da_camera_<slug>`, device class motion): polls
  Frigate's event API every 10 s for the Companion's manual "motion" events and
  stays on for 30 s after each one. Attributes: last event, events today, source.
- Unit tests (`tests/test_units.py`, stdlib unittest; pytest-compatible) for DP
  mapping, discovery filters, home listing, Companion sync comparison and the
  cloud leak check; see `TESTING.md`.

## 0.6.0

- New "Nuvem" (cloud) tab: per-device cloud block through the Tuya Proxy
  Companion (`GET/POST /api/tuya_wizard/cloud`), available only when the
  Companion is running. Devices added only for the block leave the Companion
  again when unblocked (each entry keeps a local connection open).
- The tab explains the limits learned in the field: the ARP-based block can
  leak; Tuya cameras validate the cloud TLS certificate and many stop detecting
  motion without the cloud; the block stops if Home Assistant/the Companion is
  down and is re-applied when they return.
- Router help: reserve a fixed IP and block it to the internet (generic steps
  plus Vivo/MitraStar, TP-Link, Intelbras and OpenWrt), or use an IoT Wi-Fi
  without internet.
- Leak check: reads the device's cloud log and reports anything the cloud
  received after the block started.

## 0.5.0

- Home selector: the page lists the Tuya homes (spaces) the cloud project sees
  (`GET /api/tuya_wizard/homes`) and discovery shows only the chosen home's
  devices. Installer accounts also see client homes; homes without read
  permission are labelled as such. The last choice is remembered per browser.
- Discovery uses the credentials saved in the config entry; the page no longer
  sends the secret on every scan.
- Device names in Portuguese for common DP codes (cameras, switches, lights,
  breakers), with the code in Title Case as fallback.
- Raw / long-payload DPs (event pictures and messages, e.g. `movement_detect_pic`,
  `alarm_message`, `initiative_message`) are left out of LocalTuya: they exceed
  Home Assistant's 255-char state limit. Camera events come via the Companion.
- Re-provisioning updates the existing LocalTuya device (new IP, local key or
  DP list after re-pairing) instead of skipping it.

## 0.4.0

- Tuya Proxy Companion: the wizard now stores the camera list in Home Assistant
  (`.storage/tuya_wizard.companion`) and re-sends it whenever the add-on loses it
  (checked every 5 minutes and 30 s after startup). Cameras provisioned by the
  wizard are registered automatically (local motion events -> Frigate). The list
  already present in the add-on is adopted on first run.
- Cloud block stays off by default: Tuya cameras validate the cloud TLS
  certificate, so the Companion cannot impersonate the cloud, and many cameras
  stop detecting motion without it.
- Frigate step: RTSP probe retries (4 x 5 s) because cameras restart their RTSP
  server right after the ONVIF password is written.

