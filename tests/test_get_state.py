"""get_state takes the SDK's signature, get_state(ip, **kwargs), as both nodes call it."""

import asyncio

from jarvis_command_sdk import DiscoveredDevice

from device_families.zwave.protocol import ZWaveProtocol
from device_families.zwave.zwave_service import ZWaveService

DIMMER = {"values": [{"commandClass": 38, "property": "currentValue", "value": 99}]}


def _state(**kwargs):
    ZWaveService()._nodes = {2: DIMMER}
    return asyncio.run(ZWaveProtocol().get_state(**kwargs))


def test_reads_the_state_by_entity_id_as_the_node_calls_it():
    state = _state(ip="", entity_id="zw6hd_2", cloud_id="", mac_address="", model="")
    assert state == {"state": "on", "brightness": 99}


def test_still_reads_a_discovered_device():
    dev = DiscoveredDevice(entity_id="zw6hd_2", name="Dimmer", domain="light", manufacturer="zwave",
                           model="", protocol="zwave")
    assert _state(ip=dev) == {"state": "on", "brightness": 99}


def test_an_unknown_node_is_an_error_not_a_crash():
    assert "not in cache" in _state(ip="", entity_id="lamp_9")["error"]
    assert "Missing node_id" in _state(ip="", entity_id="")["error"]
