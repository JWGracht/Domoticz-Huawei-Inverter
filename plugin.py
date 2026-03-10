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
from asyncio import new_event_loop, AbstractEventLoop
from typing import Optional, Dict, List, Tuple, Any, Set
import time

try:
    from huawei_solar import HuaweiSolarBridge, create_tcp_bridge
    from huawei_solar import register_names as rn
except ImportError as e:
    Domoticz.Error(f"Failed to import huawei_solar: {e}")
    Domoticz.Error(f"Please install with: pip3 install -U huawei-solar")


# Device configuration mapping
DEVICE_CONFIGS: Dict[str, Tuple[List[str], int, int]] = {
    "voltage": (
        ["PV_01_VOLTAGE", "PV_02_VOLTAGE", "PV_03_VOLTAGE", "PV_04_VOLTAGE",
         "PHASE_A_VOLTAGE", "PHASE_B_VOLTAGE", "PHASE_C_VOLTAGE",
         "GRID_A_VOLTAGE", "GRID_B_VOLTAGE", "GRID_C_VOLTAGE"],
        243, 8
    ),
    "current": (
        ["PV_01_CURRENT", "PV_02_CURRENT", "PV_03_CURRENT", "PV_04_CURRENT",
         "PHASE_A_CURRENT", "PHASE_B_CURRENT", "PHASE_C_CURRENT",
         "ACTIVE_GRID_A_CURRENT", "ACTIVE_GRID_B_CURRENT", "ACTIVE_GRID_C_CURRENT"],
        243, 23
    ),
    "power": (
        ["INPUT_POWER", "ACTIVE_POWER_FAST", "REACTIVE_POWER",
         "ACTIVE_GRID_A_POWER", "ACTIVE_GRID_B_POWER", "ACTIVE_GRID_C_POWER"],
        248, 1
    ),
    "percentage": (["EFFICIENCY"], 243, 6),
    "text": (["DEVICE_STATUS"], 243, 19),
    "temperature": (
        ["INTERNAL_TEMPERATURE", "ANTI_REVERSE_MODULE_1_TEMP", "INV_MODULE_A_TEMP",
         "INV_MODULE_B_TEMP", "INV_MODULE_C_TEMP"],
        80, 5
    ),
    
}

# Fast update registers (every heartbeat)
FAST_UPDATE_REGISTERS = [
    rn.INPUT_POWER, rn.PHASE_A_VOLTAGE, rn.PHASE_B_VOLTAGE, rn.PHASE_C_VOLTAGE,
    rn.PHASE_A_CURRENT, rn.PHASE_B_CURRENT, rn.PHASE_C_CURRENT,
    rn.ACTIVE_POWER_FAST, rn.REACTIVE_POWER,
    rn.PV_01_VOLTAGE, rn.PV_01_CURRENT, rn.PV_02_VOLTAGE, rn.PV_02_CURRENT,
    rn.PV_03_VOLTAGE, rn.PV_03_CURRENT, rn.PV_04_VOLTAGE, rn.PV_04_CURRENT,
    rn.GRID_A_VOLTAGE, rn.GRID_B_VOLTAGE, rn.GRID_C_VOLTAGE,
    rn.ACTIVE_GRID_A_CURRENT, rn.ACTIVE_GRID_B_CURRENT, rn.ACTIVE_GRID_C_CURRENT,
    rn.ACTIVE_GRID_A_POWER, rn.ACTIVE_GRID_B_POWER, rn.ACTIVE_GRID_C_POWER
]

# Slow update registers (every 60 seconds = 12 heartbeats)
SLOW_UPDATE_REGISTERS = [
    rn.EFFICIENCY, rn.DEVICE_STATUS, rn.ACCUMULATED_YIELD_ENERGY, rn.INTERNAL_TEMPERATURE,
    rn.ANTI_REVERSE_MODULE_1_TEMP, rn.INV_MODULE_A_TEMP, rn.INV_MODULE_B_TEMP, rn.INV_MODULE_C_TEMP
]

# Mapping between register names and device names
REGISTER_TO_DEVICE: Dict[str, str] = {
    'pv_01_voltage': 'PV_01_VOLTAGE',
    'pv_02_voltage': 'PV_02_VOLTAGE',
    'pv_03_voltage': 'PV_03_VOLTAGE',
    'pv_04_voltage': 'PV_04_VOLTAGE',
    'pv_01_current': 'PV_01_CURRENT',
    'pv_02_current': 'PV_02_CURRENT',
    'pv_03_current': 'PV_03_CURRENT',
    'pv_04_current': 'PV_04_CURRENT',
    'input_power': 'INPUT_POWER',
    'phase_A_voltage': 'PHASE_A_VOLTAGE',
    'phase_B_voltage': 'PHASE_B_VOLTAGE',
    'phase_C_voltage': 'PHASE_C_VOLTAGE',
    'phase_A_current': 'PHASE_A_CURRENT',
    'phase_B_current': 'PHASE_B_CURRENT',
    'phase_C_current': 'PHASE_C_CURRENT',
    'active_power_fast': 'ACTIVE_POWER_FAST',
    'reactive_power': 'REACTIVE_POWER',
    'grid_A_voltage': 'GRID_A_VOLTAGE',
    'grid_B_voltage': 'GRID_B_VOLTAGE',
    'grid_C_voltage': 'GRID_C_VOLTAGE',
    'active_grid_A_current': 'ACTIVE_GRID_A_CURRENT',
    'active_grid_B_current': 'ACTIVE_GRID_B_CURRENT',
    'active_grid_C_current': 'ACTIVE_GRID_C_CURRENT',
    'active_grid_A_power': 'ACTIVE_GRID_A_POWER',
    'active_grid_B_power': 'ACTIVE_GRID_B_POWER',
    'active_grid_C_power': 'ACTIVE_GRID_C_POWER',
    'efficiency': 'EFFICIENCY',
    'device_status': 'DEVICE_STATUS',
    'accumulated_yield_energy': 'accumulated_yield_energy',
    "internal_temperature": "INTERNAL_TEMPERATURE",
    "anti_reverse_module_1_temp": "ANTI_REVERSE_MODULE_1_TEMP",
    "inv_module_a_temp": "INV_MODULE_A_TEMP",
    "inv_module_b_temp": "INV_MODULE_B_TEMP",
    "inv_module_c_temp": "INV_MODULE_C_TEMP",
}


class HuaweiSolarPlugin:
    """Domoticz plugin for Huawei Solar inverters via Modbus TCP/IP.
    """

    def __init__(self) -> None:
        self.enabled = False
        self.inverter_address: str = "127.0.0.1"
        self.inverter_port: int = 502
        self.bridge: Optional[HuaweiSolarBridge] = None
        self.minute_counter: int = 0
        self.accumulated_yield_energy: float = 0
        self.async_loop: AbstractEventLoop = new_event_loop()
        self.efficiency: float = 0
        self.device_status: str = ""
        
        # New: Value caching to avoid unnecessary updates
        self.cached_values: Dict[str, Any] = {}
        self.last_update_time: float = time.time()

    def onStart(self) -> None:
        """Initialize the plugin on startup."""
        Domoticz.Log("========================================")
        Domoticz.Log("Huawei Solar Inverter Plugin")
        Domoticz.Log("========================================")
        self.minute_counter = 0
        self.inverter_address = Parameters["Address"].strip()
        self.inverter_port = int(Parameters["Port"].strip())
        self.bridge = self._connect_inverter()

        # Create devices using configuration dictionary (cleaner than individual lists)
        for device_name, (devices, device_type, subtype) in DEVICE_CONFIGS.items():
            self._create_devices(devices, device_type, subtype)

        # Create exported KWH meter
        if self._get_device("Energy Meter") < 0:
            self._create_device("Energy Meter", 243, 29, 4)

        Domoticz.Heartbeat(5)

    def onStop(self) -> None:
        """Cleanup on shutdown."""
        Domoticz.Log("onStop called")
        if self.async_loop and not self.async_loop.is_closed():
            try:
                self.async_loop.close()
            except Exception as e:
                Domoticz.Warning(f"Error closing event loop: {e}")

    def onConnect(self, Connection, Status: int, Description: str) -> None:
        """Handle connection events."""
        Domoticz.Log(f"onConnect called: Status={Status}")

    def onMessage(self, Connection, Data) -> None:
        """Handle incoming messages."""
        Domoticz.Log("onMessage called")

    def onCommand(self, DeviceID: int, Unit: int, Command: str, Level: int, Color: str) -> None:
        """Handle device commands."""
        Domoticz.Log(f"onCommand: Device={DeviceID}, Unit={Unit}, Command='{Command}'")

    def onNotification(self, Name: str, Subject: str, Text: str, Status: str, Priority: int, Sound: str, ImageFile: str) -> None:
        """Handle notifications."""
        Domoticz.Log(f"Notification: {Name}")

    def onDisconnect(self, Connection) -> None:
        """Handle disconnection events."""
        Domoticz.Log("onDisconnect called")

    def onHeartbeat(self) -> None:
        """Main heartbeat handler - called every 5 seconds."""
        Domoticz.Debug("onHeartbeat called")

        try:
            if not self.bridge:
                Domoticz.Warning("Bridge not available, attempting reconnection...")
                self.bridge = self._connect_inverter()
                if not self.bridge:
                    return

            # Get 5 second data (fast update)
            try:
                result = self.async_loop.run_until_complete(
                    self.bridge.batch_update(FAST_UPDATE_REGISTERS)
                )
                self._process_results(result, register_type="fast")
            except Exception as e:
                Domoticz.Error(f"Error in fast update: {str(e)}")
                self._reconnect_inverter()
                return

            # Get 1 minute data (slow update) - every 12 heartbeats (60 seconds)
            if self.minute_counter == 12:
                self.minute_counter = 0
            
            if self.minute_counter == 0:
                try:
                    result = self.async_loop.run_until_complete(
                        self.bridge.batch_update(SLOW_UPDATE_REGISTERS)
                    )
                    self._process_results(result, register_type="slow")
                except Exception as e:
                    Domoticz.Warning(f"Error in slow update: {str(e)}")

            self.minute_counter += 1
            
        except Exception as e:
            Domoticz.Error(f"Unexpected error in onHeartbeat: {str(e)}")
            self._reconnect_inverter()

    def _process_results(self, result: Dict[str, Any], register_type: str = "fast") -> None:
        """Process and update devices with results from batch_update."""
        updated_count = 0
        
        for key, value in result.items():
            if key not in REGISTER_TO_DEVICE:
                continue
            
            device_name = REGISTER_TO_DEVICE[key]
            
            # Skip special handling for accumulated yield
            if key == 'accumulated_yield_energy':
                try:
                    self.accumulated_yield_energy = value[0] * 1000
                    Domoticz.Debug(f"Updated accumulated yield: {self.accumulated_yield_energy}")
                except Exception as e:
                    Domoticz.Debug(f"Error processing yield: {e}")
                continue
            
            try:
                device_value = str(value[0]) if isinstance(value, (list, tuple)) else str(value)
                
                # NEW: Cache check - only update if value changed
                cache_key = f"{register_type}_{key}"
                if cache_key not in self.cached_values or self.cached_values[cache_key] != device_value:
                    self._update_device(device_name, device_value)
                    self.cached_values[cache_key] = device_value
                    updated_count += 1
                
            except (ValueError, IndexError, TypeError) as e:
                Domoticz.Warning(f"Error processing register {key}: {str(e)}")

        # Update energy meter with combined values
        if 'active_power_fast' in result:
            try:
                active_power = result['active_power_fast'][0]
                energy_value = f"{active_power};{self.accumulated_yield_energy}"
                self._update_device("Energy Meter", energy_value)
                updated_count += 1
            except Exception as e:
                Domoticz.Warning(f"Error updating energy meter: {str(e)}")
        
        if updated_count > 0:
            Domoticz.Debug(f"Updated {updated_count} devices ({register_type})")

    def _connect_inverter(self) -> Optional[HuaweiSolarBridge]:
        """Establish connection to the inverter."""
        Domoticz.Log(f"Connecting to inverter at {self.inverter_address}:{self.inverter_port}")
        try:
            bridge = self.async_loop.run_until_complete(
                create_tcp_bridge(
                    host=self.inverter_address,
                    port=self.inverter_port,
                    slave_id=1
                )
            )
            Domoticz.Log("Inverter connected successfully")
            return bridge
        except Exception as e:
            Domoticz.Error(f"Failed to connect to inverter: {str(e)}")
            return None

    def _reconnect_inverter(self) -> None:
        """Attempt to reconnect to the inverter."""
        try:
            if self.bridge:
                try:
                    self.async_loop.run_until_complete(self.bridge.stop())
                except Exception:
                    pass
            
            self.bridge = self._connect_inverter()
            if self.bridge:
                Domoticz.Log("Reconnected successfully")
            else:
                Domoticz.Error("Reconnection failed")
        except Exception as e:
            Domoticz.Error(f"Reconnection attempt failed: {e}")

    def _get_device(self, name: str) -> int:
        """Find device by name. Returns device unit number or -1 if not found."""
        for device_unit, device in Devices.items():
            if device.DeviceID.strip() == name:
                return device_unit
        return -1

    def _create_devices(self, device_list: List[str], device_type: int, subtype: int) -> None:
        """Create multiple devices if they don't exist."""
        for device_name in device_list:
            if self._get_device(device_name) < 0:
                self._create_device(device_name, device_type, subtype)

    def _create_device(self, name: str, device_type: int, subtype: int, switchtype: int = 0) -> None:
        """Create a single device in Domoticz."""
        unit = self._find_available_unit()
        if unit == 0:
            Domoticz.Error(f"Failed to create device {name}: No available units")
            return
        
        try:
            Domoticz.Device(
                Name=name,
                Unit=unit,
                Type=device_type,
                Subtype=subtype,
                Used=0,
                DeviceID=name,
                Switchtype=switchtype
            ).Create()
            Domoticz.Debug(f"Device created: {name} (Unit {unit})")
        except Exception as e:
            Domoticz.Error(f"Failed to create device {name}: {str(e)}")

    def _find_available_unit(self) -> int:
        """Find the first available device unit number."""
        for unit in range(1, 256):
            if unit not in Devices:
                return unit
        return 0

    def _update_device(self, name: str, value: str) -> None:
        """Update a device's value if it has changed."""
        unit = self._get_device(name)
        if unit < 0:
            Domoticz.Debug(f"Device not found: {name}")
            return

        try:
            device = Devices[unit]
            if device.sValue != value:
                device.Update(nValue=0, sValue=value)
        except Exception as e:
            Domoticz.Error(f"Failed to update device {name}: {str(e)}")


# Global plugin instance
_plugin: Optional[HuaweiSolarPlugin] = None


def onStart() -> None:
    """Domoticz callback: plugin startup."""
    global _plugin
    _plugin = HuaweiSolarPlugin()
    _plugin.onStart()


def onStop() -> None:
    """Domoticz callback: plugin shutdown."""
    global _plugin
    if _plugin:
        _plugin.onStop()


def onConnect(Connection, Status: int, Description: str) -> None:
    """Domoticz callback: connection event."""
    global _plugin
    if _plugin:
        _plugin.onConnect(Connection, Status, Description)


def onMessage(Connection, Data) -> None:
    """Domoticz callback: incoming message."""
    global _plugin
    if _plugin:
        _plugin.onMessage(Connection, Data)


def onCommand(DeviceID: int, Unit: int, Command: str, Level: int, Color: str) -> None:
    """Domoticz callback: device command."""
    global _plugin
    if _plugin:
        _plugin.onCommand(DeviceID, Unit, Command, Level, Color)


def onNotification(Name: str, Subject: str, Text: str, Status: str, Priority: int, Sound: str, ImageFile: str) -> None:
    """Domoticz callback: notification."""
    global _plugin
    if _plugin:
        _plugin.onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile)


def onDisconnect(Connection) -> None:
    """Domoticz callback: disconnection event."""
    global _plugin
    if _plugin:
        _plugin.onDisconnect(Connection)


def onHeartbeat() -> None:
    """Domoticz callback: heartbeat."""
    global _plugin
    if _plugin:
        _plugin.onHeartbeat()