# Changelog

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

