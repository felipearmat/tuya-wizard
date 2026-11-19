# Testing

## Unit tests

Pure logic (DP mapping, discovery filters, Companion sync comparison, cloud
leak check) is covered by `tests/test_units.py`, using only the standard
library's `unittest` (pytest also runs it). It needs `homeassistant`,
`pydantic` and `tinytuya` importable, and uses no network.

```sh
python -m unittest discover -s tests -v
```

Inside a Home Assistant OS install, the Core container already has the
dependencies:

```sh
tar --exclude=.git -c . | docker exec -i homeassistant sh -c \
  'rm -rf /tmp/tw && mkdir -p /tmp/tw && tar -x -C /tmp/tw && cd /tmp/tw && python3 -m unittest discover -s tests -v; rm -rf /tmp/tw'
```

(`docker cp` into the Core container does not work on HA OS; use the tar pipe.)

## Manual checks done on a real install

- Companion persistence: restart the Tuya Proxy Companion add-on; the camera
  list is re-sent by the wizard within ~2.5 minutes.
- Home selector: `GET /api/tuya_wizard/homes` lists homes; discovery with
  `home_id` returns only that home's devices.
- Cloud tab: block a light, run the leak check, unblock; the light leaves the
  Companion after unblocking.
- Motion sensor: walk in front of a camera registered in the Companion; the
  `binary_sensor` turns on for 30 s.
