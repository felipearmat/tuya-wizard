# Changelog

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

