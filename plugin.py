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
<plugin key="Domoticz-Huawei-Inverter" name="Huawei Solar inverter (modbus TCP/IP)" author="jwgracht" version="2.0.0" wikilink="" externallink="https://github.com/JWGracht/Domoticz-Huawei-Inverter/">
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
        <param field="Mode1" label="Modbus Slave ID" width="40px" required="true" default="1">
            <description>Modbus slave address of the inverter (default: 1). Use 0 for single-inverter setups or 1-3 for multi-inverter daisy-chain configurations.</description>
            <options>
                <option label="0" value="0"/>
                <option label="1 (default)" value="1" default="true"/>
                <option label="2" value="2"/>
                <option label="3" value="3"/>
            </options>
        </param>
    </params>
</plugin>
"""

"""
IMPORTANT ARCHITECTURAL NOTES
====================================================

This plugin runs inside Domoticz's Python interpreter with CRITICAL constraints:

1. SINGLE INTERPRETER INSTANCE
   - Domoticz reuses the same Python interpreter across ALL plugins
   - This is logged as: "(Domoticz-Huawei-Inverter) reusing preserved interpreter (0x...)"
   - This means: ALL threads run under the same GIL (Global Interpreter Lock)
   - Consequence: True parallelism is NOT possible with threads

2. DO NOT USE THREADING FOR PARALLELISM
   WRONG: Creating separate threads for "parallel" fast/slow updates
      - Python threads under single interpreter = no true parallelism
      - Will cause event loop conflicts
      - Will cause interpreter state conflicts
      - Example of FAILURE:
        ```
        FastUpdateThread(daemon=True)  # DON'T DO THIS
        SlowUpdateThread(daemon=True)  # DON'T DO THIS
        ```

3. DO USE PERSISTENT EVENT LOOP
   CORRECT: Create event loop ONCE in __init__, reuse in all heartbeats
      - Single event loop per plugin instance
      - Reuse same loop for every batch_update() call
      - Example of SUCCESS:
        ```
        def __init__(self):
            self.async_loop = new_event_loop()  # Create ONCE
        
        def onHeartbeat(self):
            result = self.async_loop.run_until_complete(...)  # Reuse
        ```

4. DO NOT USE asyncio.run()
   WRONG: asyncio.run() in Domoticz context
      - asyncio.run() creates a NEW event loop each call
      - With Domoticz's preserved interpreter, this causes conflicts
      - Multiple event loops = RuntimeError
      - Example of FAILURE:
        ```
        result = asyncio.run(self.bridge.batch_update(...))  # DON'T DO THIS
        ```

5. HEARTBEAT IS YOUR TIMING MECHANISM
   - Domoticz calls onHeartbeat() at fixed intervals (e.g., every 30 seconds)
   - Fast updates run every heartbeat
   - Use heartbeat counter to schedule slow updates
   - NEVER spawn background threads
   - Example of SUCCESS:
        ```
        def onHeartbeat(self):
            self._update_fast_registers()  # Every heartbeat (30s)
            if self.heartbeat_counter % 10 == 0:
                self._update_slow_registers()  # Every 10 heartbeats (5 min)
            self.heartbeat_counter += 1
        ```

6. CLOSE EVENT LOOP PROPERLY IN onStop()
   - Domoticz may reload plugins without restarting
   - Must clean up event loop to avoid "EventLoop is closed" errors
   - Example:
        ```
        def onStop(self):
            if self.async_loop and not self.async_loop.is_closed():
                self.async_loop.close()
        ```

SUMMARY:
==============================
- ONE event loop per plugin, created in __init__
- REUSE that loop in onHeartbeat() with run_until_complete()
- Use heartbeat counter for timing slow updates
- Wrap fast/slow operations in try/except for robustness
- Close event loop in onStop()
- NO asyncio.run() - it conflicts with Domoticz interpreter
- NO background threads - they don't provide parallelism and cause conflicts
- NO multiple event loops - one per plugin only
- NO thread.Thread for concurrent updates - use heartbeat intervals instead
"""

import Domoticz
from asyncio import new_event_loop, AbstractEventLoop, wait_for, TimeoutError as AsyncTimeoutError
from typing import Optional, Dict, List, Tuple, Any, Set
import time

try:
    from huawei_solar import HuaweiSolarDevice, create_device_instance, create_tcp_client
    from huawei_solar import register_names as rn
except ImportError as e:
    Domoticz.Error(f"Failed to import huawei_solar: {e}")
    Domoticz.Error(f"Please install with: pip3 install -U huawei-solar")

# ============================================================================
# TIMEOUT AND RETRY CONFIGURATION
# ============================================================================
# Timeout for batch_update operations (seconds).
# The library itself has a 10s timeout per Modbus read with 3 retries.
# This overall timeout prevents run_until_complete() from blocking indefinitely.
BATCH_UPDATE_TIMEOUT = 25

# Timeout for connection/reconnection attempts (seconds)
CONNECTION_TIMEOUT = 15

# Number of consecutive failures before backing off
MAX_CONSECUTIVE_FAILURES = 3

# Backoff multiplier: skip N heartbeats after MAX_CONSECUTIVE_FAILURES
BACKOFF_HEARTBEATS = 6  # 6 * 30s = 3 minutes backoff


# ============================================================================
# CONFIGURATION DICTIONARIES
# ============================================================================
# These centralize all device and register configuration for maintainability

DEVICE_CONFIGS: Dict[str, Tuple[List[str], int, int]] = {
    """
    Device configuration mapping for Domoticz device creation.
    
    Format: "category_name": (device_names_list, device_type, subtype)
    
    Args:
        device_names_list: List of register names to create as devices
        device_type: Domoticz device type (243=Voltage, 248=Power, etc.)
        subtype: Domoticz device subtype for specific measurement units
    
    See: https://www.domoticz.com/wiki/Domoticz_API/JSON_URL%27s#Device_types_and_subtypes
    """
    "voltage": (
        ["PV_01_VOLTAGE", "PV_02_VOLTAGE", "PV_03_VOLTAGE", "PV_04_VOLTAGE",
         "PHASE_A_VOLTAGE", "PHASE_B_VOLTAGE", "PHASE_C_VOLTAGE",
         "GRID_A_VOLTAGE", "GRID_B_VOLTAGE", "GRID_C_VOLTAGE"],
        243, 8  # Type 243 Subtype 8 = Voltage
    ),
    "current": (
        ["PV_01_CURRENT", "PV_02_CURRENT", "PV_03_CURRENT", "PV_04_CURRENT",
         "PHASE_A_CURRENT", "PHASE_B_CURRENT", "PHASE_C_CURRENT",
         "ACTIVE_GRID_A_CURRENT", "ACTIVE_GRID_B_CURRENT", "ACTIVE_GRID_C_CURRENT"],
        243, 23  # Type 243 Subtype 23 = Current
    ),
    "power": (
        ["INPUT_POWER", "ACTIVE_POWER_FAST", "REACTIVE_POWER",
         "ACTIVE_GRID_A_POWER", "ACTIVE_GRID_B_POWER", "ACTIVE_GRID_C_POWER"],
        248, 1  # Type 248 Subtype 1 = Power
    ),
    "percentage": (["EFFICIENCY"], 243, 6),  # Type 243 Subtype 6 = Percentage
    "text": (["DEVICE_STATUS"], 243, 19),    # Type 243 Subtype 19 = Text
    "temperature": (
        ["INTERNAL_TEMPERATURE", "ANTI_REVERSE_MODULE_1_TEMP", "INV_MODULE_A_TEMP",
         "INV_MODULE_B_TEMP", "INV_MODULE_C_TEMP"],
        80, 5
    ),
}

# Fast update registers - fetched every heartbeat (30 seconds)
# These are rapidly changing values that need frequent updates
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

# Slow update registers - fetched every 5 minutes (10 heartbeats)
# These are slowly changing values that don't need frequent updates
SLOW_UPDATE_REGISTERS = [
    rn.EFFICIENCY, rn.DEVICE_STATUS, rn.ACCUMULATED_YIELD_ENERGY, rn.INTERNAL_TEMPERATURE,
    rn.ANTI_REVERSE_MODULE_1_TEMP, rn.INV_MODULE_A_TEMP, rn.INV_MODULE_B_TEMP, rn.INV_MODULE_C_TEMP
]

# Mapping between huawei-solar register names and Domoticz device names
# This enables automated device updates without hardcoding each register
REGISTER_TO_DEVICE: Dict[str, str] = {
    """
    Maps huawei_solar library register names (lowercase_with_underscores)
    to Domoticz device names (UPPERCASE_WITH_UNDERSCORES).
    
    This is used in _process_results() to automatically update devices
    based on API response without manual extraction of each register.
    
    Example:
        'pv_01_voltage' (from API) -> 'PV_01_VOLTAGE' (device name)
    """
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


# ============================================================================
# MAIN PLUGIN CLASS
# ============================================================================

class HuaweiSolarPlugin:
    """
    Domoticz plugin for Huawei Solar inverters via Modbus TCP/IP.
    
    This plugin fetches inverter data via the huawei-solar v3 library and updates
    Domoticz devices with current values. It uses a persistent event loop to
    handle async operations within Domoticz's single-interpreter architecture.
    
    CRITICAL DESIGN DECISIONS:
    ==========================
    
    1. Single Event Loop Pattern
       - Created ONCE in __init__ with new_event_loop()
       - Reused in every onHeartbeat() call via run_until_complete()
       - NOT using asyncio.run() which creates new loops
       - Closed properly in onStop() to prevent resource leaks
       
    2. Heartbeat-Based Timing
       - Domoticz calls onHeartbeat() every 30 seconds
       - Fast updates: every heartbeat (30s)
       - Slow updates: every 10 heartbeats (5 min)
       - This avoids need for background threads
       
    3. Value Caching
       - Caches last known values for each register
       - Only updates Domoticz devices when value changes
       - Reduces database writes and improves efficiency
       
    4. Error Handling & Recovery
       - Catches exceptions in both fast and slow updates
       - Attempts automatic reconnection on repeated failures
       - Logs all errors for debugging
    
    Attributes:
        inverter_address (str): IP address of Huawei inverter
        inverter_port (int): Modbus TCP port (default 502)
        slave_id (int): Modbus slave address of the inverter (0-3, default 1).
                        Use 0 for single-inverter setups or 1-3 for
                        multi-inverter daisy-chain configurations.
        bridge (Optional[HuaweiSolarDevice]): Device connection to inverter (v3 API).
                        Named 'bridge' for backward compatibility, but is a
                        HuaweiSolarDevice instance in v3.
        async_loop (AbstractEventLoop): PERSISTENT event loop for async operations
        heartbeat_counter (int): Heartbeat counter for slow update intervals
        accumulated_yield_energy (float): Total energy produced (Wh)
        cached_values (Dict): Caches last value of each register
        last_update_time (float): Timestamp of last update (for monitoring)
    """

    def __init__(self) -> None:
        """
        Initialize the plugin instance.
        
        CRITICAL: Creates the event loop ONCE here. This same loop is reused
        for all async operations throughout the plugin lifetime.
        
        Why not create it in onStart()? Because Domoticz may call __init__
        multiple times during plugin management, but we want ONE consistent
        event loop per plugin instance.
        """
        self.enabled: bool = False
        self.inverter_address: str = "127.0.0.1"
        self.inverter_port: int = 502
        self.slave_id: int = 1
        self.bridge: Optional[HuaweiSolarDevice] = None
        self.heartbeat_counter: int = 0
        self.accumulated_yield_energy: float = 0
        
        # CRITICAL: Create event loop ONCE - reuse in every onHeartbeat()
        # This is NOT asyncio.run() which creates new loops each call
        self.async_loop: AbstractEventLoop = new_event_loop()
        
        self.efficiency: float = 0
        self.device_status: str = ""
        
        # Value caching to avoid unnecessary Domoticz updates
        self.cached_values: Dict[str, Any] = {}
        self.last_update_time: float = time.time()
        
        # Failure tracking for exponential backoff
        self.consecutive_failures: int = 0
        self.skip_heartbeats: int = 0

    def onStart(self) -> None:
        """
        Domoticz callback: Initialize plugin on startup.
        
        Called by Domoticz framework when:
        - Plugin is first loaded
        - User clicks "Start" in web UI
        - Domoticz restarts
        
        Responsibilities:
        - Read configuration parameters (IP address, port, Modbus slave ID)
        - Create Domoticz devices from DEVICE_CONFIGS
        - Establish connection to inverter
        - Set heartbeat interval
        
        Note: Does NOT create event loop - that's done in __init__
        """
        Domoticz.Log("onStart called")
        self.heartbeat_counter = 0
        
        # Read parameters from Domoticz plugin configuration
        self.inverter_address = Parameters["Address"].strip()
        self.inverter_port = int(Parameters["Port"].strip())
        self.slave_id = int(Parameters["Mode1"].strip()) if Parameters.get("Mode1", "").strip() else 1
        
        Domoticz.Log(f"Configuration: address={self.inverter_address}, port={self.inverter_port}, slave_id={self.slave_id}")
        
        # Establish connection to inverter
        self.bridge = self._connect_inverter()

        # Create devices using configuration dictionary (cleaner than individual lists)
        # See DEVICE_CONFIGS at top of file
        for device_name, (devices, device_type, subtype) in DEVICE_CONFIGS.items():
            self._create_devices(devices, device_type, subtype)

        # Create exported KWH meter for energy tracking
        if self._get_device("Energy Meter") < 0:
            self._create_device("Energy Meter", 243, 29, 4)

        # Set heartbeat interval: Domoticz will call onHeartbeat() every N seconds
        Domoticz.Heartbeat(30)

    def onStop(self) -> None:
        """
        Domoticz callback: Cleanup on plugin shutdown.
        
        Called by Domoticz framework when:
        - User clicks "Stop" in web UI
        - Plugin is being unloaded
        - Domoticz is shutting down
        
        CRITICAL: Must close event loop to avoid:
        - "RuntimeError: Event loop is closed" on next plugin load
        - Resource leaks
        - Memory accumulation over time
        """
        Domoticz.Log("onStop called")
        
        # Clean up bridge connection first
        self._cleanup_bridge()
        
        # Close event loop - IMPORTANT for plugin reload scenarios
        if self.async_loop and not self.async_loop.is_closed():
            try:
                self.async_loop.close()
            except Exception as e:
                Domoticz.Log(f"Error closing event loop: {e}")

    def onConnect(self, Connection, Status: int, Description: str) -> None:
        """
        Domoticz callback: Handle connection events from Domoticz.
        
        This is called when a Domoticz connection object changes state.
        Note: This plugin uses direct HTTP calls, not Domoticz connections,
        so this is mostly a placeholder.
        
        Args:
            Connection: Domoticz connection object
            Status: Connection status code
            Description: Human-readable status description
        """
        Domoticz.Log(f"onConnect called: Status={Status}")

    def onMessage(self, Connection, Data) -> None:
        """
        Domoticz callback: Handle incoming messages on connections.
        
        Called when data is received on a Domoticz connection.
        Not used in this plugin.
        """
        Domoticz.Log("onMessage called")

    def onCommand(self, DeviceID: int, Unit: int, Command: str, Level: int, Color: str) -> None:
        """
        Domoticz callback: Handle commands sent to devices.
        
        Called when user interacts with a device in Domoticz.
        This inverter plugin is read-only, so we just log commands.
        
        Args:
            DeviceID: ID of device receiving command
            Unit: Unit number within the device
            Command: Command string (e.g., "On", "Off")
            Level: Command level/value
            Color: Color value for RGB devices
        """
        Domoticz.Log(f"onCommand: Device={DeviceID}, Unit={Unit}, Command='{Command}'")

    def onNotification(self, Name: str, Subject: str, Text: str, Status: str, Priority: int, Sound: str, ImageFile: str) -> None:
        """
        Domoticz callback: Handle notifications from other plugins/devices.
        
        Called when a notification is sent to this plugin.
        Not used in this plugin.
        """
        Domoticz.Log(f"Notification: {Name}")

    def onDisconnect(self, Connection) -> None:
        """
        Domoticz callback: Handle disconnection events.
        
        Called when a Domoticz connection is closed.
        Not used in this plugin.
        """
        Domoticz.Log("onDisconnect called")

    def onHeartbeat(self) -> None:
        """
        Domoticz callback: Main processing loop - called every 30 seconds.
        
        This is the CORE of the plugin. Called at regular intervals to:
        1. Fetch fast-changing registers (every heartbeat = 30s)
        2. Fetch slow-changing registers (every 10 heartbeats = 5 min)
        3. Update Domoticz devices with new values
        4. Handle errors with backoff to prevent blocking Domoticz
        
        CRITICAL DESIGN NOTES:
        - Uses asyncio.wait_for() to enforce timeouts on all async operations
        - Implements exponential backoff on consecutive failures
        - Does NOT reconnect in the error path (deferred to next heartbeat)
        """
        Domoticz.Debug("onHeartbeat called")

        # Backoff: skip heartbeats after repeated failures
        if self.skip_heartbeats > 0:
            self.skip_heartbeats -= 1
            Domoticz.Debug(f"Backing off, skipping heartbeat ({self.skip_heartbeats} remaining)")
            self.heartbeat_counter += 1
            return

        try:
            if not self.bridge:
                Domoticz.Log("Bridge not available, attempting reconnection...")
                self.bridge = self._connect_inverter()
                if not self.bridge:
                    self._handle_failure()
                    return

            # ================================================================
            # FAST UPDATE: Every heartbeat (30 seconds)
            # ================================================================
            try:
                # CRITICAL: wrap in wait_for() to prevent indefinite hangs
                result = self.async_loop.run_until_complete(
                    wait_for(
                        self.bridge.batch_update(FAST_UPDATE_REGISTERS),
                        timeout=BATCH_UPDATE_TIMEOUT
                    )
                )
                self._process_results(result, register_type="fast")
                # Success: reset failure counter
                self.consecutive_failures = 0
            except AsyncTimeoutError:
                Domoticz.Error(f"Fast update timed out after {BATCH_UPDATE_TIMEOUT}s")
                self._handle_failure()
                return
            except Exception as e:
                Domoticz.Error(f"Error in fast update: {str(e)}")
                self._handle_failure()
                return

            # ================================================================
            # SLOW UPDATE: Every 10 heartbeats (5 minutes)
            # ================================================================
            if self.heartbeat_counter % 10 == 0:
                try:
                    result = self.async_loop.run_until_complete(
                        wait_for(
                            self.bridge.batch_update(SLOW_UPDATE_REGISTERS),
                            timeout=BATCH_UPDATE_TIMEOUT
                        )
                    )
                    self._process_results(result, register_type="slow")
                except AsyncTimeoutError:
                    Domoticz.Log(f"Slow update timed out after {BATCH_UPDATE_TIMEOUT}s")
                except Exception as e:
                    Domoticz.Log(f"Error in slow update: {str(e)}")

            self.heartbeat_counter += 1
            
        except Exception as e:
            Domoticz.Error(f"Unexpected error in onHeartbeat: {str(e)}")
            self._handle_failure()

    def _handle_failure(self) -> None:
        """
        Handle a connection/update failure with exponential backoff.
        
        After MAX_CONSECUTIVE_FAILURES, starts skipping heartbeats to avoid
        hammering an unresponsive inverter (e.g. at night when it's off).
        Invalidates the bridge so next attempt will reconnect fresh.
        """
        self.consecutive_failures += 1
        
        if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self.skip_heartbeats = BACKOFF_HEARTBEATS
            Domoticz.Log(
                f"{self.consecutive_failures} consecutive failures. "
                f"Backing off for {self.skip_heartbeats * 30}s before retry."
            )
            # Invalidate bridge - will reconnect on next active heartbeat
            self._cleanup_bridge()
        else:
            # First few failures: just invalidate bridge for reconnect next beat
            self._cleanup_bridge()

    def _cleanup_bridge(self) -> None:
        """Safely clean up the bridge connection without blocking."""
        if self.bridge:
            try:
                self.async_loop.run_until_complete(
                    wait_for(self.bridge.stop(), timeout=5)
                )
            except Exception:
                pass  # Don't let cleanup errors propagate
        self.bridge = None

    def _process_results(self, result: Dict[str, Any], register_type: str = "fast") -> None:
        """
        Process inverter API results and update Domoticz devices.
        
        Uses REGISTER_TO_DEVICE mapping to automatically convert API register
        names to device names, enabling clean update logic without hardcoding.
        
        Implements value caching: only updates devices when values actually change.
        This reduces database writes and improves efficiency.
        
        Args:
            result (Dict[str, Any]): Dictionary of register_name -> Result(value, unit)
                                    returned by huawei_solar v3 batch_update().
                                    Result is a dataclass with .value and .unit attributes.
            register_type (str): Either "fast" or "slow" for cache key generation
        
        Algorithm:
        1. Iterate through all registers in result
        2. Skip registers not in REGISTER_TO_DEVICE mapping
        3. For accumulated yield: special handling (store for later)
        4. For others: check cache - only update if value changed
        5. Update energy meter with combined power and yield
        
        Example result dict (v3 format):
        {
            'pv_01_voltage': Result(value=100.5, unit='V'),
            'phase_A_current': Result(value=5.3, unit='A'),
            'active_power_fast': Result(value=2500, unit='W'),
            'accumulated_yield_energy': Result(value=123456, unit='kWh'),
        }
        """
        updated_count = 0
        
        for key, value in result.items():
            # Skip registers not in our mapping
            if key not in REGISTER_TO_DEVICE:
                continue
            
            device_name = REGISTER_TO_DEVICE[key]
            
            # Special handling for accumulated energy yield
            if key == 'accumulated_yield_energy':
                try:
                    # Convert kWh to Wh for consistency
                    self.accumulated_yield_energy = value.value * 1000
                    Domoticz.Debug(f"Updated accumulated yield: {self.accumulated_yield_energy}")
                except Exception as e:
                    Domoticz.Debug(f"Error processing yield: {e}")
                continue
            
            try:
                # v3: Extract value from Result dataclass (.value attribute)
                device_value = str(value.value)
                
                # VALUE CACHING: Only update if value actually changed
                # This is important for:
                # - Reducing database writes
                # - Avoiding unnecessary Domoticz processing
                # - Improving overall system efficiency
                cache_key = f"{register_type}_{key}"
                if cache_key not in self.cached_values or self.cached_values[cache_key] != device_value:
                    self._update_device(device_name, device_value)
                    self.cached_values[cache_key] = device_value
                    updated_count += 1
                
            except (ValueError, IndexError, TypeError) as e:
                Domoticz.Log(f"Error processing register {key}: {str(e)}")

        # Update energy meter with combined values
        # Energy meter shows: current_power;total_yield
        if 'active_power_fast' in result:
            try:
                # v3: Access .value on Result dataclass
                active_power = result['active_power_fast'].value
                energy_value = f"{active_power};{self.accumulated_yield_energy}"
                self._update_device("Energy Meter", energy_value)
                updated_count += 1
            except Exception as e:
                Domoticz.Log(f"Error updating energy meter: {str(e)}")
        
        if updated_count > 0:
            Domoticz.Debug(f"Updated {updated_count} devices ({register_type})")

    def _connect_inverter(self) -> Optional[HuaweiSolarDevice]:
        """
        Establish connection to Huawei inverter via Modbus TCP.
        
        Uses the huawei-solar v3 two-step connection pattern:
        1. create_tcp_client() - synchronous, creates the Modbus TCP client
        2. create_device_instance() - async, auto-detects device type
        
        CRITICAL: Uses wait_for() to enforce CONNECTION_TIMEOUT
        
        Returns:
            HuaweiSolarDevice if connection successful, None if failed
        """
        Domoticz.Log(f"Connecting to inverter at {self.inverter_address}:{self.inverter_port} (unit_id={self.slave_id})")
        try:
            client = create_tcp_client(
                host=self.inverter_address,
                port=self.inverter_port,
                unit_id=self.slave_id
            )
            
            # Use wait_for() to prevent hanging on unresponsive inverters
            bridge = self.async_loop.run_until_complete(
                wait_for(
                    create_device_instance(client),
                    timeout=CONNECTION_TIMEOUT
                )
            )
            Domoticz.Log(f"Inverter connected successfully (unit_id={self.slave_id}, type={type(bridge).__name__})")
            return bridge
        except AsyncTimeoutError:
            Domoticz.Error(f"Connection timed out after {CONNECTION_TIMEOUT}s")
            return None
        except Exception as e:
            Domoticz.Error(f"Failed to connect to inverter: {str(e)}")
            return None

    def _reconnect_inverter(self) -> None:
        """
        Attempt to reconnect to inverter after connection failure.
        
        Uses _cleanup_bridge for safe disconnection with timeout,
        then attempts a fresh connection.
        """
        self._cleanup_bridge()
        self.bridge = self._connect_inverter()
        if self.bridge:
            Domoticz.Log("Reconnected successfully")
            self.consecutive_failures = 0
        else:
            Domoticz.Error("Reconnection failed")

    def _get_device(self, name: str) -> int:
        """
        Find a Domoticz device by its DeviceID (name).
        
        Domoticz devices are stored in the global Devices dictionary
        with unit number as key. We search by DeviceID to find the device.
        
        Args:
            name (str): The DeviceID to search for
            
        Returns:
            int: Unit number if device found, -1 if not found
            
        Algorithm:
            1. Iterate all devices in global Devices dict
            2. Compare device.DeviceID with search name
            3. Return unit number if found
            4. Return -1 if not found in any device
        """
        for device_unit, device in Devices.items():
            if device.DeviceID.strip() == name:
                return device_unit
        return -1

    def _create_devices(self, device_list: List[str], device_type: int, subtype: int) -> None:
        """
        Create multiple Domoticz devices if they don't already exist.
        
        Convenience method to create a batch of devices from a list.
        Checks if each device exists before creating.
        
        Args:
            device_list: List of device names (e.g., ['PV_01_VOLTAGE', 'PV_02_VOLTAGE'])
            device_type: Domoticz device type (243, 248, etc.)
            subtype: Domoticz device subtype
        """
        for device_name in device_list:
            if self._get_device(device_name) < 0:  # Device doesn't exist
                self._create_device(device_name, device_type, subtype)

    def _create_device(self, name: str, device_type: int, subtype: int, switchtype: int = 0) -> None:
        """
        Create a single Domoticz device.
        
        Args:
            name (str): Device name (also used as DeviceID)
            device_type (int): Domoticz device type
                              - 243: Sensor types (voltage, current, etc.)
                              - 248: Power sensor
                              - See Domoticz documentation for full list
            subtype (int): Device subtype for specific measurement units
                          - Type 243 Subtype 8: Voltage (V)
                          - Type 243 Subtype 23: Current (A)
                          - See Domoticz documentation
            switchtype (int): Only relevant for Type 244+ (control devices)
        
        Process:
        1. Find first available Unit number (1-255)
        2. Create device with that Unit number
        3. Set DeviceID = name for identification
        4. Log result
        """
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
                Used=0,  # Device not used initially
                DeviceID=name,  # Use name as unique identifier
                Switchtype=switchtype
            ).Create()
            Domoticz.Debug(f"Device created: {name} (Unit {unit})")
        except Exception as e:
            Domoticz.Error(f"Failed to create device {name}: {str(e)}")

    def _find_available_unit(self) -> int:
        """
        Find the first available Unit number for a new device.
        
        Domoticz devices are numbered 1-255. This method finds the first
        unused number.
        
        Returns:
            int: First available unit number (1-255), or 0 if all full
        """
        for unit in range(1, 256):
            if unit not in Devices:
                return unit
        return 0

    def _update_device(self, name: str, value: str) -> None:
        """
        Update a Domoticz device's value.
        
        Only updates the device if the value has changed (prevents
        redundant database writes). This is called by _process_results()
        after cache checking.
        
        Args:
            name (str): Device name (DeviceID)
            value (str): New value to set (converted to string)
            
        Process:
        1. Find device by name
        2. Check if sValue (string value) has changed
        3. If changed: call device.Update()
        4. If not changed: skip update (cache prevents redundant calls)
        
        Note: This method is only called if cache checking already
              determined the value has changed
        """
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


# ============================================================================
# GLOBAL PLUGIN INSTANCE AND DOMOTICZ CALLBACKS
# ============================================================================
# These functions are called by Domoticz framework

_plugin: Optional[HuaweiSolarPlugin] = None


def onStart() -> None:
    """Domoticz framework callback: Plugin initialization."""
    global _plugin
    _plugin = HuaweiSolarPlugin()
    _plugin.onStart()


def onStop() -> None:
    """Domoticz framework callback: Plugin shutdown."""
    global _plugin
    if _plugin:
        _plugin.onStop()


def onConnect(Connection, Status: int, Description: str) -> None:
    """Domoticz framework callback: Connection event."""
    global _plugin
    if _plugin:
        _plugin.onConnect(Connection, Status, Description)


def onMessage(Connection, Data) -> None:
    """Domoticz framework callback: Incoming message."""
    global _plugin
    if _plugin:
        _plugin.onMessage(Connection, Data)


def onCommand(DeviceID: int, Unit: int, Command: str, Level: int, Color: str) -> None:
    """Domoticz framework callback: Device command."""
    global _plugin
    if _plugin:
        _plugin.onCommand(DeviceID, Unit, Command, Level, Color)


def onNotification(Name: str, Subject: str, Text: str, Status: str, Priority: int, Sound: str, ImageFile: str) -> None:
    """Domoticz framework callback: Notification."""
    global _plugin
    if _plugin:
        _plugin.onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile)


def onDisconnect(Connection) -> None:
    """Domoticz framework callback: Disconnection."""
    global _plugin
    if _plugin:
        _plugin.onDisconnect(Connection)


def onHeartbeat() -> None:
    """
    Domoticz framework callback: Heartbeat (every 5 seconds).
    
    This is the main processing loop. Called at regular intervals by Domoticz.
    See HuaweiSolarPlugin.onHeartbeat() for implementation details.
    """
    global _plugin
    if _plugin:
        _plugin.onHeartbeat()