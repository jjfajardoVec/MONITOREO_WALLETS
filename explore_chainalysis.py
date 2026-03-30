#!/usr/bin/env python3
"""
VECTORA AML — Explorador Completo de Chainalysis KYT API v2
Ejecutar en EC2: python3 explore_chainalysis.py
Guardar output:  python3 explore_chainalysis.py > chainalysis_exploration.txt 2>&1

Explora TODOS los endpoints disponibles y muestra la data real.
"""

import requests
import json
import sys
import time
from datetime import datetime

API_KEY = "20015585b408146ea6634bdae442f443784439d36a0009f301f05cb56b0ae64d"
BASE_URL = "https://api.chainalysis.com"
HEADERS = {"Token": API_KEY, "Content-Type": "application/json"}

# Chainalysis Sanctions (API separada)
SANCTIONS_KEY = "cb4dd1ffdc458b86ef8e49a486412e830ad7258012170221291fc7506582b35a"
SANCTIONS_URL = "https://public.chainalysis.com/api/v1"
SANCTIONS_HEADERS = {"X-API-Key": SANCTIONS_KEY}

# ── TXs de referencia para testing ──────────────────────────
# Ecoexports (risk bajo, esperamos APROBAR)
ECO_TX = "ff78d86428e8836220fffb0514ba7a719e852f260c373c31b842b6c45d71bd15"
ECO_ADDR = "TJLQc8drrf9ZWQcwWjkuA96eCkmPSye8pT"
ECO_CLIENT = "Ecoexports"
ECO_AMOUNT = 40014.47

# DELTEX (risk bajo con observación OFAC indirecto)
DEL_TX = "0791cc5c043d8e9bb74f875eaa635e2e57c96adcbe440f59c7a722bfb8b420b1"
DEL_ADDR = "THaEwWojvq5PatoRCa4pYThZm8zW88pj2p"
DEL_CLIENT = "DELTEX"
DEL_AMOUNT = 100002.41

# Wallets del backward trace de Ecoexports (hop 1) para probar screening
TRACE_WALLETS = [
    "TJLQc8drrf9ZWQcwWjkuA96eCkmPSye8pT",  # dest Ecoexports
    "THaEwWojvq5PatoRCa4pYThZm8zW88pj2p",  # dest DELTEX
]

SEP = "=" * 80
SUBSEP = "─" * 50


def api(method, path, json_data=None, params=None, quiet=False):
    """Llamada al API. Retorna (status_code, data_or_error)."""
    url = f"{BASE_URL}{path}"
    try:
        if method == "GET":
            r = requests.get(url, headers=HEADERS, params=params, timeout=30)
        elif method == "POST":
            r = requests.post(url, headers=HEADERS, json=json_data, timeout=30)
        else:
            return 0, {"_error": f"Unsupported method: {method}"}

        if not quiet:
            print(f"  [{r.status_code}] {method} {path}")

        if r.status_code in (200, 201, 202):
            try:
                return r.status_code, r.json()
            except:
                return r.status_code, r.text
        elif r.status_code == 204:
            return 204, None
        else:
            try:
                return r.status_code, r.json()
            except:
                return r.status_code, r.text
    except Exception as e:
        if not quiet:
            print(f"  [ERR] {method} {path} → {e}")
        return 0, {"_exception": str(e)}


def pj(data, max_lines=60):
    """Pretty print JSON."""
    if data is None:
        print("  (empty/204)")
        return
    text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    lines = text.split("\n")
    for line in lines[:max_lines]:
        print(f"  {line}")
    if len(lines) > max_lines:
        print(f"  ... ({len(lines) - max_lines} líneas más)")


def section(num, title, desc):
    print(f"\n{SEP}")
    print(f"  {num}. {title}")
    print(f"  {desc}")
    print(SEP)


# ════════════════════════════════════════════════════════════
# 1. CATEGORIES — Categorías de clasificación de KYT
# ════════════════════════════════════════════════════════════
def s1_categories():
    section(1, "CATEGORIES", "Categorías que KYT usa para clasificar entidades y exposures")
    print("\n▸ GET /v2/categories")
    code, data = api("GET", "/v2/categories")
    pj(data, max_lines=120)


# ════════════════════════════════════════════════════════════
# 2. USERS — Clientes registrados en KYT
# ════════════════════════════════════════════════════════════
def s2_users():
    section(2, "USERS", "Cada user en KYT = un cliente de Vectora. Incluye risk score del user.")

    print("\n▸ GET /v2/users — Listar todos")
    code, data = api("GET", "/v2/users")
    pj(data)

    for name in [ECO_CLIENT, DEL_CLIENT]:
        print(f"\n▸ GET /v2/users/{name} — Risk score")
        code, data = api("GET", f"/v2/users/{name}")
        pj(data)


# ════════════════════════════════════════════════════════════
# 3. TRANSFERS — Registrar y consultar TXs
# ════════════════════════════════════════════════════════════
def s3_transfers():
    section(3, "TRANSFERS", "Registrar TXs received → obtener externalId para consultar exposures")

    results = {}
    for label, client, tx, addr, amount in [
        ("Ecoexports", ECO_CLIENT, ECO_TX, ECO_ADDR, ECO_AMOUNT),
        ("DELTEX", DEL_CLIENT, DEL_TX, DEL_ADDR, DEL_AMOUNT),
    ]:
        print(f"\n{SUBSEP}")
        print(f"  {label}")
        print(SUBSEP)

        payload = {
            "asset": "USDT_TRX",
            "transferReference": f"{tx}:{addr}",
            "direction": "received",
            "assetAmount": amount
        }
        print(f"\n▸ POST /v2/users/{client}/transfers")
        print(f"  Payload: {json.dumps(payload)}")
        code, data = api("POST", f"/v2/users/{client}/transfers", json_data=payload)
        pj(data)

        eid = None
        if isinstance(data, dict):
            eid = data.get("externalId")
        if eid:
            print(f"\n  ✅ externalId = {eid}")
        results[label] = eid

    return results


# ════════════════════════════════════════════════════════════
# 4. TRANSFER DETAILS — Summary, Exposures, Alerts, Network
# ════════════════════════════════════════════════════════════
def s4_transfer_details(eids):
    section(4, "TRANSFER DETAILS", "Summary + Exposures + Alerts + Network IDs para cada TX")

    for label, eid in eids.items():
        if not eid:
            print(f"\n  ⚠ Sin externalId para {label}")
            continue

        print(f"\n{SUBSEP}")
        print(f"  {label} — externalId: {eid}")
        print(SUBSEP)

        # 4a. Summary (poll hasta updatedAt != null)
        print(f"\n▸ GET /v2/transfers/{eid} — Summary")
        for attempt in range(5):
            code, data = api("GET", f"/v2/transfers/{eid}")
            if isinstance(data, dict) and data.get("updatedAt"):
                print(f"  ✅ updatedAt populated: {data['updatedAt']}")
                pj(data)
                break
            elif attempt < 4:
                print(f"  ⏳ updatedAt is null, waiting 5s... (attempt {attempt+1}/5)")
                time.sleep(5)
            else:
                print(f"  ⚠ updatedAt still null after 5 attempts")
                pj(data)

        # 4b. Direct Exposure (CRUCIAL — nombres de servicios/clusters)
        print(f"\n▸ GET /v2/transfers/{eid}/exposures — Direct Exposure")
        code, data = api("GET", f"/v2/transfers/{eid}/exposures")
        pj(data, max_lines=80)

        # 4c. Network Identifications (counterparty cluster names)
        print(f"\n▸ GET /v2/transfers/{eid}/network-identifications — Cluster/Network IDs")
        code, data = api("GET", f"/v2/transfers/{eid}/network-identifications")
        pj(data, max_lines=40)

        # 4d. Alerts per transfer
        print(f"\n▸ GET /v2/transfers/{eid}/alerts — Alerts de esta TX")
        code, data = api("GET", f"/v2/transfers/{eid}/alerts")
        pj(data, max_lines=40)

        # 4e. High-risk addresses (Chainalysis Identifications)
        print(f"\n▸ GET /v2/transfers/{eid}/high-risk-addresses — High Risk")
        code, data = api("GET", f"/v2/transfers/{eid}/high-risk-addresses")
        pj(data, max_lines=40)

        # 4f. Counterparties (posible endpoint)
        print(f"\n▸ GET /v2/transfers/{eid}/counterparties — Counterparties")
        code, data = api("GET", f"/v2/transfers/{eid}/counterparties")
        pj(data, max_lines=40)


# ════════════════════════════════════════════════════════════
# 5. WITHDRAWAL ATTEMPTS — Pre-screening de wallets
#    CLAVE: Esto es lo que usamos para screening de wallets
#    del backward trace (cada wallet se registra como
#    withdrawal attempt para obtener su risk/exposure)
# ════════════════════════════════════════════════════════════
def s5_withdrawal_attempts():
    section(5, "WITHDRAWAL ATTEMPTS",
            "Pre-screening de wallets individuales. CLAVE para screening del backward trace.")

    results = {}
    for i, addr in enumerate(TRACE_WALLETS):
        print(f"\n{SUBSEP}")
        print(f"  Wallet: {addr}")
        print(SUBSEP)

        # Registrar withdrawal attempt
        payload = {
            "asset": "USDT_TRX",
            "address": addr,
            "attemptIdentifier": f"trace_test_{i}_{int(time.time())}",
            "assetAmount": 1000
        }
        print(f"\n▸ POST /v2/users/{ECO_CLIENT}/withdrawal-attempts")
        print(f"  Payload: {json.dumps(payload)}")
        code, data = api("POST", f"/v2/users/{ECO_CLIENT}/withdrawal-attempts", json_data=payload)
        pj(data)

        eid = None
        if isinstance(data, dict):
            eid = data.get("externalId")
        if not eid:
            print(f"  ⚠ No externalId, probando formato alternativo...")
            # Intentar otro formato de payload
            payload2 = {
                "asset": "USDT_TRX",
                "address": addr,
            }
            code, data = api("POST", f"/v2/users/{ECO_CLIENT}/withdrawal-attempts", json_data=payload2)
            pj(data)
            if isinstance(data, dict):
                eid = data.get("externalId")

        if eid:
            print(f"\n  ✅ externalId = {eid}")
            results[addr] = eid

            # Summary
            print(f"\n▸ GET /v2/withdrawal-attempts/{eid} — Summary")
            for attempt in range(3):
                code, data = api("GET", f"/v2/withdrawal-attempts/{eid}")
                if isinstance(data, dict) and data.get("updatedAt"):
                    pj(data)
                    break
                elif attempt < 2:
                    time.sleep(3)
            else:
                pj(data)

            # Exposures (counterparty exposure!)
            print(f"\n▸ GET /v2/withdrawal-attempts/{eid}/exposures — Counterparty Exposure")
            code, data = api("GET", f"/v2/withdrawal-attempts/{eid}/exposures")
            pj(data, max_lines=60)

            # High-risk addresses (Chainalysis Identifications = CLUSTERS!)
            print(f"\n▸ GET /v2/withdrawal-attempts/{eid}/high-risk-addresses — Cluster Identifications")
            code, data = api("GET", f"/v2/withdrawal-attempts/{eid}/high-risk-addresses")
            pj(data, max_lines=40)

            # Network identifications
            print(f"\n▸ GET /v2/withdrawal-attempts/{eid}/network-identifications — Network IDs")
            code, data = api("GET", f"/v2/withdrawal-attempts/{eid}/network-identifications")
            pj(data, max_lines=40)

            # Alerts
            print(f"\n▸ GET /v2/withdrawal-attempts/{eid}/alerts — Alerts")
            code, data = api("GET", f"/v2/withdrawal-attempts/{eid}/alerts")
            pj(data, max_lines=40)

    return results


# ════════════════════════════════════════════════════════════
# 6. ALERTS — Todas las alertas globales del sistema
# ════════════════════════════════════════════════════════════
def s6_alerts():
    section(6, "ALERTS", "Alertas globales generadas por KYT")

    print("\n▸ GET /v2/alerts — Todas")
    code, data = api("GET", "/v2/alerts")
    pj(data, max_lines=80)

    for severity in ["LOW", "MEDIUM", "HIGH", "SEVERE"]:
        print(f"\n▸ GET /v2/alerts?severity={severity}")
        code, data = api("GET", "/v2/alerts", params={"severity": severity})
        if isinstance(data, list) and len(data) > 0:
            print(f"  ✅ {len(data)} alertas {severity}")
            pj(data, max_lines=30)
        elif isinstance(data, dict) and "data" in data:
            items = data["data"]
            print(f"  ✅ {len(items)} alertas {severity}")
            pj(data, max_lines=30)
        else:
            print(f"  (vacío o sin alertas {severity})")


# ════════════════════════════════════════════════════════════
# 7. ENDPOINT DISCOVERY — Buscar endpoints ocultos
# ════════════════════════════════════════════════════════════
def s7_discovery():
    section(7, "ENDPOINT DISCOVERY", "Probando todos los endpoints posibles de KYT v2")

    endpoints = [
        # Core
        ("GET", "/v2/categories", "Categories"),
        ("GET", "/v2/users", "Users"),
        ("GET", "/v2/alerts", "Alerts"),
        ("GET", "/v2/transfers", "Transfers list"),

        # Possible extras
        ("GET", "/v2/assets", "Assets"),
        ("GET", "/v2/clusters", "Clusters"),
        ("GET", "/v2/counterparties", "Counterparties"),
        ("GET", "/v2/entities", "Entities"),
        ("GET", "/v2/services", "Services"),
        ("GET", "/v2/risk-scores", "Risk Scores"),
        ("GET", "/v2/risk-assessment", "Risk Assessment"),
        ("GET", "/v2/summary", "Summary"),
        ("GET", "/v2/analytics", "Analytics"),
        ("GET", "/v2/dashboard", "Dashboard"),
        ("GET", "/v2/webhooks", "Webhooks"),
        ("GET", "/v2/webhook-subscriptions", "Webhook Subs"),
        ("GET", "/v2/config", "Config"),
        ("GET", "/v2/settings", "Settings"),
        ("GET", "/v2/organization", "Organization"),
        ("GET", "/v2/network", "Network"),
        ("GET", "/v2/graph", "Graph"),
        ("GET", "/v2/connections", "Connections"),
        ("GET", "/v2/sanctions", "Sanctions"),
        ("GET", "/v2/screening", "Screening"),
        ("GET", "/v2/depositaddresses", "Deposit Addresses"),

        # Address-level
        ("GET", f"/v2/addresses/{ECO_ADDR}", "Address info"),
        ("GET", f"/v2/addresses/{ECO_ADDR}/exposures", "Address exposures"),
        ("GET", f"/v2/addresses/{ECO_ADDR}/network-identifications", "Address net IDs"),

        # V1 fallbacks
        ("GET", "/v1/users", "V1 Users"),
        ("GET", "/v1/transfers", "V1 Transfers"),
        ("GET", "/v1/alerts", "V1 Alerts"),
    ]

    found = []
    forbidden = []
    not_found = []

    for method, path, desc in endpoints:
        code, data = api(method, path, quiet=True)
        if code in (200, 201, 202):
            found.append((method, path, desc, data))
            print(f"  ✅ [{code}] {desc}: {method} {path}")
        elif code == 403:
            forbidden.append((method, path, desc))
            print(f"  🔒 [{code}] {desc}: {method} {path} (existe, sin acceso)")
        elif code == 405:
            forbidden.append((method, path, desc))
            print(f"  ⚠️  [{code}] {desc}: {method} {path} (existe, método incorrecto)")
        elif code == 404:
            not_found.append(desc)
        else:
            print(f"  ❓ [{code}] {desc}: {method} {path}")

    print(f"\n{'─' * 40}")
    print(f"  RESUMEN:")
    print(f"    ✅ Funcionan: {len(found)}")
    print(f"    🔒 Existen pero restringidos: {len(forbidden)}")
    print(f"    ❌ No existen: {len(not_found)}")

    if found:
        print(f"\n  ENDPOINTS CONFIRMADOS:")
        for method, path, desc, data in found:
            print(f"    {method} {path}")
            # Mostrar preview de la respuesta
            if isinstance(data, (dict, list)):
                preview = json.dumps(data, default=str)[:200]
                print(f"      → {preview}...")


# ════════════════════════════════════════════════════════════
# 8. SANCTIONS API — API separada para OFAC
# ════════════════════════════════════════════════════════════
def s8_sanctions():
    section(8, "CHAINALYSIS SANCTIONS API",
            "API separada (gratuita). Verificación OFAC de direcciones.")

    addrs = [
        (ECO_ADDR, "Ecoexports dest"),
        (DEL_ADDR, "DELTEX dest"),
    ]

    for addr, desc in addrs:
        print(f"\n▸ GET /address/{addr} — {desc}")
        try:
            r = requests.get(
                f"{SANCTIONS_URL}/address/{addr}",
                headers=SANCTIONS_HEADERS, timeout=15
            )
            print(f"  [{r.status_code}]")
            if r.status_code == 200:
                pj(r.json())
        except Exception as e:
            print(f"  Error: {e}")


# ════════════════════════════════════════════════════════════
# 9. ADDRESS SCREENING API — Cluster directo por wallet
#    API SEPARADA de KYT. Retorna cluster.name, cluster.category,
#    risk level, y exposures directas/indirectas de cualquier address.
#    ESTO ES LO MÁS IMPORTANTE PARA EL BACKWARD TRACE.
# ════════════════════════════════════════════════════════════
def s9_address_screening():
    section(9, "ADDRESS SCREENING API (api/risk/v2)",
            "API separada de KYT. Retorna cluster + risk + exposures de cualquier wallet.")

    # Probar primero registrar y luego consultar
    addrs = [
        (ECO_ADDR, "Ecoexports dest wallet"),
        (DEL_ADDR, "DELTEX dest wallet"),
    ]

    for addr, desc in addrs:
        print(f"\n{SUBSEP}")
        print(f"  {desc}: {addr}")
        print(SUBSEP)

        # Método 1: POST para registrar + GET para consultar
        print(f"\n▸ POST /api/risk/v2/entities — Registrar address")
        code, data = api("POST", "/api/risk/v2/entities", json_data={"address": addr})
        print(f"  Status: {code}")
        pj(data)

        print(f"\n▸ GET /api/risk/v2/entities/{addr} — Cluster + Risk + Exposures")
        code, data = api("GET", f"/api/risk/v2/entities/{addr}")
        print(f"  Status: {code}")
        pj(data, max_lines=80)

        # Método 2: probar path alternativo
        print(f"\n▸ GET /api/risk/v2/entities/{addr}?network=TRON — Con network param")
        code, data = api("GET", f"/api/risk/v2/entities/{addr}", params={"network": "TRON"})
        print(f"  Status: {code}")
        pj(data, max_lines=40)

    # Probar endpoint discovery dentro de risk API
    print(f"\n{SUBSEP}")
    print(f"  Discovery: endpoints de /api/risk/")
    print(SUBSEP)

    risk_endpoints = [
        ("GET", "/api/risk/v2/entities", "List entities"),
        ("GET", "/api/risk/v2/categories", "Risk categories"),
        ("GET", "/api/risk/v2/clusters", "Clusters"),
        ("GET", "/api/risk/v1/entities", "V1 entities"),
    ]
    for method, path, desc in risk_endpoints:
        code, data = api(method, path, quiet=True)
        status = "✅" if code in (200, 201, 202) else f"[{code}]"
        print(f"  {status} {desc}: {method} {path}")
        if code in (200, 201, 202):
            pj(data, max_lines=15)


# ════════════════════════════════════════════════════════════
# 10. DEEP DIVE — Exposures detallado de transfers
# ════════════════════════════════════════════════════════════
def s10_deep_dive(eids):
    section(10, "DEEP DIVE — EXPOSURES COMPLETO",
            "Todas las variaciones de consulta de exposures por transfer")

    for label, eid in eids.items():
        if not eid:
            continue

        print(f"\n{SUBSEP}")
        print(f"  {label} — externalId: {eid}")
        print(SUBSEP)

        # Exposures sin filtros
        print(f"\n▸ GET /v2/transfers/{eid}/exposures (sin filtros)")
        code, data = api("GET", f"/v2/transfers/{eid}/exposures")
        pj(data, max_lines=100)

        # Exposures con direction=received
        print(f"\n▸ GET /v2/transfers/{eid}/exposures?direction=received")
        code, data = api("GET", f"/v2/transfers/{eid}/exposures",
                        params={"direction": "received"})
        pj(data, max_lines=60)

        # Exposures con direction=sent
        print(f"\n▸ GET /v2/transfers/{eid}/exposures?direction=sent")
        code, data = api("GET", f"/v2/transfers/{eid}/exposures",
                        params={"direction": "sent"})
        pj(data, max_lines=60)


# ════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════
def main():
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║     VECTORA AML — Chainalysis KYT API Explorer v2               ║
║     {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                                       ║
║                                                                  ║
║     Endpoints documentados:                                      ║
║       • POST /v2/users/{{userId}}/transfers         (register TX)  ║
║       • GET  /v2/transfers/{{externalId}}            (summary)     ║
║       • GET  /v2/transfers/{{eid}}/exposures         (direct exp)  ║
║       • GET  /v2/transfers/{{eid}}/network-identif.  (clusters)    ║
║       • GET  /v2/transfers/{{eid}}/alerts            (alerts)      ║
║       • GET  /v2/transfers/{{eid}}/high-risk-addr    (ident.)      ║
║       • POST /v2/users/{{uid}}/withdrawal-attempts   (pre-screen)  ║
║       • GET  /v2/withdrawal-attempts/{{eid}}/exposures             ║
║       • GET  /v2/withdrawal-attempts/{{eid}}/high-risk-addresses   ║
║       • GET  /v2/withdrawal-attempts/{{eid}}/network-identif.      ║
║       • GET  /v2/withdrawal-attempts/{{eid}}/alerts                ║
║       • GET  /v2/users/{{userId}}                    (user risk)   ║
║       • GET  /v2/users                              (list users)  ║
║       • GET  /v2/alerts                             (all alerts)  ║
║       • GET  /v2/categories                         (categories)  ║
╚══════════════════════════════════════════════════════════════════╝
""")

    sections = {
        "1": ("categories", s1_categories),
        "2": ("users", s2_users),
        "3": ("transfers", None),       # needs special handling
        "4": ("details", None),         # needs eids
        "5": ("withdrawal", None),      # needs special handling
        "6": ("alerts", s6_alerts),
        "7": ("discovery", s7_discovery),
        "8": ("sanctions", s8_sanctions),
        "9": ("deep", None),            # needs eids
    }

    if "--section" in sys.argv:
        target = sys.argv[sys.argv.index("--section") + 1]
    else:
        target = "all"

    # Sections that don't need dependencies
    if target in ("all", "1", "categories"):
        s1_categories()
    if target in ("all", "2", "users"):
        s2_users()

    # Register transfers
    eids = {}
    if target in ("all", "3", "transfers", "4", "details", "9", "deep"):
        eids = s3_transfers()

    # Transfer details (needs eids)
    if target in ("all", "4", "details"):
        s4_transfer_details(eids)

    # Withdrawal attempts (wallet screening!)
    if target in ("all", "5", "withdrawal"):
        s5_withdrawal_attempts()

    # Alerts
    if target in ("all", "6", "alerts"):
        s6_alerts()

    # Discovery
    if target in ("all", "7", "discovery"):
        s7_discovery()

    # Sanctions
    if target in ("all", "8", "sanctions"):
        s8_sanctions()

    # Address Screening API (CLUSTERS!)
    if target in ("all", "9", "address", "screening"):
        s9_address_screening()

    # Deep dive
    if target in ("all", "10", "deep"):
        s10_deep_dive(eids)

    print(f"""
{SEP}
  EXPLORACIÓN COMPLETA — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
{SEP}

  USO:
    python3 explore_chainalysis.py                       # Todo
    python3 explore_chainalysis.py --section 1           # Solo categories
    python3 explore_chainalysis.py --section 5           # Solo withdrawal attempts
    python3 explore_chainalysis.py --section discovery   # Endpoint discovery

  SECCIONES:
    1/categories  — Categorías de riesgo
    2/users       — Usuarios/clientes registrados
    3/transfers   — Registrar TXs
    4/details     — Summary + exposures + alerts + network IDs
    5/withdrawal  — Pre-screening de wallets (CLAVE para trace!)
    6/alerts      — Alertas globales
    7/discovery   — Buscar endpoints ocultos
    8/sanctions   — API de sanciones OFAC
    9/address     — ADDRESS SCREENING API (cluster + risk por wallet!)
    10/deep       — Deep dive exposures

  GUARDAR OUTPUT:
    python3 explore_chainalysis.py > chainalysis_exploration.txt 2>&1
    python3 explore_chainalysis.py --section 5 > withdrawal_test.txt 2>&1
""")


if __name__ == "__main__":
    main()
