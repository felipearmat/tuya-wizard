# Tuya Wizard

[![hassfest](https://github.com/felipearmat/tuya-wizard/actions/workflows/validate.yml/badge.svg)](https://github.com/felipearmat/tuya-wizard/actions/workflows/validate.yml)

A Home Assistant custom integration that discovers your Tuya devices (cloud + local network) and provisions them into [LocalTuya](https://github.com/rospogrigio/localtuya), so they're controlled entirely over your LAN instead of through Tuya's cloud.

## What it does

- **Discovery**: queries your Tuya IoT Platform project for devices, scans the local network for their IPs, and pulls each device's full DP (data point) schema from the cloud so every controllable feature — not just whatever happens to be reporting live — gets exposed.
- **Provisioning**: writes the discovered devices straight into your existing LocalTuya config entry. No manual LocalTuya YAML/UI editing.
- **Local capability enablement**: some devices (e.g. cameras) ship with local-control features such as ONVIF disabled by default; the wizard enables them via a local DP write during provisioning, using a password you provide.
- **Card generation**: automatically generates and deploys a generic Lovelace card per provisioned device, rendering the right control (toggle/dropdown/slider) for each DP type.
- **Removal**: cleanly removes a device from LocalTuya and its orphaned entity/device registry entries.

## Requirements

- [LocalTuya](https://github.com/rospogrigio/localtuya) already installed and configured with at least one config entry (Tuya Wizard adds devices into it, it doesn't replace it).
- A Tuya IoT Platform project (Cloud > Development in [iot.tuya.com](https://iot.tuya.com)) with your devices linked to it, for its `Client ID` / `Client Secret` — needed to discover devices and pull their DP schema.

## Installation

Via [HACS](https://hacs.xyz/): add this repository as a custom repository, install "Tuya Wizard", restart Home Assistant, then add the integration from **Settings > Devices & Services**.

## Configuration

Set your Tuya IoT Platform credentials during setup, or later via **Settings > Devices & Services > Tuya Wizard > Configure**.

## Status

The backend (discovery, provisioning, local-enable, card generation) is functional and REST/SSE-driven. A dedicated in-panel UI is still in progress — see the `static/` panel for current status.

## License

MIT — see [LICENSE](LICENSE).
