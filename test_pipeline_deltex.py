#!/usr/bin/env python3
"""
VECTORA AML — Pipeline Completo para TX DELTEX
TX: dc628aae13d57b7099e613f6e0fe433845a8707650b3bff960328d7e0bdefd29
Sender: THwHh6EJCPZ9G2c4YysSiaen1hmDcvnjND (whitelisted)
Receiver: THaEwWojvq5PatoRCa4pYThZm8zW88pj2p (Utila vault - DELTEX)
"""

import os
import json
import time
import requests
from datetime import datetime

# ── Config ──────────────────────────────────────────────
CHAIN_KYT_KEY = os.environ.get("CHAINALYSIS_KYT_KEY", "20015585b408146ea6634bdae442f443784439d36a0009f301f05cb56b0ae64d")
CHAIN_SANCTIONS_KEY = os.environ.get("CHAINALYSIS_SANCTIONS_KEY", "cb4dd1ffdc458b86ef8e49a486412e830ad7258012170221291fc7506582b35a")

TX_HASH = "dc628aae13d57b7099e613f6e0fe433845a8707650b3bff960328d7e0bdefd29"
SENDER = "THwHh6EJCPZ9G2c4YysSiaen1hmDcvnjND"
RECEIVER = "THaEwWojvq5PatoRCa4pYThZm8zW88pj2p"
CLIENT = "DELTEX"
USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

SEP = "=" * 80
SUB = "-" * 60

def pj(data, label=""):
    if label:
        print(f"\n  {label}:")
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))

def section(num, title):
    print(f"\n{SEP}")
    print(f"  PASO {num}: {title}")
    print(SEP)


# ════════════════════════════════════════════════════════
# PASO 0: Obtener info de la TX on-chain (TronGrid)
# ════════════════════════════════════════════════════════
def paso0_tx_onchain():
    section(0, "INFO ON-CHAIN DE LA TX (TronGrid)")

    # Info de la TX
    print("\n  Consultando TX en TronGrid...")
    try:
        r = requests.get(
            f"https://api.trongrid.io/v1/transactions/{TX_HASH}/events",
            timeout=15
        )
        if r.status_code == 200:
            events = r.json()
            pj(events, "TX Events")
        else:
            print(f"  [{r.status_code}] Error obteniendo TX events")
    except Exception as e:
        print(f"  Error: {e}")

    # TRC20 transfers del sender
    print(f"\n  Consultando historial TRC20 del sender {SENDER}...")
    try:
        r = requests.get(
            f"https://api.trongrid.io/v1/accounts/{SENDER}/transactions/trc20",
            params={
                "contract_address": USDT_CONTRACT,
                "only_confirmed": "true",
                "limit": 20,
            },
            timeout=15
        )
        if r.status_code == 200:
            data = r.json()
            txs = data.get("data", [])
            print(f"  Encontradas {len(txs)} transacciones TRC20 del sender")
            for tx in txs[:5]:
                val = int(tx.get("value", "0")) / 1e6
                from_addr = tx.get("from", "")
                to_addr = tx.get("to", "")
                tx_id = tx.get("transaction_id", "")[:16]
                ts = tx.get("block_timestamp", 0)
                dt = datetime.fromtimestamp(ts/1000).strftime("%Y-%m-%d %H:%M") if ts else "?"
                direction = "SENT" if from_addr == SENDER else "RECV"
                other = to_addr if direction == "SENT" else from_addr
                print(f"    {dt} | {direction} | ${val:,.2f} USDT | {other[:16]}... | tx:{tx_id}...")
    except Exception as e:
        print(f"  Error: {e}")

    return True


# ════════════════════════════════════════════════════════
# PASO 1: Verificar whitelist (simulado)
# ════════════════════════════════════════════════════════
def paso1_whitelist():
    section(1, "VERIFICACION DE WHITELIST")
    print(f"  Cliente: {CLIENT}")
    print(f"  Wallet destino (Utila): {RECEIVER}")
    print(f"  Sender: {SENDER}")
    print(f"  Estado whitelist: REGISTRADA (whitelisted)")
    print(f"  >> No se genera alerta de wallet desconocida")
    return True


# ════════════════════════════════════════════════════════
# PASO 2: Chainalysis KYT — Screening de la TX
# ════════════════════════════════════════════════════════
def paso2_chainalysis_kyt():
    section(2, "CHAINALYSIS KYT — SCREENING DE TX")
    headers = {"Token": CHAIN_KYT_KEY, "Content-Type": "application/json"}
    base = "https://api.chainalysis.com"
    results = {}

    # 2a. Registrar transfer
    print(f"\n  2a. Registrando transfer...")
    payload = {
        "asset": "USDT_TRX",
        "transferReference": f"{TX_HASH}:{RECEIVER}",
        "direction": "received",
        "assetAmount": 0  # KYT lo detecta del chain
    }
    try:
        r = requests.post(f"{base}/v2/users/{CLIENT}/transfers",
                         headers=headers, json=payload, timeout=30)
        print(f"  [{r.status_code}]")
        if r.status_code in (200, 201, 202):
            data = r.json()
            pj(data, "Register Response")
            results["external_id"] = data.get("externalId")
        else:
            print(f"  Error: {r.text}")
    except Exception as e:
        print(f"  Error: {e}")

    eid = results.get("external_id")
    if not eid:
        print("  !! No se obtuvo externalId, no se puede continuar con KYT")
        return results

    # 2b. Poll summary hasta procesamiento
    print(f"\n  2b. Polling transfer summary (externalId: {eid})...")
    for attempt in range(10):
        try:
            r = requests.get(f"{base}/v2/transfers/{eid}",
                           headers=headers, timeout=15)
            if r.status_code == 200:
                data = r.json()
                if data.get("updatedAt"):
                    print(f"  Procesada en intento {attempt+1}")
                    pj(data, "Transfer Summary")
                    results["summary"] = data
                    break
                else:
                    print(f"  Intento {attempt+1}/10: updatedAt null, esperando 5s...")
                    time.sleep(5)
        except Exception as e:
            print(f"  Error polling: {e}")
            time.sleep(5)

    # 2c. Direct Exposure
    print(f"\n  2c. Direct Exposure...")
    try:
        r = requests.get(f"{base}/v2/transfers/{eid}/exposures",
                       headers=headers, timeout=15)
        print(f"  [{r.status_code}]")
        if r.status_code == 200:
            data = r.json()
            pj(data, "Direct Exposure")
            results["exposures"] = data
    except Exception as e:
        print(f"  Error: {e}")

    # 2d. Alerts
    print(f"\n  2d. Transfer Alerts...")
    try:
        r = requests.get(f"{base}/v2/transfers/{eid}/alerts",
                       headers=headers, timeout=15)
        print(f"  [{r.status_code}]")
        if r.status_code == 200:
            data = r.json()
            pj(data, "Alerts")
            results["alerts"] = data
    except Exception as e:
        print(f"  Error: {e}")

    # 2e. Network Identifications
    print(f"\n  2e. Network Identifications...")
    try:
        r = requests.get(f"{base}/v2/transfers/{eid}/network-identifications",
                       headers=headers, timeout=15)
        print(f"  [{r.status_code}]")
        if r.status_code == 200:
            data = r.json()
            pj(data, "Network IDs")
            results["network_ids"] = data
    except Exception as e:
        print(f"  Error: {e}")

    # 2f. High Risk Addresses
    print(f"\n  2f. High Risk Addresses...")
    try:
        r = requests.get(f"{base}/v2/transfers/{eid}/high-risk-addresses",
                       headers=headers, timeout=15)
        print(f"  [{r.status_code}]")
        if r.status_code == 200:
            data = r.json()
            pj(data, "High Risk Addresses")
            results["high_risk"] = data
    except Exception as e:
        print(f"  Error: {e}")

    # 2g. User Risk Score
    print(f"\n  2g. User Risk Score ({CLIENT})...")
    try:
        r = requests.get(f"{base}/v2/users/{CLIENT}",
                       headers=headers, timeout=15)
        print(f"  [{r.status_code}]")
        if r.status_code == 200:
            data = r.json()
            pj(data, "User Risk")
            results["user_risk"] = data
    except Exception as e:
        print(f"  Error: {e}")

    return results


# ════════════════════════════════════════════════════════
# PASO 3: Chainalysis Address Screening — Sender
# ════════════════════════════════════════════════════════
def paso3_address_screening():
    section(3, "CHAINALYSIS ADDRESS SCREENING — SENDER")
    headers = {"Token": CHAIN_KYT_KEY, "Content-Type": "application/json"}
    base = "https://api.chainalysis.com"
    results = {}

    # Registrar address
    print(f"\n  Registrando sender {SENDER}...")
    try:
        r = requests.post(f"{base}/api/risk/v2/entities",
                        headers=headers, json={"address": SENDER}, timeout=15)
        print(f"  Register [{r.status_code}]: {r.text[:200]}")
    except Exception as e:
        print(f"  Error register: {e}")

    time.sleep(2)

    # Consultar risk + cluster
    print(f"\n  Consultando cluster + risk...")
    try:
        r = requests.get(f"{base}/api/risk/v2/entities/{SENDER}",
                       headers=headers, timeout=15)
        print(f"  [{r.status_code}]")
        if r.status_code == 200:
            data = r.json()
            pj(data, "Address Screening - Sender")
            results["sender"] = data
        else:
            print(f"  Response: {r.text[:300]}")
            results["sender_error"] = {"status": r.status_code, "body": r.text[:300]}
    except Exception as e:
        print(f"  Error: {e}")

    return results


# ════════════════════════════════════════════════════════
# PASO 4: Chainalysis Sanctions — OFAC Check
# ════════════════════════════════════════════════════════
def paso4_sanctions():
    section(4, "CHAINALYSIS SANCTIONS — OFAC CHECK")
    headers = {"X-API-Key": CHAIN_SANCTIONS_KEY}
    base = "https://public.chainalysis.com/api/v1"
    results = {}

    for label, addr in [("Sender", SENDER), ("Receiver", RECEIVER)]:
        print(f"\n  Checking {label}: {addr}")
        try:
            r = requests.get(f"{base}/address/{addr}",
                           headers=headers, timeout=15)
            print(f"  [{r.status_code}]")
            if r.status_code == 200:
                data = r.json()
                identifications = data.get("identifications", [])
                if identifications:
                    print(f"  !! SANCTIONED — {len(identifications)} matches")
                    pj(data, f"OFAC - {label}")
                else:
                    print(f"  CLEAN — No sanctions matches")
                results[label.lower()] = data
        except Exception as e:
            print(f"  Error: {e}")

    return results


# ════════════════════════════════════════════════════════
# PASO 5: Backward Trace On-Chain (TronGrid, 2 hops)
# ════════════════════════════════════════════════════════
def paso5_backward_trace():
    section(5, "BACKWARD TRACE ON-CHAIN (TronGrid, 2 hops)")
    results = {"hop1": [], "hop2": []}

    def get_trc20_senders(address, label=""):
        """Obtiene senders de TRC20 USDT para una dirección."""
        senders = []
        try:
            r = requests.get(
                f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20",
                params={
                    "contract_address": USDT_CONTRACT,
                    "only_confirmed": "true",
                    "limit": 100,
                    "only_to": "true",
                },
                timeout=15
            )
            if r.status_code == 200:
                txs = r.json().get("data", [])
                seen = set()
                for tx in txs:
                    from_addr = tx.get("from", "")
                    if from_addr and from_addr != address and from_addr not in seen:
                        seen.add(from_addr)
                        val = int(tx.get("value", "0")) / 1e6
                        senders.append({
                            "address": from_addr,
                            "amount": val,
                            "tx_id": tx.get("transaction_id", ""),
                        })
        except Exception as e:
            print(f"    Error getting senders for {address[:16]}: {e}")
        return senders

    # Hop 1: quién le envió USDT al sender
    print(f"\n  HOP 1: Senders de {SENDER[:16]}...")
    hop1 = get_trc20_senders(SENDER)
    print(f"  Encontrados: {len(hop1)} senders únicos")
    for s in hop1[:15]:
        print(f"    {s['address']} | ${s['amount']:,.2f} USDT")
    if len(hop1) > 15:
        print(f"    ... y {len(hop1)-15} más")
    results["hop1"] = hop1

    # Hop 2: quién le envió a cada sender de hop 1 (top 5 por volumen)
    hop1_sorted = sorted(hop1, key=lambda x: x["amount"], reverse=True)
    print(f"\n  HOP 2: Senders de los top {min(5, len(hop1_sorted))} de hop 1...")
    all_hop2 = []
    for s in hop1_sorted[:5]:
        print(f"\n    Tracing {s['address'][:16]}... (${s['amount']:,.2f})")
        hop2 = get_trc20_senders(s["address"])
        print(f"    → {len(hop2)} senders")
        for h in hop2[:5]:
            print(f"      {h['address']} | ${h['amount']:,.2f}")
        if len(hop2) > 5:
            print(f"      ... y {len(hop2)-5} más")
        all_hop2.extend(hop2)
        time.sleep(0.5)  # rate limit
    results["hop2"] = all_hop2

    # Resumen
    all_addresses = set()
    for s in hop1:
        all_addresses.add(s["address"])
    for s in all_hop2:
        all_addresses.add(s["address"])
    results["unique_addresses"] = list(all_addresses)
    print(f"\n  RESUMEN TRACE:")
    print(f"    Hop 1: {len(hop1)} senders")
    print(f"    Hop 2: {len(all_hop2)} senders")
    print(f"    Total addresses únicas: {len(all_addresses)}")

    return results


# ════════════════════════════════════════════════════════
# PASO 6: Screening de wallets del trace (Address Screening)
# ════════════════════════════════════════════════════════
def paso6_screen_trace_wallets(trace_results):
    section(6, "SCREENING DE WALLETS DEL TRACE")
    headers = {"Token": CHAIN_KYT_KEY, "Content-Type": "application/json"}
    base = "https://api.chainalysis.com"
    sanctions_headers = {"X-API-Key": CHAIN_SANCTIONS_KEY}
    sanctions_base = "https://public.chainalysis.com/api/v1"
    results = {}

    # Solo hop 1 (screening completo) + sample de hop 2
    hop1_addrs = [s["address"] for s in trace_results.get("hop1", [])]
    hop2_addrs = list(set(s["address"] for s in trace_results.get("hop2", [])))

    # Limitar hop2 a 10 para no abusar rate limits
    hop2_sample = hop2_addrs[:10]

    all_to_screen = [(addr, "hop1") for addr in hop1_addrs] + \
                    [(addr, "hop2") for addr in hop2_sample]

    print(f"  Screening {len(hop1_addrs)} wallets hop1 + {len(hop2_sample)} wallets hop2")

    for addr, hop in all_to_screen:
        print(f"\n  {SUB}")
        print(f"  {hop.upper()} | {addr}")

        wallet_result = {"address": addr, "hop": hop}

        # Address Screening (cluster + risk)
        try:
            requests.post(f"{base}/api/risk/v2/entities",
                        headers=headers, json={"address": addr}, timeout=10)
            time.sleep(1)
            r = requests.get(f"{base}/api/risk/v2/entities/{addr}",
                           headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                cluster = data.get("cluster", {})
                risk = data.get("risk", "unknown")
                print(f"  Address Screening: risk={risk}, cluster={cluster.get('name', 'N/A')} ({cluster.get('category', 'N/A')})")
                wallet_result["address_screening"] = data
            else:
                print(f"  Address Screening: [{r.status_code}] {r.text[:100]}")
                wallet_result["address_screening_error"] = r.status_code
        except Exception as e:
            print(f"  Address Screening error: {e}")

        # Sanctions check
        try:
            r = requests.get(f"{sanctions_base}/address/{addr}",
                           headers=sanctions_headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                ids = data.get("identifications", [])
                sanctioned = len(ids) > 0
                print(f"  Sanctions: {'!! SANCTIONED' if sanctioned else 'CLEAN'}")
                wallet_result["sanctioned"] = sanctioned
                if sanctioned:
                    wallet_result["sanctions_detail"] = ids
        except Exception as e:
            print(f"  Sanctions error: {e}")

        # Tronscan blacklist (Tether freeze)
        try:
            r = requests.get(
                f"https://apilist.tronscanapi.com/api/stableCoin/blackList",
                params={"blackAddress": addr},
                timeout=10
            )
            if r.status_code == 200:
                data = r.json()
                blacklisted = data.get("total", 0) > 0
                print(f"  Tether Blacklist: {'!! BLACKLISTED' if blacklisted else 'CLEAN'}")
                wallet_result["tether_blacklisted"] = blacklisted
        except Exception as e:
            print(f"  Tronscan error: {e}")

        results[addr] = wallet_result
        time.sleep(0.5)

    # Resumen
    print(f"\n{SUB}")
    print(f"  RESUMEN SCREENING WALLETS:")
    sanctioned_count = sum(1 for r in results.values() if r.get("sanctioned"))
    blacklisted_count = sum(1 for r in results.values() if r.get("tether_blacklisted"))
    high_risk_count = sum(1 for r in results.values()
                         if isinstance(r.get("address_screening"), dict)
                         and r["address_screening"].get("risk") in ("High", "Severe"))
    print(f"    Total screened: {len(results)}")
    print(f"    Sanctioned: {sanctioned_count}")
    print(f"    Tether blacklisted: {blacklisted_count}")
    print(f"    High/Severe risk: {high_risk_count}")

    return results


# ════════════════════════════════════════════════════════
# PASO 7: KYT Categories (para referencia)
# ════════════════════════════════════════════════════════
def paso7_categories():
    section(7, "KYT CATEGORIES (referencia)")
    headers = {"Token": CHAIN_KYT_KEY}
    try:
        r = requests.get("https://api.chainalysis.com/v2/categories",
                       headers=headers, timeout=15)
        if r.status_code == 200:
            pj(r.json(), "Categories")
    except Exception as e:
        print(f"  Error: {e}")


# ════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════
def main():
    print(f"""
{'=' * 80}
  VECTORA AML MONITOR — PIPELINE COMPLETO
  TX: {TX_HASH}
  Sender: {SENDER}
  Receiver: {RECEIVER}
  Client: {CLIENT}
  Timestamp: {datetime.now().isoformat()}
{'=' * 80}
""")

    start = time.time()

    # Paso 0: Info on-chain
    paso0_tx_onchain()

    # Paso 1: Whitelist (simulado)
    paso1_whitelist()

    # Paso 2: Chainalysis KYT screening
    kyt_results = paso2_chainalysis_kyt()

    # Paso 3: Address Screening del sender
    addr_results = paso3_address_screening()

    # Paso 4: Sanctions OFAC
    sanctions_results = paso4_sanctions()

    # Paso 5: Backward trace
    trace_results = paso5_backward_trace()

    # Paso 6: Screening de wallets del trace
    wallet_results = paso6_screen_trace_wallets(trace_results)

    # Paso 7: Categories
    paso7_categories()

    elapsed = time.time() - start

    print(f"""
{'=' * 80}
  PIPELINE COMPLETO
  Tiempo total: {elapsed:.1f}s
  TX: {TX_HASH}
  Client: {CLIENT}
{'=' * 80}
""")


if __name__ == "__main__":
    main()
