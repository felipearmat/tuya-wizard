"""Map Tuya DP definitions to LocalTuya entity platforms."""

from __future__ import annotations

from .models import DpInfo

# Human-readable names for the most common cross-category DPs
_COMMON_DP_NAMES: dict[str, str] = {
    "1": "Switch",
    "2": "Switch 2",
    "3": "Switch 3",
    "7": "Countdown Timer",
    "9": "Countdown",
    "14": "Power-On Behavior",
    "38": "Scene Mode",
    "47": "Relay Mode",
}

# Portuguese names by Tuya DP code (cameras, switches, lights, breakers).
# Falls back to the code in Title Case when the code is not listed.
CODE_NAMES_PT: dict[str, str] = {
    "switch": "Liga/desliga", "switch_1": "Liga/desliga", "switch_2": "Liga/desliga 2",
    "switch_3": "Liga/desliga 3", "switch_led": "Luz", "bright_value": "Brilho",
    "bright_value_v2": "Brilho", "temp_value": "Temperatura de cor", "temp_value_v2": "Temperatura de cor",
    "work_mode": "Modo", "colour_data": "Cor", "colour_data_v2": "Cor", "scene_data": "Cena",
    "countdown": "Temporizador", "countdown_1": "Temporizador", "relay_status": "Comportamento ao ligar",
    "light_mode": "Modo do LED", "child_lock": "Trava infantil",
    "cur_power": "Potência", "cur_current": "Corrente", "cur_voltage": "Tensão", "add_ele": "Energia",
    "temp_current": "Temperatura", "fault": "Falha",
    "basic_indicator": "LED indicador", "basic_flip": "Imagem espelhada", "basic_osd": "Data e hora na imagem",
    "basic_private": "Modo privacidade", "motion_sensitivity": "Sensibilidade de movimento",
    "basic_wdr": "WDR (contraste)", "sd_storge": "Cartão SD (espaço)", "sd_status": "Cartão SD (estado)",
    "sd_format": "Formatar cartão SD", "sd_format_state": "Formatação do cartão SD",
    "ptz_stop": "PTZ parar", "ptz_control": "Controle PTZ", "ptz_calibration": "Calibrar PTZ",
    "ipc_auto_siren": "Sirene automática", "nightvision_mode": "Visão noturna",
    "event_linkage": "Último evento", "motion_switch": "Detecção de movimento",
    "doorbell_active": "Campainha", "floodlight_switch": "Luz de iluminação",
    "decibel_switch": "Detecção de som", "decibel_sensitivity": "Sensibilidade de som",
    "record_switch": "Gravação no cartão SD", "record_mode": "Modo de gravação",
    "basic_device_volume": "Volume", "motion_tracking": "Rastreamento automático",
    "device_restart": "Reiniciar câmera", "zoom_control": "Zoom", "zoom_stop": "Zoom parar",
    "motion_area_switch": "Zona de movimento", "motion_area": "Área de movimento",
    "humanoid_filter": "Só pessoas", "ipc_preset_action": "Ir para preset", "ipc_preset_set": "Salvar preset",
    "ipc_object_outline": "Contorno de objetos", "ipc_audible_alarm": "Alarme sonoro",
    "basic_anti_flicker": "Anti-oscilação", "onvif_switch": "ONVIF",
}

_TUYA_TYPE_TO_PLATFORM: dict[str, str] = {
    "bool": "switch",
    "enum": "select",
    "int": "number",
    "float": "number",
    "string": "sensor",
    "raw": "sensor",
}

# Tuya's cloud schema reports types in PascalCase (e.g. "Boolean", "Integer");
# normalize to the short lowercase tokens used above and by value-inference.
_CLOUD_TYPE_ALIASES: dict[str, str] = {
    "boolean": "bool",
    "integer": "int",
    "json": "raw",
    "bitmap": "int",
}


def _infer_type_from_value(value: object) -> str:
    """Infer Tuya type from a live DP value when cloud schema is unavailable."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    return "raw"


def map_dp(
    dp_id: str,
    *,
    tuya_type: str | None = None,
    value: object = None,
    schema: dict | None = None,
) -> DpInfo:
    """Build a DpInfo from a cloud schema entry and/or a live DP value.

    Priority: explicit tuya_type > schema type > value inference.
    """
    resolved_type = tuya_type
    if not resolved_type and schema:
        resolved_type = schema.get("type") or schema.get("data_type") or ""
    if not resolved_type:
        resolved_type = _infer_type_from_value(value) if value is not None else "string"

    resolved_type = resolved_type.lower()
    resolved_type = _CLOUD_TYPE_ALIASES.get(resolved_type, resolved_type)

    platform = _TUYA_TYPE_TO_PLATFORM.get(resolved_type, "sensor")

    # Name: cloud schema code beats common name table
    name = _COMMON_DP_NAMES.get(dp_id, f"DP {dp_id}")
    if schema and schema.get("code"):
        code = schema["code"]
        name = CODE_NAMES_PT.get(code) or code.replace("_", " ").title()

    dp = DpInfo(id=dp_id, name=name, tuya_type=resolved_type, platform=platform)

    if resolved_type == "enum" and schema:
        raw_values = schema.get("values", {})
        # Tuya API returns range as list or as a dict with a "range" key
        if isinstance(raw_values, dict):
            enum_range = raw_values.get("range", [])
        elif isinstance(raw_values, list):
            enum_range = raw_values
        else:
            enum_range = []
        dp.select_options = {v: str(v) for v in enum_range}

    if resolved_type in ("int", "float") and schema:
        raw_values = schema.get("values", {})
        if isinstance(raw_values, dict):
            dp.min_value = float(raw_values.get("min", 0))
            dp.max_value = float(raw_values.get("max", 100))
            dp.step_size = float(raw_values.get("step", 1))

    return dp


def from_status(status: dict[str, object]) -> list[DpInfo]:
    """Build a DpInfo list from a device status() dict without cloud schema."""
    return [map_dp(str(dp_id), value=val) for dp_id, val in status.items()]
