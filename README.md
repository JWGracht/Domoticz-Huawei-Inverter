# Domoticz-Huawei-Inverter
Domoticz plugin for Huawei Solar inverters via Modbus

## Prerequisites
- Modbus connection enabled at the inverter
- ([huwei_solar.py](https://github.com/wlcrs/huawei-solar-lib))

## Installation
```bash
cd ~/domoticz/plugins
git clone https://github.com/JWGracht/Domoticz-Huawei-Inverter.git
sudo pip3 install -U huawei-solar
sudo systemctl restart domoticz
```

## Update
```bash
cd ~/domoticz/plugins/Domoticz-Huawei-Inverter.git
git pull
sudo systemctl restart domoticz
```
