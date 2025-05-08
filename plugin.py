# Domoticz Huawei Inverter
#
# Domoticz plugin for Huawei Solar inverters via Modbus
#
# Author: JWGracht
#
# Prerequisites:
#    1. Modbus connection enabled at the inverter
#    2. python 3.x
#    3. pip3 install -U huawei-solar
#
"""
<plugin key="Domoticz-Huawei-Inverter" name="Huawei Solar inverter (modbus TCP/IP)" author="jwgracht" version="1.1.0" wikilink="" externallink="https://github.com/JWGracht/Domoticz-Huawei-Inverter/">
    <description>
        <p>Domoticz plugin for Huawei Solar inverters via Modbus.</p>
        <p>Required:
        <ul style="list-style-type:square">
            <li>Modbus connection enabled at the inverter</li>
            <li>python 3.x</li>
            <li>sudo pip3 install -U huawei-solar</li>
        </ul></p>
    </description>
    <params>
        <param field="Address" label="Your Huawei inverter IP Address" width="200px" required="true" default="192.168.200.1"/>
        <param field="Port" label="Port" width="40px" required="true" default="502"/>
    </params>
</plugin>
"""
import Domoticz
from asyncio import get_event_loop
from huawei_solar import HuaweiSolarBridge
from huawei_solar import register_names as rn

class HuaweiSolarPlugin:
    enabled = False
    inverterserveraddress = "127.0.0.1"
    inverterserverport = "502"
    bridge = None
    minuteCounter = 0
    accumulated_yield_energy = 0
    async_loop = None
    efficiency = 0
    device_status = None

    def __init__(self):
        self.async_loop = get_event_loop()

    def onStart(self):
        Domoticz.Log("onStart called")
        self.minuteCounter = 0
        self.inverterserveraddress = Parameters["Address"].strip()
        self.inverterserverport = Parameters["Port"].strip()
        self.bridge = self._connectInverter()

        voltage_devices = ["PV_01_VOLTAGE", "PV_02_VOLTAGE", "PV_03_VOLTAGE", "PV_04_VOLTAGE", "PHASE_A_VOLTAGE", "PHASE_B_VOLTAGE", "PHASE_C_VOLTAGE", "GRID_A_VOLTAGE", "GRID_B_VOLTAGE", "GRID_C_VOLTAGE"]
        self._createDevices(voltage_devices, 243, 8)

        current_devices = ["PV_01_CURRENT", "PV_02_CURRENT", "PV_03_CURRENT", "PV_04_CURRENT", "PHASE_A_CURRENT", "PHASE_B_CURRENT", "PHASE_C_CURRENT", "ACTIVE_GRID_A_CURRENT", "ACTIVE_GRID_B_CURRENT", "ACTIVE_GRID_C_CURRENT"]
        self._createDevices(current_devices, 243, 23)

        power_devices = ["INPUT_POWER", "ACTIVE_POWER_FAST", "REACTIVE_POWER", "ACTIVE_GRID_A_POWER", "ACTIVE_GRID_B_POWER", "ACTIVE_GRID_C_POWER"]
        self._createDevices(power_devices, 248, 1)
        
        percentage_devices = ["EFFICIENCY"]
        self._createDevices(percentage_devices, 243, 6)
        
        text_devices = ["DEVICE_STATUS"]
        self._createDevices(text_devices, 243, 19)

        # Create exported KWH meter
        if self._getDevice("Energy Meter") < 0:
            self._createDevice("Energy Meter", 243, 29, 4)

        Domoticz.Heartbeat(5)

    def onStop(self):
        Domoticz.Log("onStop called")
        self.async_loop.close()

    def onConnect(self, Connection, Status, Description):
        Domoticz.Log("onConnect called")

    def onMessage(self, Connection, Data):
        Domoticz.Log("onMessage called")

    def onCommand(self, DeviceID, Unit, Command, Level, Color):
        Domoticz.Log(f"onCommand called for Device {DeviceID} Unit {Unit}: Parameter '{Command}', Level: {Level}")

    def onNotification(self, Name, Subject, Text, Status, Priority, Sound, ImageFile):
        Domoticz.Log(f"Notification: {Name},{Subject},{Text},{Status},{Priority},{Sound},{ImageFile}")

    def onDisconnect(self, Connection):
        Domoticz.Log("onDisconnect called")

    def onHeartbeat(self):
        Domoticz.Log("onHeartbeat called")

        # Get 5 second data
        try:
            result = self.async_loop.run_until_complete(self.bridge.batch_update([
                rn.INPUT_POWER, rn.PHASE_A_VOLTAGE, rn.PHASE_B_VOLTAGE, rn.PHASE_C_VOLTAGE,
                rn.PHASE_A_CURRENT, rn.PHASE_B_CURRENT, rn.PHASE_C_CURRENT,
                rn.ACTIVE_POWER_FAST, rn.REACTIVE_POWER,
                rn.PV_01_VOLTAGE, rn.PV_01_CURRENT, rn.PV_02_VOLTAGE, rn.PV_02_CURRENT, rn.PV_03_VOLTAGE, rn.PV_03_CURRENT, rn.PV_04_VOLTAGE, rn.PV_04_CURRENT,
                rn.GRID_A_VOLTAGE, rn.GRID_B_VOLTAGE, rn.GRID_C_VOLTAGE,
                rn.ACTIVE_GRID_A_CURRENT, rn.ACTIVE_GRID_B_CURRENT, rn.ACTIVE_GRID_C_CURRENT,
                rn.ACTIVE_GRID_A_POWER, rn.ACTIVE_GRID_B_POWER, rn.ACTIVE_GRID_C_POWER
            ]))
            pv_01_voltage = result['pv_01_voltage'][0]
            pv_02_voltage = result['pv_02_voltage'][0]
            pv_03_voltage = result['pv_03_voltage'][0]
            pv_04_voltage = result['pv_04_voltage'][0]
            pv_01_current = result['pv_01_current'][0]
            pv_02_current = result['pv_02_current'][0]
            pv_03_current = result['pv_03_current'][0]
            pv_04_current = result['pv_04_current'][0]
            input_power = result['input_power'][0]
            phase_A_voltage = result['phase_A_voltage'][0]
            phase_B_voltage = result['phase_B_voltage'][0]
            phase_C_voltage = result['phase_C_voltage'][0]
            phase_A_current = result['phase_A_current'][0]
            phase_B_current = result['phase_B_current'][0]
            phase_C_current = result['phase_C_current'][0]
            active_power_fast = result['active_power_fast'][0]
            reactive_power = result['reactive_power'][0]
            grid_A_voltage = result['grid_A_voltage'][0]
            grid_B_voltage = result['grid_B_voltage'][0]
            grid_C_voltage = result['grid_C_voltage'][0]
            active_grid_A_current = result['active_grid_A_current'][0]
            active_grid_B_current = result['active_grid_B_current'][0]
            active_grid_C_current = result['active_grid_C_current'][0]
            active_grid_A_power = result['active_grid_A_power'][0]
            active_grid_B_power = result['active_grid_B_power'][0]
            active_grid_C_power = result['active_grid_C_power'][0]
            
            # Get 1 minute data
            if self.minuteCounter == 12:
                self.minuteCounter = 0
            if self.minuteCounter == 0:
                result = self.async_loop.run_until_complete(self.bridge.batch_update([rn.EFFICIENCY, rn.DEVICE_STATUS, rn.ACCUMULATED_YIELD_ENERGY]))
                self.efficiency = result['efficiency'][0]
                self.device_status = result['device_status'][0]
                self.accumulated_yield_energy = result['accumulated_yield_energy'][0] * 1000

            self.minuteCounter += 1
            
            #update Domoticz devices
            self._updateDevice("PV_01_VOLTAGE", str(pv_01_voltage))
            self._updateDevice("PV_02_VOLTAGE", str(pv_02_voltage))
            self._updateDevice("PV_03_VOLTAGE", str(pv_03_voltage))
            self._updateDevice("PV_04_VOLTAGE", str(pv_04_voltage))
            self._updateDevice("PV_01_CURRENT", str(pv_01_current))
            self._updateDevice("PV_02_CURRENT", str(pv_02_current))
            self._updateDevice("PV_03_CURRENT", str(pv_03_current))
            self._updateDevice("PV_04_CURRENT", str(pv_04_current))
            self._updateDevice("INPUT_POWER", str(input_power))
            self._updateDevice("PHASE_A_VOLTAGE", str(phase_A_voltage))
            self._updateDevice("PHASE_B_VOLTAGE", str(phase_B_voltage))
            self._updateDevice("PHASE_C_VOLTAGE", str(phase_C_voltage))
            self._updateDevice("PHASE_A_CURRENT", str(phase_A_current))
            self._updateDevice("PHASE_B_CURRENT", str(phase_B_current))
            self._updateDevice("PHASE_C_CURRENT", str(phase_C_current))
            self._updateDevice("ACTIVE_POWER_FAST", str(active_power_fast))
            self._updateDevice("REACTIVE_POWER", str(reactive_power))
            self._updateDevice("Energy Meter", str(active_power_fast)+";"+str(self.accumulated_yield_energy))
            self._updateDevice("GRID_A_VOLTAGE", str(grid_A_voltage))
            self._updateDevice("GRID_B_VOLTAGE", str(grid_B_voltage))
            self._updateDevice("GRID_C_VOLTAGE", str(grid_C_voltage))
            self._updateDevice("ACTIVE_GRID_A_CURRENT", str(active_grid_A_current))
            self._updateDevice("ACTIVE_GRID_B_CURRENT", str(active_grid_B_current))
            self._updateDevice("ACTIVE_GRID_C_CURRENT", str(active_grid_C_current))
            self._updateDevice("ACTIVE_GRID_A_POWER", str(active_grid_A_power))
            self._updateDevice("ACTIVE_GRID_B_POWER", str(active_grid_B_power))
            self._updateDevice("ACTIVE_GRID_C_POWER", str(active_grid_C_power))
            self._updateDevice("EFFICIENCY", str(self.efficiency))
            self._updateDevice("DEVICE_STATUS", str(self.device_status))

        except (TimeoutError, Exception) as e:
            Domoticz.Error(f"Error in onHeartbeat: {str(e)}")
            Domoticz.Log("Trying to reconnect to inverter...")
            self._reconnectInverter()

    def _connectInverter(self):
        Domoticz.Log("Connecting inverter")
        try:
            bridge = self.async_loop.run_until_complete(HuaweiSolarBridge.create(
                host=self.inverterserveraddress, port=int(self.inverterserverport), slave_id=1))
            Domoticz.Log("Inverter connected")
            return bridge
        except Exception as e:
            Domoticz.Error(f"Failed to connect to inverter: {str(e)}")
            return None

    def _reconnectInverter(self):
        try:
            self.bridge = self._connectInverter()
            if self.bridge:
                Domoticz.Log("Reconnected successfully.")
            else:
                Domoticz.Error("Reconnection failed.")
        except Exception as e:
            Domoticz.Error(f"Reconnection attempt failed with error: {e}")

    def _getDevice(self, name):
        for Device in Devices:
            if Devices[Device].DeviceID.strip() == name:
                return Device
        return -1

    def _createDevices(self, device_list, device_type, subtype):
        for device in device_list:
            if self._getDevice(device) < 0:
                self._createDevice(device, device_type, subtype)

    def _createDevice(self, name, device_type, subtype, switchtype=0):
        iUnit = 0
        for x in range(1, 256):
            if x not in Devices:
                iUnit = x
                break
        if iUnit == 0:
            iUnit = len(Devices) + 1
        Domoticz.Device(Name=name, Unit=iUnit, Type=device_type, Subtype=subtype, Used=0, DeviceID=name, Switchtype=switchtype).Create()

    def _updateDevice(self, name, value):
        try:
            device = Devices[self._getDevice(name)]
            if device and device.sValue != value:
                device.Update(nValue=0, sValue=value)
        except Exception as e:
            Domoticz.Error(f"Failed to update device: {str(e)}")

global _plugin
_plugin = HuaweiSolarPlugin()

def onStart():
    global _plugin
    _plugin.onStart()

def onStop():
    global _plugin
    _plugin.onStop()

def onConnect(Connection, Status, Description):
    global _plugin
    _plugin.onConnect(Connection, Status, Description)

def onMessage(Connection, Data):
    global _plugin
    _plugin.onMessage(Connection, Data)

def onCommand(DeviceID, Unit, Command, Level, Color):
    global _plugin
    _plugin.onCommand(DeviceID, Unit, Command, Level, Color)

def onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile):
    global _plugin
    _plugin.onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile)

def onDisconnect(Connection):
    global _plugin
    _plugin.onDisconnect(Connection)

def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()
