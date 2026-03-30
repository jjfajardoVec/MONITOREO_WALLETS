"""
VECTORA AML — Elliptic API Client (Motor AML #1)
Screening de transacciones y wallets via Elliptic.

Auth: HMAC-SHA256
  - x-access-key: API key
  - x-access-sign: Base64(HMAC-SHA256(secret_decoded, timestamp + METHOD + path.lower() + body))
  - x-access-timestamp: milisegundos epoch

Endpoints:
  - POST /v2/analyses/synchronous      → TX analysis (source_of_funds)
  - POST /v2/wallet/synchronous        → Wallet analysis (wallet_exposure)
  - GET  /v2/analyses/{id}             → Get analysis by ID

Response clave:
  - risk_score (0-10)
  - evaluation_detail con contributions[]
  - matched_elements con contribution_percentage, counterparty_percentage, indirect_percentage, min_number_of_hops
  - entities con name, category, actor_id, is_vasp
"""

import hashlib
import hmac
import base64
import json
import time
import logging
import requests

logger = logging.getLogger("vectora.elliptic")


class EllipticClient:

    BASE_URL = "https://aml-api.elliptic.co"

    # Categorías que Elliptic clasifica como riesgo alto
    RISK_CATEGORIES = {
        "dark market", "darknet", "darknet marketplace",
        "mixer", "tumbler", "mixing",
        "ransomware", "malware",
        "scam", "fraud", "theft", "stolen funds",
        "terrorist financing", "terrorism",
        "sanctioned entity", "sanctions", "ofac",
        "child exploitation", "csam",
        "cybercriminal", "hacking",
        "token blacklisting",
        "gambling",
        "illicit",
    }

    def __init__(self, api_key: str, api_secret: str, timeout: int = 30):
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout = timeout

    # ── Auth ────────────────────────────────────────────────

    def _sign(self, method: str, path: str, body: str = "") -> dict:
        """Genera headers de autenticación HMAC-SHA256 para Elliptic."""
        timestamp = str(int(time.time() * 1000))  # milisegundos
        message = timestamp + method.upper() + path.lower() + body
        secret_decoded = base64.b64decode(self.api_secret)
        signature = base64.b64encode(
            hmac.HMAC(secret_decoded, message.encode("utf-8"), hashlib.sha256).digest()
        ).decode("utf-8")

        return {
            "x-access-key": self.api_key,
            "x-access-sign": signature,
            "x-access-timestamp": timestamp,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, payload: dict = None,
                 retries: int = 3) -> dict:
        """Ejecuta request autenticado contra Elliptic API con retry."""
        body = ""
        if payload is not None:
            # CRUCIAL: separators compactos para que el signature coincida
            body = json.dumps(payload, separators=(",", ":"))

        for attempt in range(retries):
            # Re-firmar en cada intento (timestamp cambia)
            headers = self._sign(method, path, body)
            url = f"{self.BASE_URL}{path}"

            try:
                if method == "GET":
                    r = requests.get(url, headers=headers, timeout=self.timeout)
                elif method == "POST":
                    r = requests.post(url, headers=headers, data=body, timeout=self.timeout)
                else:
                    raise ValueError(f"Método no soportado: {method}")

                logger.debug(f"[{r.status_code}] {method} {path}")

                if r.status_code in (200, 201):
                    return r.json()

                # Retry en errores de servidor (5xx) o rate limit (429)
                if r.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    wait = 2 ** (attempt + 1)
                    logger.warning(f"Elliptic {r.status_code}, retrying in {wait}s...")
                    time.sleep(wait)
                    continue

                error_body = r.text
                try:
                    error_body = r.json()
                except Exception:
                    pass
                logger.error(f"Elliptic API error {r.status_code}: {error_body}")
                return {"_error": r.status_code, "_body": error_body}

            except requests.exceptions.Timeout:
                if attempt < retries - 1:
                    wait = 2 ** (attempt + 1)
                    logger.warning(f"Elliptic timeout, retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                logger.error(f"Elliptic timeout after {retries} attempts: {method} {path}")
                return {"_error": "timeout"}
            except requests.exceptions.RequestException as e:
                logger.error(f"Elliptic request error: {e}")
                return {"_error": "request_exception", "_detail": str(e)}

    # ── TX Analysis (Source of Funds) ──────────────────────

    def analyze_tx(self, tx_hash: str, output_address: str,
                   customer_reference: str = "") -> dict:
        """
        Análisis de transacción — source_of_funds.
        Retorna risk_score, entities, contributions, matched_elements.

        Args:
            tx_hash: Hash de la transacción
            output_address: Dirección destino del depósito
            customer_reference: Nombre del cliente (para tracking en Elliptic)
        """
        payload = {
            "subject": {
                "asset": "holistic",
                "blockchain": "holistic",
                "type": "transaction",
                "hash": tx_hash,
                "output_address": output_address,
            },
            "type": "source_of_funds",
            "customer_reference": customer_reference,
        }

        logger.info(f"Analyzing TX {tx_hash[:16]}... for {customer_reference}")
        result = self._request("POST", "/v2/analyses/synchronous", payload)

        if "_error" in result:
            return result

        return self._parse_analysis(result, tx_hash)

    # ── Wallet Analysis (Wallet Exposure) ──────────────────

    def analyze_wallet(self, address: str,
                       customer_reference: str = "") -> dict:
        """
        Análisis de wallet — wallet_exposure.
        Retorna risk_score y exposición histórica de la wallet.

        Args:
            address: Dirección de la wallet a analizar
            customer_reference: Nombre del cliente
        """
        payload = {
            "subject": {
                "asset": "holistic",
                "blockchain": "holistic",
                "type": "address",
                "hash": address,
            },
            "type": "wallet_exposure",
            "customer_reference": customer_reference,
        }

        logger.info(f"Analyzing wallet {address[:16]}... for {customer_reference}")
        result = self._request("POST", "/v2/wallet/synchronous", payload)

        if "_error" in result:
            return result

        return self._parse_wallet_analysis(result, address)

    # ── Get Analysis by ID ─────────────────────────────────

    def get_analysis(self, analysis_id: str) -> dict:
        """Obtiene un análisis existente por su ID."""
        logger.info(f"Fetching analysis {analysis_id}")
        return self._request("GET", f"/v2/analyses/{analysis_id}")

    # ── Batch: analizar múltiples wallets ──────────────────

    def analyze_wallets_batch(self, addresses: list,
                              customer_reference: str = "",
                              delay: float = 0.5) -> dict:
        """
        Analiza múltiples wallets en batch.
        Respeta rate limits con delay entre calls.

        Returns:
            {address: result_dict, ...}
        """
        results = {}
        total = len(addresses)

        for i, addr in enumerate(addresses, 1):
            logger.info(f"Batch wallet {i}/{total}: {addr[:16]}...")
            results[addr] = self.analyze_wallet(addr, customer_reference)

            if i < total:
                time.sleep(delay)

        return results

    # ── Parsing ────────────────────────────────────────────

    def _parse_analysis(self, raw: dict, tx_hash: str) -> dict:
        """Extrae datos relevantes de un TX analysis response."""
        analysis_id = raw.get("id", "")
        risk_score = raw.get("risk_score")

        # Extraer evaluation_detail
        eval_detail = raw.get("evaluation_detail", {})
        contributions = eval_detail.get("contributions", [])

        # Extraer matched_elements
        matched = raw.get("matched_elements", {})

        # Extraer entidades blacklisted
        blacklisted = []
        source_of_funds = {}

        for contrib in contributions:
            entity = contrib.get("entity", {})
            category = entity.get("category", "")
            name = entity.get("name", "Unknown")
            is_vasp = entity.get("is_vasp", False)

            # Porcentajes
            contribution_pct = contrib.get("contribution_percentage", 0)
            counterparty_pct = contrib.get("counterparty_percentage", 0)
            indirect_pct = contrib.get("indirect_percentage", 0)
            min_hops = contrib.get("min_number_of_hops", 0)

            # Acumular source of funds por categoría
            if category:
                source_of_funds[category] = source_of_funds.get(category, 0) + contribution_pct

            if any(rc in category.lower() for rc in self.RISK_CATEGORIES):
                blacklisted.append({
                    "name": name,
                    "category": category,
                    "contribution_pct": contribution_pct,
                    "counterparty_pct": counterparty_pct,
                    "indirect_pct": indirect_pct,
                    "min_hops": min_hops,
                    "is_vasp": is_vasp,
                    "was_removed": entity.get("was_removed", False),
                })

        return {
            "analysis_id": analysis_id,
            "tx_hash": tx_hash,
            "risk_score": risk_score,
            "source_of_funds": source_of_funds,
            "blacklisted_entities": blacklisted,
            "total_blacklisted_pct": sum(e["contribution_pct"] for e in blacklisted),
            "contributions_count": len(contributions),
            "matched_elements": matched,
            "raw": raw,
        }

    def _parse_wallet_analysis(self, raw: dict, address: str) -> dict:
        """Extrae datos relevantes de un wallet analysis response."""
        analysis_id = raw.get("id", "")
        risk_score = raw.get("risk_score")

        eval_detail = raw.get("evaluation_detail", {})
        contributions = eval_detail.get("contributions", [])

        # Categorizar exposures
        exposures = {}
        risk_entities = []

        for contrib in contributions:
            entity = contrib.get("entity", {})
            category = entity.get("category", "")
            name = entity.get("name", "Unknown")
            contribution_pct = contrib.get("contribution_percentage", 0)
            min_hops = contrib.get("min_number_of_hops", 0)

            if category:
                exposures[category] = exposures.get(category, 0) + contribution_pct

            if any(rc in category.lower() for rc in self.RISK_CATEGORIES):
                risk_entities.append({
                    "name": name,
                    "category": category,
                    "contribution_pct": contribution_pct,
                    "min_hops": min_hops,
                })

        return {
            "analysis_id": analysis_id,
            "address": address,
            "risk_score": risk_score,
            "exposures": exposures,
            "risk_entities": risk_entities,
            "total_risk_pct": sum(e["contribution_pct"] for e in risk_entities),
            "contributions_count": len(contributions),
            "raw": raw,
        }
