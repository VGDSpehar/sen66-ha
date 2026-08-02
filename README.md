# SEN66 → Home Assistant

Ce service lit un capteur Sensirion SEN66 connecté en I²C à un Raspberry Pi, puis publie ses mesures vers Home Assistant avec MQTT Discovery.

Il crée automatiquement un appareil **Qualité de l’air — Bureau** avec :

- PM1, PM2,5, PM4 et PM10 en µg/m³ ;
- température et humidité relative ;
- indices COV et NOx ;
- CO₂ en ppm ;
- les cinq concentrations numériques de particules ;
- l’état matériel et les erreurs internes du capteur.

Les concentrations numériques et le détail des erreurs sont désactivés par défaut pour ne pas encombrer Home Assistant. Ils restent activables depuis la page de l’appareil.

## 1. Préparer Mosquitto

La configuration suivante est déjà suffisante :

```conf
listener 1883
allow_anonymous false
password_file /mosquitto/config/passwd
```

Crée un compte dédié depuis le répertoire contenant ton `compose.yaml` :

```bash
docker compose exec mosquitto mosquitto_passwd /mosquitto/config/passwd sen66
docker compose restart mosquitto
```

La commande demande le mot de passe sans l’afficher. N’utilise pas l’option `-c` : elle remplacerait le fichier de mots de passe existant.

Vérifie aussi que l’intégration **MQTT** est présente dans Home Assistant : **Paramètres → Appareils et services → MQTT**. Zigbee2MQTT peut utiliser le même broker avec son propre compte.

## 2. Préparer le Raspberry Pi 5

Branchement : SEN66 rouge → 3,3 V (pin 1), noir → GND (pin 6), vert → SDA (pin 3), jaune → SCL (pin 5).

Active I²C et vérifie que le capteur répond à l’adresse `0x6b` :

```bash
sudo raspi-config nonint do_i2c 0
sudo reboot
i2cdetect -y 1
```

## 3. Installer le bridge

Sur le Pi :

```bash
git clone https://github.com/VGDSpehar/sen66-ha.git
cd sen66-ha
sudo ./install.sh
sudo nano /etc/sen66-ha.env
```

Dans `/etc/sen66-ha.env`, renseigne :

- `MQTT_HOST` : l’adresse IP LAN de la machine Home Assistant/Mosquitto ;
- `MQTT_USERNAME=sen66` ;
- `MQTT_PASSWORD` : le mot de passe créé à l’étape 1.

Puis démarre le service :

```bash
sudo systemctl enable --now sen66-ha
journalctl -u sen66-ha -f
```

Tu dois voir une connexion MQTT suivie de relevés toutes les 30 secondes. L’appareil apparaît ensuite automatiquement dans **Paramètres → Appareils et services → MQTT**.

## 4. Ajouter le tableau de bord

Le fichier [`dashboard/home-assistant-dashboard.yaml`](dashboard/home-assistant-dashboard.yaml) contient une vue Lovelace prête à importer en mode YAML. Avec `DEVICE_SLUG=sen66_bureau`, les identifiants d’entités sont stables et le tableau fonctionne sans modification.

Si ton tableau de bord est géré depuis l’interface, ajoute les mêmes entités dans quatre cartes **Graphique d’historique** : CO₂, particules, gaz, puis confort.

## Diagnostic

```bash
# Présence du SEN66
i2cdetect -y 1

# État du service
systemctl status sen66-ha
journalctl -u sen66-ha -n 100 --no-pager

# Test du compte MQTT depuis l'hôte Mosquitto
docker compose exec mosquitto mosquitto_sub \
  -h 127.0.0.1 -u sen66 -P 'MOT_DE_PASSE' -t 'sen66/bureau/#' -v
```

Le mot de passe n’est jamais stocké dans le dépôt. `/etc/sen66-ha.env` est lisible uniquement par `root`.

## Références

- [Pilote Python officiel SEN66](https://github.com/Sensirion/python-i2c-sen66)
- [Exemple Sensirion pour Linux I²C](https://sensirion.github.io/python-i2c-sen66/execute-measurements.html#execute-measurements-using-internal-linux-i2c-driver)
- [MQTT Discovery de Home Assistant](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery)
