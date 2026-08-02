#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Lance ce script avec sudo: sudo ./install.sh" >&2
    exit 1
fi

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

apt-get update
apt-get install -y python3-venv i2c-tools

if ! getent group i2c >/dev/null; then
    groupadd --system i2c
fi
if ! id sen66-ha >/dev/null 2>&1; then
    useradd --system --home-dir /opt/sen66-ha --shell /usr/sbin/nologin --groups i2c sen66-ha
fi

install -d -o root -g root -m 0755 /opt/sen66-ha
install -o root -g root -m 0755 "$REPO_DIR/sen66_mqtt.py" /opt/sen66-ha/sen66_mqtt.py
install -o root -g root -m 0644 "$REPO_DIR/requirements.txt" /opt/sen66-ha/requirements.txt

python3 -m venv /opt/sen66-ha/.venv
/opt/sen66-ha/.venv/bin/pip install --upgrade pip
/opt/sen66-ha/.venv/bin/pip install -r /opt/sen66-ha/requirements.txt

install -o root -g root -m 0644 "$REPO_DIR/systemd/sen66-ha.service" /etc/systemd/system/sen66-ha.service
if [ ! -e /etc/sen66-ha.env ]; then
    install -o root -g root -m 0600 "$REPO_DIR/config/sen66-ha.env.example" /etc/sen66-ha.env
    echo "Configuration créée dans /etc/sen66-ha.env"
fi

systemctl daemon-reload
echo "Édite /etc/sen66-ha.env, puis lance:"
echo "  sudo systemctl enable --now sen66-ha"
