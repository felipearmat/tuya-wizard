"""Model catalog: one JSON file per Tuya product (product_id) with the FULL data model.

Why (2026-11-27): the short cloud specification used by discovery hides vendor DPs — a smart breaker
(K013_KB_B1M) came in with 12 nameless "DP 1xx" and without DP 102 (phase_a: voltage/current/power in raw).
The full thing model (/v2.0/cloud/thing/<device_id>/model) describes every DP (code, type, unit, scale, range).

Flow: a known product uses its file (no cloud call); an unknown one is fetched ONCE from the cloud, probed on the
LAN (which DPs arrive, and which only arrive after a "sync" DP is set) and saved with "revisado": false so the
user can review names. Files live in <config>/tuya_wizard/modelos/<product_id>.json.

Pure Python (no Home Assistant imports) so it can be tested on its own.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Raw DPs with a known binary layout -> decoder name (see decodificar()).
DECODERS: dict[str, str] = {
    "phase_a": "fase_v2a3w3", "phase_b": "fase_v2a3w3", "phase_c": "fase_v2a3w3",
}

# DPs that make the device push other (raw) DPs over the LAN when set to True.
SYNC_CODES = ("sync_dp_info",)


def decodificar(decoder: str, value: str) -> dict[str, float] | None:
    """Decode a base64 raw DP. fase_v2a3w3: voltage 2 B (0.1 V), current 3 B (mA), power 3 B (W)."""
    try:
        b = base64.b64decode(value)
    except (ValueError, TypeError):
        return None
    if decoder == "fase_v2a3w3" and len(b) >= 8:
        return {"tensao_v": int.from_bytes(b[0:2], "big") / 10,
                "corrente_a": int.from_bytes(b[2:5], "big") / 1000,
                "potencia_w": int.from_bytes(b[5:8], "big")}
    return None


def _path(folder: str, product_id: str) -> str:
    safe = "".join(ch for ch in product_id if ch.isalnum() or ch in "-_") or "sem_id"
    return os.path.join(folder, f"{safe}.json")


# Reviewed entries shipped WITH the integration (travel with every install/update).
# The learned folder in <config> is checked first, so a local review overrides a shipped entry.
BUILTIN_DIR = os.path.join(os.path.dirname(__file__), "modelos")


def carregar(folder: str, product_id: str | None) -> dict | None:
    if not product_id:
        return None
    for base in (folder, BUILTIN_DIR):
        try:
            with open(_path(base, product_id), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return None


def salvar(folder: str, ficha: dict) -> None:
    os.makedirs(folder, exist_ok=True)
    p = _path(folder, ficha["product_id"])
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ficha, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def listar(folder: str) -> list[dict]:
    out = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for n in names:
        if n.endswith(".json"):
            try:
                with open(os.path.join(folder, n), encoding="utf-8") as f:
                    d = json.load(f)
                out.append({k: d.get(k) for k in ("product_id", "categoria", "modelo", "criado", "revisado")}
                           | {"dps": len(d.get("dps", {}))})
            except (OSError, ValueError):
                continue
    return out


def modelo_da_nuvem(cloud, device_id: str) -> dict[str, dict]:
    """Full thing model -> {dp_id: {code, nome_nuvem, tipo, unidade, escala, min, max, passo, valores, acesso}}."""
    res = cloud.cloudrequest(f"/v2.0/cloud/thing/{device_id}/model") or {}
    model = (res.get("result") or {}).get("model")
    if isinstance(model, str):
        try:
            model = json.loads(model)
        except ValueError:
            model = None
    dps: dict[str, dict] = {}
    for service in (model or {}).get("services", []):
        for p in service.get("properties", []):
            spec = p.get("typeSpec") or {}
            dp_id = str(p.get("abilityId", ""))
            if not dp_id.isdigit():
                continue
            dps[dp_id] = {
                "code": p.get("code", ""), "nome_nuvem": p.get("name", ""), "tipo": spec.get("type", ""),
                "unidade": spec.get("unit", ""), "escala": spec.get("scale", 0),
                "min": spec.get("min"), "max": spec.get("max"), "passo": spec.get("step"),
                "valores": spec.get("range") or [], "acesso": p.get("accessMode", ""),
            }
    return dps


def sondar_local(device, dps_model: dict[str, dict], status: dict) -> dict[str, Any]:
    """Which DPs arrive locally, and which only arrive after setting a sync DP (if the model has one)."""
    locais = {str(k) for k in status}
    sync_id = next((i for i, d in dps_model.items() if d["code"] in SYNC_CODES and d["acesso"] == "rw"), None)
    apos_sync: set[str] = set()
    if sync_id and device is not None:
        try:
            device.set_socketPersistent(True)
            device.set_value(int(sync_id), True)
            t = time.time()
            while time.time() - t < 6:
                m = device.receive()
                if m and m.get("dps"):
                    apos_sync |= {str(k) for k in m["dps"]}
        except Exception as exc:  # the probe is best effort
            _LOGGER.debug("sync probe failed: %s", exc)
    return {"locais": sorted(locais, key=int), "sync_dp": sync_id,
            "so_apos_sync": sorted(apos_sync - locais, key=int)}


def montar_ficha(raw: dict, dps_model: dict[str, dict], sonda: dict[str, Any], nomes_pt: dict[str, str]) -> dict:
    so_sync = set(sonda.get("so_apos_sync", []))
    locais = set(sonda.get("locais", []))
    dps = {}
    for dp_id, d in sorted(dps_model.items(), key=lambda x: int(x[0])):
        nome = nomes_pt.get(d["code"]) or d["code"].replace("_", " ").capitalize()
        if d["code"] == "temp_value" and d["unidade"] in ("℃", "°C"):
            nome = "Temperatura"
        item = dict(d, nome=nome, local=dp_id in locais or dp_id in so_sync, precisa_sync=dp_id in so_sync)
        if d["code"] in DECODERS:
            item["decodificador"] = DECODERS[d["code"]]
        dps[dp_id] = item
    return {
        "product_id": raw.get("product_id") or raw.get("productId") or "",
        "categoria": raw.get("category", ""),
        "modelo": raw.get("product_name") or raw.get("model") or "",
        "criado": time.strftime("%Y-%m-%d"), "fonte": "nuvem: /v2.0/cloud/thing/<id>/model + sonda local",
        "revisado": False, "sync_dp": sonda.get("sync_dp"), "dps": dps,
    }


def schema_da_ficha(ficha: dict) -> dict[str, dict]:
    """Convert a catalog entry to the schema shape dp_mapper.map_dp() understands."""
    out: dict[str, dict] = {}
    for dp_id, d in ficha.get("dps", {}).items():
        tipo = {"value": "int", "bitmap": "int", "bool": "bool", "enum": "enum",
                "string": "string", "raw": "raw"}.get(d.get("tipo", ""), d.get("tipo", ""))
        values: dict[str, Any] = {}
        if tipo == "int":
            values = {"min": d.get("min") if d.get("min") is not None else 0,
                      "max": d.get("max") if d.get("max") is not None else 100,
                      "step": d.get("passo") or 1, "scale": d.get("escala") or 0, "unit": d.get("unidade", "")}
        elif tipo == "enum":
            values = {"range": d.get("valores") or []}
        out[dp_id] = {"code": d.get("code", ""), "type": tipo, "values": values, "nome": d.get("nome"),
                     "acesso": d.get("acesso", "")}
    return out
