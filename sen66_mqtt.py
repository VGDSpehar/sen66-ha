#!/usr/bin/env python3
"""Publish every useful SEN66 measurement to Home Assistant over MQTT."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import signal
import time
from dataclasses import dataclass
from typing import Any

import paho.mqtt.client as mqtt
from sensirion_driver_adapters.i2c_adapter.i2c_channel import I2cChannel
from sensirion_i2c_driver import CrcCalculator, I2cConnection, LinuxI2cTransceiver
from sensirion_i2c_sen66.device import Sen66Device


LOG = logging.getLogger("sen66-ha")
STOP = False


@dataclass(frozen=True)
class SensorDescription:
    key: str
    name: str
    unit: str | None = None
    device_class: str | None = None
    icon: str | None = None
    precision: int | None = None
    enabled: bool = True


MEASUREMENTS = (
    SensorDescription("pm1", "PM1", "µg/m³", "particulate_matter", precision=1),
    SensorDescription("pm2_5", "PM2,5", "µg/m³", "particulate_matter", precision=1),
    SensorDescription("pm4", "PM4", "µg/m³", "particulate_matter", precision=1),
    SensorDescription("pm10", "PM10", "µg/m³", "particulate_matter", precision=1),
    SensorDescription("humidity", "Humidité", "%", "humidity", precision=1),
    SensorDescription("temperature", "Température", "°C", "temperature", precision=1),
    SensorDescription("voc_index", "Indice COV", icon="mdi:chemical-weapon", precision=1),
    SensorDescription("nox_index", "Indice NOx", icon="mdi:molecule", precision=1),
    SensorDescription("co2", "CO₂", "ppm", "carbon_dioxide", precision=0),
    SensorDescription("number_pm0_5", "Nombre PM0,5", "particles/cm³", icon="mdi:dots-hexagon", precision=1, enabled=False),
    SensorDescription("number_pm1", "Nombre PM1", "particles/cm³", icon="mdi:dots-hexagon", precision=1, enabled=False),
    SensorDescription("number_pm2_5", "Nombre PM2,5", "particles/cm³", icon="mdi:dots-hexagon", precision=1, enabled=False),
    SensorDescription("number_pm4", "Nombre PM4", "particles/cm³", icon="mdi:dots-hexagon", precision=1, enabled=False),
    SensorDescription("number_pm10", "Nombre PM10", "particles/cm³", icon="mdi:dots-hexagon", precision=1, enabled=False),
)

STATUS_FLAGS = {
    "fan_error": (4, "Erreur ventilateur"),
    "rht_error": (6, "Erreur température/humidité"),
    "gas_error": (7, "Erreur capteur de gaz"),
    "pm_error": (11, "Erreur capteur de particules"),
    "co2_error_1": (12, "Erreur CO₂ 1"),
    "co2_error_2": (9, "Erreur CO₂ 2"),
    "fan_speed_warning": (21, "Avertissement vitesse ventilateur"),
}


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"La variable {name} est obligatoire")
    return value


def slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")
    if not cleaned:
        raise RuntimeError("DEVICE_SLUG ne contient aucun caractère utilisable")
    return cleaned


def finite_number(value: Any, *, integer: bool = False) -> float | int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(round(number)) if integer else round(number, 3)


def handle_signal(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


class Sen66MqttBridge:
    def __init__(self, sensor: Sen66Device, serial_number: str) -> None:
        self.sensor = sensor
        self.serial_number = serial_number.strip() or "unknown"
        self.device_slug = slug(os.getenv("DEVICE_SLUG", "sen66_bureau"))
        self.device_name = os.getenv("DEVICE_NAME", "Qualité de l’air — Bureau").strip()
        self.topic_prefix = os.getenv("MQTT_TOPIC_PREFIX", "sen66/bureau").strip().strip("/")
        self.discovery_prefix = os.getenv("MQTT_DISCOVERY_PREFIX", "homeassistant").strip().strip("/")
        self.state_topic = f"{self.topic_prefix}/state"
        self.availability_topic = f"{self.topic_prefix}/availability"
        self.device_id = f"sen66_{slug(self.serial_number)}"
        self.interval = max(5.0, float(os.getenv("PUBLISH_INTERVAL", "30")))
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"sen66-ha-{slug(self.serial_number)[-12:]}",
            protocol=mqtt.MQTTv311,
        )
        self.client.username_pw_set(required_env("MQTT_USERNAME"), required_env("MQTT_PASSWORD"))
        self.client.will_set(self.availability_topic, "offline", qos=1, retain=True)
        self.client.on_connect = self._on_connect

    @property
    def device(self) -> dict[str, Any]:
        return {
            "identifiers": [self.device_id],
            "name": self.device_name,
            "manufacturer": "Sensirion",
            "model": "SEN66",
            "serial_number": self.serial_number,
        }

    def _publish_discovery(self) -> None:
        common = {
            "state_topic": self.state_topic,
            "availability_topic": self.availability_topic,
            "device": self.device,
            "origin": {"name": "sen66-ha", "sw_version": "1.0.0", "support_url": "https://github.com/VGDSpehar/sen66-ha"},
        }
        for desc in MEASUREMENTS:
            config = {
                **common,
                "name": desc.name,
                "unique_id": f"{self.device_id}_{desc.key}",
                "object_id": f"{self.device_slug}_{desc.key}",
                "value_template": "{{ value_json.%s }}" % desc.key,
                "state_class": "measurement",
                "enabled_by_default": desc.enabled,
            }
            if desc.unit:
                config["unit_of_measurement"] = desc.unit
            if desc.device_class:
                config["device_class"] = desc.device_class
            if desc.icon:
                config["icon"] = desc.icon
            if desc.precision is not None:
                config["suggested_display_precision"] = desc.precision
            topic = f"{self.discovery_prefix}/sensor/{self.device_id}/{desc.key}/config"
            self.client.publish(topic, json.dumps(config, ensure_ascii=False), qos=1, retain=True)

        raw_status = {
            **common,
            "name": "État brut",
            "unique_id": f"{self.device_id}_status",
            "object_id": f"{self.device_slug}_status",
            "value_template": "{{ value_json.device_status }}",
            "icon": "mdi:chip",
            "entity_category": "diagnostic",
            "enabled_by_default": False,
        }
        self.client.publish(
            f"{self.discovery_prefix}/sensor/{self.device_id}/status/config",
            json.dumps(raw_status, ensure_ascii=False), qos=1, retain=True,
        )

        problem = {
            **common,
            "name": "Problème matériel",
            "unique_id": f"{self.device_id}_problem",
            "object_id": f"{self.device_slug}_problem",
            "value_template": "{{ value_json.device_problem }}",
            "payload_on": 1,
            "payload_off": 0,
            "device_class": "problem",
            "entity_category": "diagnostic",
        }
        self.client.publish(
            f"{self.discovery_prefix}/binary_sensor/{self.device_id}/problem/config",
            json.dumps(problem, ensure_ascii=False), qos=1, retain=True,
        )
        for key, (_bit, name) in STATUS_FLAGS.items():
            config = {
                **common,
                "name": name,
                "unique_id": f"{self.device_id}_{key}",
                "object_id": f"{self.device_slug}_{key}",
                "value_template": "{{ value_json.%s }}" % key,
                "payload_on": 1,
                "payload_off": 0,
                "device_class": "problem",
                "entity_category": "diagnostic",
                "enabled_by_default": False,
            }
            topic = f"{self.discovery_prefix}/binary_sensor/{self.device_id}/{key}/config"
            self.client.publish(topic, json.dumps(config, ensure_ascii=False), qos=1, retain=True)

    def _on_connect(self, client: mqtt.Client, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any) -> None:
        if reason_code != 0:
            LOG.error("Connexion MQTT refusée : %s", reason_code)
            return
        LOG.info("Connecté au broker MQTT")
        self._publish_discovery()
        client.publish(self.availability_topic, "online", qos=1, retain=True)

    def connect(self) -> None:
        host = required_env("MQTT_HOST")
        port = int(os.getenv("MQTT_PORT", "1883"))
        LOG.info("Connexion MQTT à %s:%d", host, port)
        self.client.connect(host, port, keepalive=60)
        self.client.loop_start()
        deadline = time.monotonic() + 15
        while not self.client.is_connected() and time.monotonic() < deadline:
            time.sleep(0.1)
        if not self.client.is_connected():
            raise RuntimeError("Connexion MQTT impossible après 15 secondes")

    def read(self) -> dict[str, Any]:
        values = self.sensor.read_measured_values()
        number_values = self.sensor.read_number_concentration_values()
        status = int(self.sensor.read_device_status())
        data = {
            "pm1": finite_number(values[0]),
            "pm2_5": finite_number(values[1]),
            "pm4": finite_number(values[2]),
            "pm10": finite_number(values[3]),
            "humidity": finite_number(values[4]),
            "temperature": finite_number(values[5]),
            "voc_index": finite_number(values[6]),
            "nox_index": finite_number(values[7]),
            "co2": finite_number(values[8], integer=True),
            "number_pm0_5": finite_number(number_values[0]),
            "number_pm1": finite_number(number_values[1]),
            "number_pm2_5": finite_number(number_values[2]),
            "number_pm4": finite_number(number_values[3]),
            "number_pm10": finite_number(number_values[4]),
            "device_status": status,
        }
        for key, (bit, _name) in STATUS_FLAGS.items():
            data[key] = int(bool(status & (1 << bit)))
        data["device_problem"] = int(any(data[key] for key in STATUS_FLAGS))
        return data

    def run(self) -> None:
        self.connect()
        consecutive_errors = 0
        try:
            while not STOP:
                started = time.monotonic()
                try:
                    payload = self.read()
                    info = self.client.publish(
                        self.state_topic,
                        json.dumps(payload, separators=(",", ":")),
                        qos=1,
                        retain=False,
                    )
                    if info.rc != mqtt.MQTT_ERR_SUCCESS:
                        raise RuntimeError(f"Échec de publication MQTT : {info.rc}")
                    consecutive_errors = 0
                    LOG.info("Mesure publiée : CO₂=%s ppm, PM2,5=%s µg/m³", payload["co2"], payload["pm2_5"])
                except Exception:
                    consecutive_errors += 1
                    LOG.exception("Lecture ou publication échouée (%d/3)", consecutive_errors)
                    if consecutive_errors >= 3:
                        raise
                delay = max(0.2, self.interval - (time.monotonic() - started))
                deadline = time.monotonic() + delay
                while not STOP and time.monotonic() < deadline:
                    time.sleep(min(0.5, deadline - time.monotonic()))
        finally:
            self.client.publish(self.availability_topic, "offline", qos=1, retain=True).wait_for_publish(timeout=3)
            self.client.disconnect()
            self.client.loop_stop()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    i2c_port = os.getenv("I2C_PORT", "/dev/i2c-1")
    with LinuxI2cTransceiver(i2c_port) as transceiver:
        channel = I2cChannel(
            I2cConnection(transceiver),
            slave_address=0x6B,
            crc=CrcCalculator(8, 0x31, 0xFF, 0x0),
        )
        sensor = Sen66Device(channel)
        try:
            sensor.stop_measurement()
            time.sleep(1.0)
        except Exception:
            LOG.debug("Le capteur était déjà à l'arrêt", exc_info=True)
        serial_number = str(sensor.get_serial_number())
        LOG.info("SEN66 détecté, numéro de série %s", serial_number)
        sensor.start_continuous_measurement()
        time.sleep(2.0)
        try:
            Sen66MqttBridge(sensor, serial_number).run()
        finally:
            try:
                sensor.stop_measurement()
            except Exception:
                LOG.exception("Impossible d'arrêter proprement la mesure")


if __name__ == "__main__":
    main()
