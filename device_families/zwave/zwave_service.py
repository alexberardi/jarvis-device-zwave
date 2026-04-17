"""Z-Wave JS UI service — singleton client for device data and control.

Connects to Z-Wave JS UI's socket.io API to fetch node data and
send control commands. Cache is refreshed by the ZWaveAgent on a timer.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

try:
    from jarvis_log_client import JarvisLogger
except ImportError:
    import logging

    class JarvisLogger:
        def __init__(self, **kw: Any) -> None:
            self._log = logging.getLogger(kw.get("service", __name__))

        def info(self, msg: str, **kw: Any) -> None:
            self._log.info(msg)

        def warning(self, msg: str, **kw: Any) -> None:
            self._log.warning(msg)

        def error(self, msg: str, **kw: Any) -> None:
            self._log.error(msg)

        def debug(self, msg: str, **kw: Any) -> None:
            self._log.debug(msg)


try:
    from services.secret_service import get_secret_value
except ImportError:
    import os

    def get_secret_value(key: str, scope: str = "") -> str | None:  # noqa: E302
        return os.environ.get(key)


logger = JarvisLogger(service="device.zwave")

# Default cache staleness: 5 minutes
_DEFAULT_MAX_AGE_SECONDS: int = 300

# Socket.io timeout for operations
_SIO_TIMEOUT_SECONDS: int = 30


# ---------------------------------------------------------------------------
# Z-Wave node → Jarvis domain classification
# ---------------------------------------------------------------------------

# Generic device class label → Jarvis domain
_GENERIC_CLASS_TO_DOMAIN: dict[str, str] = {
    "Binary Switch": "switch",
    "Binary Power Switch": "switch",
    "Multilevel Switch": "light",
    "Multilevel Power Switch": "light",
    "Door Lock": "lock",
    "Entry Control": "lock",
    "Thermostat": "climate",
    "General Thermostat": "climate",
    "Setback Thermostat": "climate",
    "Window Covering": "cover",
    "Barrier Operator": "cover",
}

# Command class → domain fallback (when generic class isn't mapped)
_CC_TO_DOMAIN: dict[int, str] = {
    37: "switch",   # Binary Switch
    38: "light",    # Multilevel Switch
    51: "light",    # Color Switch
    98: "lock",     # Door Lock
    64: "climate",  # Thermostat Mode
    67: "climate",  # Thermostat Setpoint
    102: "cover",   # Barrier Operator
}

# Priority order for domain classification (first match wins)
_DOMAIN_PRIORITY: list[str] = ["lock", "climate", "cover", "light", "switch"]


def classify_node(node: dict[str, Any]) -> str | None:
    """Determine the Jarvis domain for a Z-Wave node.

    Returns None for nodes that shouldn't be exposed (controllers, sensors-only).
    """
    if node.get("isControllerNode"):
        return None

    # Try generic device class first
    device_class: dict[str, Any] = node.get("deviceClass", {})
    specific: str = device_class.get("specific", {}).get("label", "")
    generic: str = device_class.get("generic", {}).get("label", "")

    for label in (specific, generic):
        domain: str | None = _GENERIC_CLASS_TO_DOMAIN.get(label)
        if domain:
            return domain

    # Fall back to command classes present in values
    values: dict[str, Any] = node.get("values", {})
    found_domains: set[str] = set()
    if isinstance(values, dict):
        for val in values.values():
            cc: int | None = val.get("commandClass")
            if cc in _CC_TO_DOMAIN:
                found_domains.add(_CC_TO_DOMAIN[cc])

    for domain in _DOMAIN_PRIORITY:
        if domain in found_domains:
            return domain

    return None


class ZWaveService:
    """Singleton service for Z-Wave JS UI communication.

    Connects to Z-Wave JS UI's socket.io API for node discovery and control.
    Cache is populated by the ZWaveAgent on a 5-minute timer.

    Usage:
        service = ZWaveService()
        await service.fetch_nodes()
        nodes = service.get_all_nodes()
        await service.write_value(5, 37, 0, "targetValue", True)
    """

    _instance: Optional["ZWaveService"] = None

    def __new__(cls) -> "ZWaveService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized: bool = True

        self._url: str | None = get_secret_value("ZWAVE_JS_URL", "integration")

        # Node cache: node_id → raw node data from Z-Wave JS UI
        self._nodes: dict[int, dict[str, Any]] = {}
        self._last_refresh: datetime | None = None
        self._last_error: str | None = None

    async def refresh_if_stale(self, max_age_seconds: int = _DEFAULT_MAX_AGE_SECONDS) -> None:
        """Re-fetch Z-Wave data if cache is older than max_age_seconds."""
        if self._last_refresh is not None:
            age: float = (datetime.now(timezone.utc) - self._last_refresh).total_seconds()
            if age < max_age_seconds:
                return
        await self.fetch_nodes()

    async def fetch_nodes(self) -> None:
        """Connect to Z-Wave JS UI and fetch all nodes into cache."""
        if not self._url:
            self._last_error = "ZWAVE_JS_URL not configured"
            logger.warning("Z-Wave fetch skipped", reason=self._last_error)
            return

        try:
            import socketio
        except ImportError:
            self._last_error = "python-socketio not installed"
            logger.error("python-socketio[asyncio_client] required for Z-Wave")
            return

        sio: socketio.AsyncClient = socketio.AsyncClient()
        try:
            await asyncio.wait_for(
                sio.connect(self._url),
                timeout=_SIO_TIMEOUT_SECONDS,
            )

            response: Any = await asyncio.wait_for(
                sio.call("zwave", {"api": "getNodes", "args": []}),
                timeout=_SIO_TIMEOUT_SECONDS,
            )

            nodes_list: list[dict[str, Any]] = self._parse_response(response, "getNodes")

            self._nodes = {}
            for node in nodes_list:
                node_id: int | None = node.get("id")
                if node_id is not None:
                    self._nodes[node_id] = node

            self._last_refresh = datetime.now(timezone.utc)
            self._last_error = None
            logger.info("Z-Wave nodes refreshed", count=len(self._nodes))

        except asyncio.TimeoutError:
            self._last_error = "Connection timeout"
            logger.error("Z-Wave JS UI connection timeout", url=self._url)
        except ConnectionRefusedError:
            self._last_error = "Connection refused — is Z-Wave JS UI running?"
            logger.error("Z-Wave JS UI connection refused", url=self._url)
        except Exception as e:
            self._last_error = str(e)
            logger.error("Z-Wave fetch error", error=str(e))
        finally:
            if sio.connected:
                await sio.disconnect()

    async def write_value(
        self,
        node_id: int,
        command_class: int,
        endpoint: int,
        property_name: str,
        value: Any,
        property_key: int | str | None = None,
    ) -> bool:
        """Send a setValue command to Z-Wave JS UI.

        Args:
            node_id: Z-Wave node ID.
            command_class: Z-Wave command class number.
            endpoint: Endpoint index (usually 0).
            property_name: Value property name (e.g., "targetValue").
            value: The value to set.
            property_key: Optional property key (needed for thermostat setpoints).

        Returns:
            True on success, False on failure.
        """
        if not self._url:
            logger.error("ZWAVE_JS_URL not configured")
            return False

        try:
            import socketio
        except ImportError:
            logger.error("python-socketio not installed")
            return False

        value_id: dict[str, Any] = {
            "nodeId": node_id,
            "commandClass": command_class,
            "endpoint": endpoint,
            "property": property_name,
        }
        if property_key is not None:
            value_id["propertyKey"] = property_key

        sio: socketio.AsyncClient = socketio.AsyncClient()
        try:
            await asyncio.wait_for(
                sio.connect(self._url),
                timeout=_SIO_TIMEOUT_SECONDS,
            )

            response: Any = await asyncio.wait_for(
                sio.call("zwave", {"api": "writeValue", "args": [value_id, value]}),
                timeout=_SIO_TIMEOUT_SECONDS,
            )

            success: bool = isinstance(response, dict) and response.get("success", False)
            if success:
                logger.info(
                    "Z-Wave value written",
                    node_id=node_id, cc=command_class,
                    prop=property_name, value=value,
                )
            else:
                msg: str = ""
                if isinstance(response, dict):
                    msg = response.get("message", str(response))
                logger.error("Z-Wave writeValue failed", node_id=node_id, response=msg[:200])
            return success

        except Exception as e:
            logger.error("Z-Wave writeValue error", error=str(e), node_id=node_id)
            return False
        finally:
            if sio.connected:
                await sio.disconnect()

    # ------------------------------------------------------------------
    # Cache accessors
    # ------------------------------------------------------------------

    def get_node(self, node_id: int) -> dict[str, Any] | None:
        """Get cached node data by ID."""
        return self._nodes.get(node_id)

    def get_all_nodes(self) -> dict[int, dict[str, Any]]:
        """Get all cached nodes."""
        return self._nodes

    def get_context_data(self) -> dict[str, Any]:
        """Return cached Z-Wave data for voice request context."""
        devices: list[dict[str, Any]] = []
        for node_id, node in self._nodes.items():
            domain: str | None = classify_node(node)
            if domain is None:
                continue

            name: str = node.get("name") or node.get("productLabel") or f"Node {node_id}"
            location: str = node.get("loc", "")

            device_info: dict[str, Any] = {
                "entity_id": f"{domain}.zwave_node_{node_id}",
                "name": name,
                "domain": domain,
                "state": self._get_node_state_summary(node, domain),
            }
            if location:
                device_info["area"] = location

            devices.append(device_info)

        return {
            "devices": devices,
            "node_count": len(self._nodes),
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
            "last_error": self._last_error,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_response(response: Any, api_name: str) -> list[dict[str, Any]]:
        """Parse a Z-Wave JS UI socket.io response into a list of results."""
        if isinstance(response, dict):
            if response.get("success"):
                result: Any = response.get("result", [])
                return result if isinstance(result, list) else []
            msg: str = response.get("message", "unknown error")
            raise ValueError(f"{api_name} failed: {msg}")

        if isinstance(response, list):
            return response

        raise ValueError(f"{api_name}: unexpected response type {type(response).__name__}")

    @staticmethod
    def _get_node_state_summary(node: dict[str, Any], domain: str) -> str:
        """Extract a human-readable state from cached node values."""
        values: dict[str, Any] = node.get("values", {})
        if not isinstance(values, dict):
            return "unknown"

        if domain == "switch":
            for val in values.values():
                if val.get("commandClass") == 37 and val.get("property") == "currentValue":
                    return "on" if val.get("value") else "off"

        elif domain == "light":
            for val in values.values():
                if val.get("commandClass") == 38 and val.get("property") == "currentValue":
                    level: int = val.get("value", 0)
                    return "off" if level == 0 else f"on ({level}%)"

        elif domain == "lock":
            for val in values.values():
                if val.get("commandClass") == 98 and val.get("property") == "currentMode":
                    mode: Any = val.get("value")
                    if mode == 255:
                        return "locked"
                    if mode == 0:
                        return "unlocked"
                    return str(mode)

        elif domain == "climate":
            for val in values.values():
                if val.get("commandClass") == 67 and val.get("property") == "setpoint":
                    temp: Any = val.get("value")
                    if temp is not None:
                        return f"setpoint {temp}"

        elif domain == "cover":
            for val in values.values():
                if val.get("commandClass") == 38 and val.get("property") == "currentValue":
                    pos: int = val.get("value", 0)
                    if pos == 0:
                        return "closed"
                    if pos >= 99:
                        return "open"
                    return f"open ({pos}%)"

        status: str = node.get("status", "unknown")
        return "offline" if status == "dead" else "unknown"
