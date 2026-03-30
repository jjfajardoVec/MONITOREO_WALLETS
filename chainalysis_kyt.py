"""
VECTORA AML — Chainalysis KYT + Address Screening Client (Motor AML #2)

3 APIs de Chainalysis integradas:

1. KYT (Know Your Transaction):
   Base: https://api.chainalysis.com
   Auth: Token: {API_KEY}
   - Registrar transfers received → obtener exposures, alerts, network IDs
   - Withdrawal attempts → pre-screening de wallets
   - User risk scores → riesgo acumulado del cliente

2. Address Screening (risk/v2):
   Base: https://api.chainalysis.com
   Auth: Token: {API_KEY}
   - GET /api/risk/v2/entities/{address} → cluster.name, category, risk, exposures
   - El endpoint MÁS importante para el backward trace

3. Sanctions (gratuita):
   Base: https://public.chainalysis.com/api/v1
   Auth: X-API-Key: {SANCTIONS_KEY}
   - Ya implementada en sanctions.py, no se duplica aquí
"""

import time
import logging
import requests

logger = logging.getLogger("vectora.chainalysis")


class ChainalysisKYT:
    """Cliente para Chainalysis KYT API v2 + Address Screening API."""

    KYT_BASE = "https://api.chainalysis.com"

    def __init__(self, api_key: str, timeout: int = 30,
                 poll_interval: int = 5, poll_max_attempts: int = 12):
        self.api_key = api_key
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.poll_max_attempts = poll_max_attempts
        self.headers = {
            "Token": api_key,
            "Content-Type": "application/json",
        }

    # ── HTTP helpers ───────────────────────────────────────

    def _get(self, path: str, params: dict = None) -> tuple:
        """GET request. Returns (status_code, data)."""
        url = f"{self.KYT_BASE}{path}"
        try:
            r = requests.get(url, headers=self.headers,
                           params=params, timeout=self.timeout)
            logger.debug(f"[{r.status_code}] GET {path}")
            return r.status_code, self._parse_response(r)
        except requests.exceptions.Timeout:
            logger.error(f"Timeout: GET {path}")
            return 0, {"_error": "timeout"}
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error: GET {path} → {e}")
            return 0, {"_error": "request_exception", "_detail": str(e)}

    def _post(self, path: str, payload: dict) -> tuple:
        """POST request. Returns (status_code, data)."""
        url = f"{self.KYT_BASE}{path}"
        try:
            r = requests.post(url, headers=self.headers,
                            json=payload, timeout=self.timeout)
            logger.debug(f"[{r.status_code}] POST {path}")
            return r.status_code, self._parse_response(r)
        except requests.exceptions.Timeout:
            logger.error(f"Timeout: POST {path}")
            return 0, {"_error": "timeout"}
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error: POST {path} → {e}")
            return 0, {"_error": "request_exception", "_detail": str(e)}

    def _parse_response(self, r) -> dict:
        if r.status_code == 204:
            return None
        try:
            return r.json()
        except Exception:
            return {"_raw_text": r.text, "_status": r.status_code}

    # ══════════════════════════════════════════════════════
    # KYT API — TRANSFERS (screening de TXs received)
    # ══════════════════════════════════════════════════════

    def register_transfer(self, client_name: str, tx_hash: str,
                          output_address: str, amount: float,
                          asset: str = "USDT_TRX",
                          direction: str = "received") -> dict:
        """
        Registra una transferencia en KYT para screening.

        Args:
            client_name: userId en KYT (nombre del cliente)
            tx_hash: Hash de la transacción
            output_address: Dirección destino
            amount: Monto en unidades del asset
            asset: Asset code (USDT_TRX, USDT_ETH, USDC_SOL, etc.)
            direction: "received" o "sent"

        Returns:
            dict con externalId para consultas posteriores
        """
        payload = {
            "asset": asset,
            "transferReference": f"{tx_hash}:{output_address}",
            "direction": direction,
            "assetAmount": amount,
        }

        logger.info(f"Registering transfer for {client_name}: {tx_hash[:16]}...")
        code, data = self._post(f"/v2/users/{client_name}/transfers", payload)

        if code not in (200, 201, 202):
            logger.error(f"Register transfer failed [{code}]: {data}")
            return {"_error": code, "_body": data}

        external_id = data.get("externalId") if isinstance(data, dict) else None
        if external_id:
            logger.info(f"Transfer registered: externalId={external_id}")

        return data

    def get_transfer_summary(self, external_id: str,
                             wait_for_processing: bool = True) -> dict:
        """
        Obtiene el summary de una transfer.
        Si wait_for_processing=True, hace polling hasta que updatedAt != null.

        Returns:
            Transfer summary con rating, asset, amounts, updatedAt
        """
        for attempt in range(self.poll_max_attempts if wait_for_processing else 1):
            code, data = self._get(f"/v2/transfers/{external_id}")

            if code != 200:
                logger.error(f"Transfer summary failed [{code}]: {data}")
                return {"_error": code, "_body": data}

            if not wait_for_processing:
                return data

            # Verificar si ya fue procesada
            if isinstance(data, dict) and data.get("updatedAt"):
                logger.info(f"Transfer {external_id} processed: updatedAt={data['updatedAt']}")
                return data

            if attempt < self.poll_max_attempts - 1:
                logger.debug(f"Transfer not ready, polling ({attempt+1}/{self.poll_max_attempts})...")
                time.sleep(self.poll_interval)

        logger.warning(f"Transfer {external_id} not processed after {self.poll_max_attempts} attempts")
        return data  # retorna el último resultado aunque updatedAt sea null

    def get_transfer_exposures(self, external_id: str) -> dict:
        """
        Obtiene la exposición directa de una transfer.

        Returns:
            {"direct": {"name": "Binance", "category": "exchange"}}
        """
        code, data = self._get(f"/v2/transfers/{external_id}/exposures")
        if code != 200:
            logger.warning(f"Transfer exposures [{code}] for {external_id}")
        return data

    def get_transfer_alerts(self, external_id: str) -> list:
        """
        Obtiene alertas generadas para una transfer específica.

        Returns:
            Lista de alertas con severity, alertType, category, service
        """
        code, data = self._get(f"/v2/transfers/{external_id}/alerts")
        if code != 200:
            logger.warning(f"Transfer alerts [{code}] for {external_id}")
            return []
        return data if isinstance(data, list) else [data] if data else []

    def get_transfer_network_ids(self, external_id: str) -> dict:
        """
        Obtiene network identifications (cluster names del counterparty).
        Funciona para assets CDN; retorna 400 para assets fully-supported.

        Returns:
            {"count": N, "networkIdentificationOrgs": [{"name": "..."}]}
        """
        code, data = self._get(f"/v2/transfers/{external_id}/network-identifications")
        if code == 400:
            logger.debug(f"Network IDs not available (fully-supported asset) for {external_id}")
            return {"_note": "fully_supported_asset", "count": 0}
        if code != 200:
            logger.warning(f"Network IDs [{code}] for {external_id}")
        return data

    def get_transfer_high_risk(self, external_id: str) -> dict:
        """
        Obtiene high-risk addresses (Chainalysis Identifications).

        Returns:
            {"chainalysisIdentifications": [...], "customAddresses": [...]}
        """
        code, data = self._get(f"/v2/transfers/{external_id}/high-risk-addresses")
        if code != 200:
            logger.warning(f"High risk addresses [{code}] for {external_id}")
        return data

    # ── Conveniencia: screening completo de una TX ─────────

    def screen_transfer(self, client_name: str, tx_hash: str,
                        output_address: str, amount: float,
                        asset: str = "USDT_TRX") -> dict:
        """
        Screening completo de una TX: register → poll → exposures + alerts + network.

        Returns:
            {
                "external_id": str,
                "summary": dict,
                "exposures": dict,
                "alerts": list,
                "network_ids": dict,
                "high_risk": dict,
            }
        """
        # 1. Registrar
        reg = self.register_transfer(client_name, tx_hash,
                                     output_address, amount, asset)
        if isinstance(reg, dict) and "_error" in reg:
            return {"_error": "register_failed", "_detail": reg}

        external_id = reg.get("externalId")
        if not external_id:
            return {"_error": "no_external_id", "_detail": reg}

        # 2. Poll summary hasta que esté listo
        summary = self.get_transfer_summary(external_id, wait_for_processing=True)

        # 3. Obtener exposures, alerts, network IDs, high-risk
        exposures = self.get_transfer_exposures(external_id)
        alerts = self.get_transfer_alerts(external_id)
        network_ids = self.get_transfer_network_ids(external_id)
        high_risk = self.get_transfer_high_risk(external_id)

        return {
            "external_id": external_id,
            "summary": summary,
            "exposures": exposures,
            "alerts": alerts,
            "network_ids": network_ids,
            "high_risk": high_risk,
        }

    # ══════════════════════════════════════════════════════
    # KYT API — USERS (risk score de clientes)
    # ══════════════════════════════════════════════════════

    def get_user_risk(self, client_name: str) -> dict:
        """
        Obtiene el risk score acumulado de un cliente.
        El risk se calcula en base a todas sus transfers registradas.
        """
        code, data = self._get(f"/v2/users/{client_name}")
        if code != 200:
            logger.warning(f"User risk [{code}] for {client_name}")
        return data

    def list_users(self) -> list:
        """Lista todos los usuarios registrados en KYT."""
        code, data = self._get("/v2/users")
        if code != 200:
            return []
        return data if isinstance(data, list) else []

    # ══════════════════════════════════════════════════════
    # KYT API — ALERTS (globales)
    # ══════════════════════════════════════════════════════

    def get_alerts(self, severity: str = None, limit: int = 100) -> list:
        """
        Obtiene alertas globales de KYT.

        Args:
            severity: Filtrar por severity (LOW, MEDIUM, HIGH, SEVERE)
            limit: Máximo de alertas a retornar
        """
        params = {}
        if severity:
            params["severity"] = severity
        if limit:
            params["limit"] = limit

        code, data = self._get("/v2/alerts", params=params)
        if code != 200:
            return []
        return data if isinstance(data, list) else []

    # ══════════════════════════════════════════════════════
    # KYT API — CATEGORIES
    # ══════════════════════════════════════════════════════

    def get_categories(self) -> list:
        """Lista todas las categorías de riesgo de KYT."""
        code, data = self._get("/v2/categories")
        if code != 200:
            return []
        return data if isinstance(data, list) else []

    # ══════════════════════════════════════════════════════
    # KYT API — WITHDRAWAL ATTEMPTS (pre-screening de wallets)
    # Útil para screening de wallets del backward trace
    # ══════════════════════════════════════════════════════

    def register_withdrawal_attempt(self, client_name: str,
                                     address: str,
                                     asset: str = "USDT_TRX",
                                     amount: float = 0) -> dict:
        """
        Registra una wallet para pre-screening (withdrawal attempt).
        Retorna externalId para consultar exposures y high-risk.

        Args:
            client_name: userId en KYT
            address: Dirección de la wallet a screenar
            asset: Asset code
            amount: Monto (puede ser 0 para pre-screening)
        """
        payload = {
            "asset": asset,
            "address": address,
        }
        if amount > 0:
            payload["assetAmount"] = amount

        logger.info(f"Registering withdrawal attempt: {address[:16]}...")
        code, data = self._post(
            f"/v2/users/{client_name}/withdrawal-attempts", payload
        )

        if code not in (200, 201, 202):
            logger.error(f"Withdrawal attempt failed [{code}]: {data}")
            return {"_error": code, "_body": data}

        return data

    def get_withdrawal_summary(self, external_id: str,
                               wait_for_processing: bool = True) -> dict:
        """Obtiene summary de withdrawal attempt con polling."""
        for attempt in range(self.poll_max_attempts if wait_for_processing else 1):
            code, data = self._get(f"/v2/withdrawal-attempts/{external_id}")

            if code != 200:
                return {"_error": code, "_body": data}

            if not wait_for_processing:
                return data

            if isinstance(data, dict) and data.get("updatedAt"):
                return data

            if attempt < self.poll_max_attempts - 1:
                time.sleep(self.poll_interval)

        return data

    def get_withdrawal_exposures(self, external_id: str) -> dict:
        """Exposures del counterparty de un withdrawal attempt."""
        code, data = self._get(f"/v2/withdrawal-attempts/{external_id}/exposures")
        return data

    def get_withdrawal_high_risk(self, external_id: str) -> dict:
        """
        High-risk addresses + Chainalysis Identifications.
        Retorna: chainalysisIdentifications[], customAddresses[]
        con addressName, categoryName, description.
        """
        code, data = self._get(
            f"/v2/withdrawal-attempts/{external_id}/high-risk-addresses"
        )
        return data

    def get_withdrawal_network_ids(self, external_id: str) -> dict:
        """Network identifications de un withdrawal attempt."""
        code, data = self._get(
            f"/v2/withdrawal-attempts/{external_id}/network-identifications"
        )
        return data

    def get_withdrawal_alerts(self, external_id: str) -> list:
        """Alertas de un withdrawal attempt."""
        code, data = self._get(f"/v2/withdrawal-attempts/{external_id}/alerts")
        return data if isinstance(data, list) else []

    # ── Conveniencia: screening completo de wallet via withdrawal ──

    def screen_wallet_via_withdrawal(self, client_name: str,
                                      address: str,
                                      asset: str = "USDT_TRX") -> dict:
        """
        Screening completo de una wallet via withdrawal attempt.
        Register → poll → exposures + high-risk + network IDs.
        """
        reg = self.register_withdrawal_attempt(client_name, address, asset)
        if isinstance(reg, dict) and "_error" in reg:
            return {"_error": "register_failed", "_detail": reg}

        external_id = reg.get("externalId")
        if not external_id:
            return {"_error": "no_external_id", "_detail": reg}

        summary = self.get_withdrawal_summary(external_id)
        exposures = self.get_withdrawal_exposures(external_id)
        high_risk = self.get_withdrawal_high_risk(external_id)
        network_ids = self.get_withdrawal_network_ids(external_id)
        alerts = self.get_withdrawal_alerts(external_id)

        return {
            "external_id": external_id,
            "address": address,
            "summary": summary,
            "exposures": exposures,
            "high_risk": high_risk,
            "network_ids": network_ids,
            "alerts": alerts,
        }

    # ══════════════════════════════════════════════════════
    # ADDRESS SCREENING API (api/risk/v2)
    # API separada — retorna cluster directamente por address.
    # MÉTODO PREFERIDO para screening de wallets del trace.
    # ══════════════════════════════════════════════════════

    def register_address(self, address: str) -> dict:
        """
        Registra una dirección en Address Screening API.
        Necesario antes de consultar el cluster/risk.
        """
        code, data = self._post("/api/risk/v2/entities", {"address": address})
        if code not in (200, 201, 202):
            logger.warning(f"Address register [{code}] for {address[:16]}...")
        return data

    def get_address_risk(self, address: str) -> dict:
        """
        Obtiene cluster, risk y exposures de una dirección.
        ENDPOINT CLAVE para backward trace.

        Returns:
            {
                "address": "TXyz...",
                "risk": "Severe|High|Medium|Low",
                "riskReason": "...",
                "cluster": {"name": "Garantex", "category": "sanctions"},
                "addressIdentifications": [...],
                "exposures": [{"category": "darknet", "value": 0.15}],
                "triggers": [...]
            }
        """
        code, data = self._get(f"/api/risk/v2/entities/{address}")
        if code != 200:
            logger.warning(f"Address risk [{code}] for {address[:16]}...")
        return data

    def screen_address(self, address: str) -> dict:
        """
        Screening completo de una dirección: register + get risk.
        Un solo método que retorna cluster + risk + exposures.
        """
        # Registrar primero (idempotente si ya existe)
        self.register_address(address)

        # Consultar
        data = self.get_address_risk(address)

        if not isinstance(data, dict) or "_error" in str(data):
            return {
                "address": address,
                "risk": "unknown",
                "cluster": None,
                "exposures": [],
                "_raw": data,
            }

        return {
            "address": address,
            "risk": data.get("risk", "unknown"),
            "risk_reason": data.get("riskReason", ""),
            "cluster": data.get("cluster"),
            "identifications": data.get("addressIdentifications", []),
            "exposures": data.get("exposures", []),
            "triggers": data.get("triggers", []),
            "_raw": data,
        }

    # ── Batch: screening de múltiples wallets ──────────────

    def screen_addresses_batch(self, addresses: list,
                               delay: float = 0.3) -> dict:
        """
        Screening de múltiples wallets via Address Screening API.
        Primero registra todas, luego consulta todas.

        Args:
            addresses: Lista de direcciones a screenar
            delay: Segundos entre calls (rate limiting)

        Returns:
            {address: screen_result, ...}
        """
        # Fase 1: Registrar todas
        logger.info(f"Batch address screening: registering {len(addresses)} addresses")
        for addr in addresses:
            self.register_address(addr)
            time.sleep(delay)

        # Pequeña pausa para procesamiento
        time.sleep(2)

        # Fase 2: Consultar todas
        results = {}
        total = len(addresses)
        for i, addr in enumerate(addresses, 1):
            logger.info(f"Screening address {i}/{total}: {addr[:16]}...")
            results[addr] = self.screen_address(addr)
            if i < total:
                time.sleep(delay)

        return results

    # ── Método combinado: KYT withdrawal + Address Screening ──

    def deep_screen_wallet(self, address: str, client_name: str,
                           asset: str = "USDT_TRX") -> dict:
        """
        Screening profundo de una wallet usando AMBOS métodos:
        1. Address Screening API (cluster + risk + exposures)
        2. KYT Withdrawal Attempt (counterparty exposure + alerts)

        Combina ambos resultados para máxima cobertura.
        """
        # Address Screening (rápido, cluster directo)
        addr_result = self.screen_address(address)

        # KYT Withdrawal (más detallado, alerts)
        kyt_result = self.screen_wallet_via_withdrawal(client_name, address, asset)

        return {
            "address": address,
            "address_screening": addr_result,
            "kyt_withdrawal": kyt_result,
            # Resumen combinado
            "combined": {
                "risk": addr_result.get("risk", "unknown"),
                "cluster": addr_result.get("cluster"),
                "exposures": addr_result.get("exposures", []),
                "kyt_exposures": kyt_result.get("exposures"),
                "kyt_high_risk": kyt_result.get("high_risk"),
                "kyt_alerts": kyt_result.get("alerts", []),
                "has_alerts": len(kyt_result.get("alerts", [])) > 0,
            },
        }

    def deep_screen_wallets_batch(self, addresses: list,
                                   client_name: str,
                                   asset: str = "USDT_TRX",
                                   use_deep: bool = False,
                                   delay: float = 0.5) -> dict:
        """
        Screening batch de wallets.

        Args:
            addresses: Lista de direcciones
            client_name: Cliente para KYT
            asset: Asset code
            use_deep: True = Address Screening + KYT Withdrawal (más lento, más completo)
                      False = Solo Address Screening (rápido, suficiente para hop 2+)
            delay: Segundos entre calls
        """
        results = {}
        total = len(addresses)

        for i, addr in enumerate(addresses, 1):
            logger.info(f"Screening wallet {i}/{total}: {addr[:16]}...")

            if use_deep:
                results[addr] = self.deep_screen_wallet(addr, client_name, asset)
            else:
                results[addr] = self.screen_address(addr)

            if i < total:
                time.sleep(delay)

        return results
