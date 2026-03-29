from typing import Dict

def sensor(name: str, **kwargs) -> Dict[str, object]:
    data: Dict[str, object] = {"name": name}
    data.update(kwargs)
    return data


SENSORS: Dict[str, Dict[str, object]] = {
    # Info / identity
    "model_code": sensor("Device Info - Device Type", icon="mdi:identifier", entity_category="diagnostic"),
    "output_model": sensor("Device Info - Output Model", icon="mdi:transmission-tower", entity_category="diagnostic"),
    "mode": sensor("Device Info - Mode", icon="mdi:transmission-tower-export"),
    "status_code": sensor("Device Info - Status Code", icon="mdi:identifier", entity_category="diagnostic"),
    "firmware_info": sensor("Device Info - Firmware Info", icon="mdi:chip", entity_category="diagnostic"),
    "firmware_version": sensor("Device Info - Firmware Version", icon="mdi:chip", entity_category="diagnostic"),
    "firmware_build_date": sensor("Device Info - Firmware Build Date", icon="mdi:calendar", entity_category="diagnostic"),
    "firmware_build_slot": sensor("Device Info - Firmware Build Slot", icon="mdi:counter", entity_category="diagnostic"),

    # Battery / BMS main page
    "bat_v": sensor("Battery Status - Battery Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery"),
    "bat_cap": sensor("Battery Status - Battery Capacity", unit="%", device_class="battery", state_class="measurement", icon="mdi:battery-high"),
    "bat_charge_current": sensor("Battery Status - Battery Charging Current", unit="A", device_class="current", state_class="measurement", icon="mdi:battery-plus"),
    "dischg_current": sensor("Battery Status - Battery Discharge Current", unit="A", device_class="current", state_class="measurement", icon="mdi:battery-minus"),
    "bat_series_count": sensor("Battery Status - Battery Number In Series", state_class="measurement", icon="mdi:numeric"),
    "battery_status": sensor("Battery Status - Battery Status", icon="mdi:battery-sync"),
    "battery_type": sensor("Battery Status - Battery Type", icon="mdi:battery-unknown"),
    "c_bms_total_capacity_ah": sensor("Battery Status - Calculated BMS Total Capacity", unit="Ah", state_class="measurement", icon="mdi:battery-high"),
    "c_bms_remaining_capacity_ah": sensor("Battery Status - Calculated BMS Remaining Capacity", unit="Ah", state_class="measurement", icon="mdi:battery-medium"),

    # BMS page
    "bms_remaining_ah": sensor("BMS Status - Remaining Capacity", unit="Ah", icon="mdi:battery-medium"),
    "bms_nominal_ah": sensor("BMS Status - Nominal Capacity", unit="Ah", icon="mdi:battery-outline"),
    "bms_display_mode": sensor("BMS Status - Display Mode", icon="mdi:view-grid-outline"),
    "bms_max_cell_mv": sensor("BMS Status - Max Voltage", unit="mV", state_class="measurement", icon="mdi:battery-high"),
    "bms_max_cell_pos": sensor("BMS Status - Max Voltage Cell Position", state_class="measurement", icon="mdi:numeric"),
    "bms_min_cell_mv": sensor("BMS Status - Min Voltage", unit="mV", state_class="measurement", icon="mdi:battery-low"),
    "bms_min_cell_pos": sensor("BMS Status - Min Voltage Cell Position", state_class="measurement", icon="mdi:numeric"),
    "bms_cell_count": sensor("BMS Status - BMS Cell Count", state_class="measurement", icon="mdi:battery-sync"),
    "bms_cell_delta_mv": sensor("BMS Status - BMS Cell Delta", unit="mV", state_class="measurement", icon="mdi:battery-sync"),
    "cell_1_mv": sensor("BMS Status - Battery Voltage 1", unit="mV", state_class="measurement", icon="mdi:car-battery"),
    "cell_2_mv": sensor("BMS Status - Battery Voltage 2", unit="mV", state_class="measurement", icon="mdi:car-battery"),
    "cell_3_mv": sensor("BMS Status - Battery Voltage 3", unit="mV", state_class="measurement", icon="mdi:car-battery"),
    "cell_4_mv": sensor("BMS Status - Battery Voltage 4", unit="mV", state_class="measurement", icon="mdi:car-battery"),
    "cell_5_mv": sensor("BMS Status - Battery Voltage 5", unit="mV", state_class="measurement", icon="mdi:car-battery"),
    "cell_6_mv": sensor("BMS Status - Battery Voltage 6", unit="mV", state_class="measurement", icon="mdi:car-battery"),
    "cell_7_mv": sensor("BMS Status - Battery Voltage 7", unit="mV", state_class="measurement", icon="mdi:car-battery"),
    "cell_8_mv": sensor("BMS Status - Battery Voltage 8", unit="mV", state_class="measurement", icon="mdi:car-battery"),
    "cell_9_mv": sensor("BMS Status - Battery Voltage 9", unit="mV", state_class="measurement", icon="mdi:car-battery"),
    "cell_10_mv": sensor("BMS Status - Battery Voltage 10", unit="mV", state_class="measurement", icon="mdi:car-battery"),
    "cell_11_mv": sensor("BMS Status - Battery Voltage 11", unit="mV", state_class="measurement", icon="mdi:car-battery"),
    "cell_12_mv": sensor("BMS Status - Battery Voltage 12", unit="mV", state_class="measurement", icon="mdi:car-battery"),
    "cell_13_mv": sensor("BMS Status - Battery Voltage 13", unit="mV", state_class="measurement", icon="mdi:car-battery"),
    "cell_14_mv": sensor("BMS Status - Battery Voltage 14", unit="mV", state_class="measurement", icon="mdi:car-battery"),
    "cell_15_mv": sensor("BMS Status - Battery Voltage 15", unit="mV", state_class="measurement", icon="mdi:car-battery"),
    "cell_16_mv": sensor("BMS Status - Battery Voltage 16", unit="mV", state_class="measurement", icon="mdi:car-battery"),

    # Grid page
    "grid_v": sensor("Grid Status - AC Input Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:transmission-tower"),
    "grid_hz": sensor("Grid Status - Mains Frequency", unit="Hz", device_class="frequency", state_class="measurement", icon="mdi:current-ac"),
    "mains_current_flow_direction": sensor("Grid Status - Mains Current Flow Direction", icon="mdi:swap-horizontal-bold"),
    "mains_power_w": sensor("Grid Status - Mains Power", unit="W", device_class="power", state_class="measurement", icon="mdi:transmission-tower-export"),
    "c_mains_power_w": sensor("Grid Status - Calculated Mains Power", unit="W", device_class="power", state_class="measurement", icon="mdi:transmission-tower-export"),
    "mains_apparent_va": sensor("Grid Status - Mains Apparent Power", unit="VA", device_class="apparent_power", state_class="measurement", icon="mdi:flash"),

    # Load page
    "out_v": sensor("Load Status - Output Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:power-plug"),
    "out_hz": sensor("Load Status - Output Frequency", unit="Hz", device_class="frequency", state_class="measurement", icon="mdi:current-ac"),
    "apparent_va": sensor("Load Status - Output Apparent Power", unit="VA", device_class="apparent_power", state_class="measurement", icon="mdi:flash"),
    "load_w": sensor("Load Status - Output Active Power", unit="W", device_class="power", state_class="measurement", icon="mdi:home-lightning-bolt"),
    "c_load_w": sensor("Load Status - Calculated Output Active Power", unit="W", device_class="power", state_class="measurement", icon="mdi:home-lightning-bolt"),
    "load_pct": sensor("Load Status - Output Load Percent", unit="%", state_class="measurement", icon="mdi:gauge"),
    "output_dc_comp": sensor("Load Status - Output DC Component", state_class="measurement", icon="mdi:tune-variant"),

    # PV page
    "generation_power_w": sensor("PV Panel Status - Generation Power", unit="W", device_class="power", state_class="measurement", icon="mdi:solar-power"),
    "c_generation_power_w": sensor("PV Panel Status - Calculated Generation Power", unit="W", device_class="power", state_class="measurement", icon="mdi:solar-power"),
    "pv_v": sensor("PV Panel Status - PV Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:solar-panel"),
    "pv_current_a": sensor("PV Panel Status - PV Current", unit="A", device_class="current", state_class="measurement", icon="mdi:current-dc"),
    "pv_w": sensor("PV Panel Status - PV Power", unit="W", device_class="power", state_class="measurement", icon="mdi:solar-power"),
    "pv2_v": sensor("PV Panel Status - PV2 Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:solar-panel-large"),
    "pv2_current_a": sensor("PV Panel Status - PV2 Current", unit="A", device_class="current", state_class="measurement", icon="mdi:current-dc"),
    "pv2_power_w": sensor("PV Panel Status - PV2 Power", unit="W", device_class="power", state_class="measurement", icon="mdi:solar-power-variant"),
    "pv_today_kwh": sensor("PV Panel Status - Daily Electricity Generation", unit="kWh", device_class="energy", icon="mdi:solar-power-variant"),
    "pv_month_kwh": sensor("PV Panel Status - Monthly Electricity Generation", unit="kWh", device_class="energy", icon="mdi:calendar-month"),
    "pv_total_kwh": sensor("PV Panel Status - Total Electricity Generation", unit="kWh", device_class="energy", state_class="total_increasing", icon="mdi:counter"),
    "pv_year_kwh": sensor("PV Panel Status - Yearly Electricity Generation", unit="kWh", device_class="energy", icon="mdi:calendar-range"),
    "pv_temp": sensor("PV Panel Status - PV Temperature", unit="°C", device_class="temperature", state_class="measurement", icon="mdi:thermometer"),
    "pv2_temp": sensor("PV Panel Status - PV2 Temperature", unit="°C", device_class="temperature", state_class="measurement", icon="mdi:thermometer"),
    "solar_charging_switch": sensor("PV Panel Status - Solar Charging Switch", icon="mdi:solar-power-variant-outline"),

    # App "More" page – mapped / partially mapped
    "bus_voltage": sensor("Settings - BUS Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:flash-triangle", entity_category="diagnostic"),
    "ac_charging_switch": sensor("Settings - AC Charging Switch", icon="mdi:power-plug-battery", entity_category="diagnostic"),
    "abnormal_fan_speed": sensor("Settings - Abnormal Fan Speed", icon="mdi:fan-alert", entity_category="diagnostic"),
    "abnormal_low_pv_power": sensor("Settings - Abnormal Low PV Power", icon="mdi:solar-panel-large", entity_category="diagnostic"),
    "abnormal_temperature_sensor": sensor("Settings - Abnormal Temperature Sensor", icon="mdi:thermometer-alert", entity_category="diagnostic"),
    "automatic_return_to_first_page": sensor("Settings - Automatic Return To The First Page Function", icon="mdi:page-first", entity_category="diagnostic"),
    "bms_allow_charging_flag": sensor("Settings - BMS Allow Charging Flag", icon="mdi:battery-plus-variant", entity_category="diagnostic"),
    "bms_allow_discharge_flag": sensor("Settings - BMS Allow Discharge Flag", icon="mdi:battery-minus-variant", entity_category="diagnostic"),
    "bms_auto_start_soc_after_low": sensor("Settings - BMS Automatically Starts SOC After Low", unit="%", state_class="measurement", icon="mdi:battery-sync", entity_category="diagnostic"),
    "bms_avg_temp_c": sensor("Settings - BMS Average Temperature", unit="°C", device_class="temperature", state_class="measurement", icon="mdi:thermometer", entity_category="diagnostic"),
    "bms_charge_current_limit_a": sensor("Settings - BMS Charge Current Limit", unit="A", device_class="current", state_class="measurement", icon="mdi:current-dc", entity_category="diagnostic"),
    "bms_charge_voltage_limit_v": sensor("Settings - BMS Charge Voltage Limit", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-arrow-up", entity_category="diagnostic"),
    "bms_charging_current_a": sensor("Settings - BMS Charging Current", unit="A", device_class="current", state_class="measurement", icon="mdi:battery-plus", entity_category="diagnostic"),
    "bms_charging_overcurrent_sign": sensor("Settings - BMS Charging Overcurrent Sign", icon="mdi:alert", entity_category="diagnostic"),
    "bms_communication_control_function": sensor("Settings - BMS Communication Control Function", icon="mdi:lan-connect", entity_category="diagnostic"),
    "bms_communication_normal": sensor("Settings - BMS Communication Normal", icon="mdi:lan-check", entity_category="diagnostic"),
    "bms_current_soc": sensor("Settings - BMS Current SOC", unit="%", state_class="measurement", icon="mdi:battery-high"),
    "bms_discharge_current_a": sensor("Settings - BMS Discharge Current", unit="A", device_class="current", state_class="measurement", icon="mdi:battery-minus", entity_category="diagnostic"),
    "bms_discharge_overcurrent_flag": sensor("Settings - BMS Discharge Overcurrent Flag", icon="mdi:alert", entity_category="diagnostic"),
    "bms_discharge_voltage_limit_v": sensor("Settings - BMS Discharge Voltage Limit", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-arrow-down", entity_category="diagnostic"),
    "bms_low_battery_alarm_flag": sensor("Settings - BMS Low Battery Alarm Flag", icon="mdi:battery-alert", entity_category="diagnostic"),
    "bms_low_power_fault_flag": sensor("Settings - BMS Low Power Fault Flag", icon="mdi:alert-circle", entity_category="diagnostic"),
    "bms_low_power_soc": sensor("Settings - BMS Low Power SOC", unit="%", state_class="measurement", icon="mdi:battery-10", entity_category="diagnostic"),
    "bms_low_temperature_flag": sensor("Settings - BMS Low Temperature Flag", icon="mdi:snowflake-alert", entity_category="diagnostic"),
    "bms_returns_to_battery_mode_soc": sensor("Settings - BMS Returns To Battery Mode SOC", unit="%", state_class="measurement", icon="mdi:battery-arrow-up", entity_category="diagnostic"),
    "bms_returns_to_mains_mode_soc": sensor("Settings - BMS Returns To Mains Mode SOC", unit="%", state_class="measurement", icon="mdi:transmission-tower", entity_category="diagnostic"),
    "bms_temperature_too_high_flag": sensor("Settings - BMS Temperature Too High Flag", icon="mdi:thermometer-alert", entity_category="diagnostic"),
    "battery_equalization_mode": sensor("Settings - Battery Equalization Mode", icon="mdi:battery-sync", entity_category="diagnostic"),
    "battery_equalization_voltage_v": sensor("Settings - Battery Equalization Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-sync", entity_category="diagnostic"),
    "battery_not_connected": sensor("Settings - Battery Not Connected", icon="mdi:battery-off", entity_category="diagnostic"),
    "battery_overvoltage_shutdown_voltage_v": sensor("Settings - Battery Overvoltage Shutdown Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-alert-variant-outline", entity_category="diagnostic"),
    "battery_voltage_higher": sensor("Settings - Battery Voltage Higher", icon="mdi:battery-alert", entity_category="diagnostic"),
    "boost_temperature_c": sensor("Settings - Boost Temperature", unit="°C", device_class="temperature", state_class="measurement", icon="mdi:thermometer-chevron-up", entity_category="diagnostic"),
    "buzzer_function": sensor("Settings - Buzzer Function", icon="mdi:bullhorn", entity_category="diagnostic"),
    "charging_light_status": sensor("Settings - Charging Light Status", icon="mdi:lightbulb", entity_category="diagnostic"),
    "charging_main_switch": sensor("Settings - Charging Main Switch", icon="mdi:power", entity_category="diagnostic"),
    "charging_priority_order": sensor("Settings - Charging Priority Order", icon="mdi:sort", entity_category="diagnostic"),
    "ct_function_switch": sensor("Settings - CT Function Switch", icon="mdi:current-ac", entity_category="diagnostic"),
    "dc_rectification_temperature_c": sensor("Settings - DC Rectification Temperature", unit="°C", device_class="temperature", state_class="measurement", icon="mdi:thermometer", entity_category="diagnostic"),
    "does_machine_have_output": sensor("Settings - Does The Machine Have An Output", icon="mdi:power-plug", entity_category="diagnostic"),
    "dual_output_mode": sensor("Settings - Dual Output Mode", icon="mdi:power-socket", entity_category="diagnostic"),
    "eco": sensor("Settings - ECO", icon="mdi:leaf", entity_category="diagnostic"),
    "eeprom_data_abnormality": sensor("Settings - EEPROM Data Abnormality", icon="mdi:memory", entity_category="diagnostic"),
    "eeprom_read_write_exception": sensor("Settings - EEPROM Read Write Exception", icon="mdi:memory-alert", entity_category="diagnostic"),
    "equalization_interval": sensor("Settings - Equalization Interval", icon="mdi:calendar-sync", entity_category="diagnostic"),
    "equalization_overtime": sensor("Settings - Equalization Overtime", icon="mdi:timer-alert", entity_category="diagnostic"),
    "equalization_time": sensor("Settings - Equalization Time", icon="mdi:timer", entity_category="diagnostic"),
    "fan_1_speed": sensor("Settings - Fan 1 Speed", unit="%", state_class="measurement", icon="mdi:fan", entity_category="diagnostic"),
    "fan_1_status": sensor("Settings - Fan 1 Status", icon="mdi:fan", entity_category="diagnostic"),
    "fan_2_speed": sensor("Settings - Fan 2 Speed", unit="%", state_class="measurement", icon="mdi:fan", entity_category="diagnostic"),
    "fan_2_status": sensor("Settings - Fan 2 Status", icon="mdi:fan", entity_category="diagnostic"),
    "float_charging_voltage_v": sensor("Settings - Float Charging Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-charging-medium", entity_category="diagnostic"),
    "grid_connected_current_a": sensor("Settings - Grid Connected Current", unit="A", device_class="current", state_class="measurement", icon="mdi:current-ac", entity_category="diagnostic"),
    "grid_connection_function": sensor("Settings - Grid Connection Function", icon="mdi:transmission-tower", entity_category="diagnostic"),
    "grid_connection_sign": sensor("Settings - Grid Connection Sign", icon="mdi:transmission-tower-off", entity_category="diagnostic"),
    "high_frequency_of_mains_power_loss_hz": sensor("Settings - High Frequency Of Mains Power Loss", unit="Hz", device_class="frequency", state_class="measurement", icon="mdi:current-ac", entity_category="diagnostic"),
    "high_point_of_mains_power_loss_voltage_v": sensor("Settings - High Point Of Mains Power Loss Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:flash-alert", entity_category="diagnostic"),
    "inductor_current_a": sensor("Settings - Inductor Current", unit="A", device_class="current", state_class="measurement", icon="mdi:coil", entity_category="diagnostic"),
    "input_source_prompt_function": sensor("Settings - Input Source Prompt Function", icon="mdi:tooltip-outline", entity_category="diagnostic"),
    "input_voltage_too_high": sensor("Settings - Input Voltage Too High", icon="mdi:flash-alert", entity_category="diagnostic"),
    "inverter_light_status": sensor("Settings - Inverter Light Status", icon="mdi:lightbulb", entity_category="diagnostic"),
    "inverter_temperature_c": sensor("Settings - Inverter Temperature", unit="°C", device_class="temperature", state_class="measurement", icon="mdi:thermometer", entity_category="diagnostic"),
    "lcd_back_lighting": sensor("Settings - LCD Back Lighting", icon="mdi:monitor", entity_category="diagnostic"),
    "li_battery_activation_function_switch": sensor("Settings - Li Battery Activation Function Switch", icon="mdi:battery-heart", entity_category="diagnostic"),
    "li_battery_activation_process": sensor("Settings - Li Battery Activation Process", icon="mdi:battery-heart-variant", entity_category="diagnostic"),
    "low_battery_alarm": sensor("Settings - Low Battery Alarm", icon="mdi:battery-alert", entity_category="diagnostic"),
    "low_electric_lock_voltage_v": sensor("Settings - Low Electric Lock Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-off-outline", entity_category="diagnostic"),
    "low_frequency_of_mains_power_loss_hz": sensor("Settings - Low Frequency Of Mains Power Loss", unit="Hz", device_class="frequency", state_class="measurement", icon="mdi:current-ac", entity_category="diagnostic"),
    "low_point_of_mains_power_loss_voltage_v": sensor("Settings - Low Point Of Mains Power Loss Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:flash-alert", entity_category="diagnostic"),
    "machine_over_temperature": sensor("Settings - Machine Over Temperature", icon="mdi:thermometer-alert", entity_category="diagnostic"),
    "main_output_relay_status": sensor("Settings - Main Output Relay Status", icon="mdi:toggle-switch", entity_category="diagnostic"),
    "mains_charging_ending_time": sensor("Settings - Mains Charging Ending Time", icon="mdi:clock-end", entity_category="diagnostic"),
    "mains_charging_starting_time": sensor("Settings - Mains Charging Starting Time", icon="mdi:clock-start", entity_category="diagnostic"),
    "mains_input_range": sensor("Settings - Mains Input Range", icon="mdi:sine-wave", entity_category="diagnostic"),
    "mains_light_status": sensor("Settings - Mains Light Status", icon="mdi:lightbulb", entity_category="diagnostic"),
    "max_utility_charge_current_a": sensor("Settings - Max utility charge current", unit="A", device_class="current", state_class="measurement", icon="mdi:current-ac", entity_category="diagnostic"),
    "max_temperature_c": sensor("Settings - Max. Temperature", unit="°C", device_class="temperature", state_class="measurement", icon="mdi:thermometer-high", entity_category="diagnostic"),
    "maximum_total_charging_current_a": sensor("Settings - Maximum Total Charging Current", unit="A", device_class="current", state_class="measurement", icon="mdi:current-dc", entity_category="diagnostic"),
    "mppt_constant_temperature_mode": sensor("Settings - MPPT Constant Temperature Mode", icon="mdi:solar-panel", entity_category="diagnostic"),
    "output_ending_time": sensor("Settings - Output Ending Time", icon="mdi:clock-end", entity_category="diagnostic"),
    "output_set_frequency": sensor("Settings - Output Set Frequency", unit="Hz", device_class="frequency", state_class="measurement", icon="mdi:current-ac", entity_category="diagnostic"),
    "output_set_voltage": sensor("Settings - Output Set Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:flash", entity_category="diagnostic"),
    "output_starting_time": sensor("Settings - Output Starting Time", icon="mdi:clock-start", entity_category="diagnostic"),
    "over_temperature_restart_function": sensor("Settings - Over Temperature Restart Function", icon="mdi:restart", entity_category="diagnostic"),
    "overloaded": sensor("Settings - OverLoaded", icon="mdi:alert", entity_category="diagnostic"),
    "overload_restart_function": sensor("Settings - Overload Restart Function", icon="mdi:restart", entity_category="diagnostic"),
    "overload_to_bypass_function": sensor("Settings - Overload To Bypass Function", icon="mdi:swap-horizontal", entity_category="diagnostic"),
    "parallel_mode": sensor("Settings - Parallel Mode", icon="mdi:call-split", entity_category="diagnostic"),
    "parallel_mode_turn_off_soc": sensor("Settings - Parallel Mode Turn Off SOC", unit="%", state_class="measurement", icon="mdi:battery-arrow-down", entity_category="diagnostic"),
    "parallel_mode_turn_off_voltage_v": sensor("Settings - Parallel Mode Turn Off Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-arrow-down-outline", entity_category="diagnostic"),
    "parallel_role": sensor("Settings - Parallel Role", icon="mdi:account-switch", entity_category="diagnostic"),
    "power_supply_from_pv_to_load_in_ac_state": sensor("Settings - Power Supply From PV To Load In AC State", icon="mdi:solar-power-variant", entity_category="diagnostic"),
    "pv_energy_feeding_priority": sensor("Settings - PV Energy Feeding Priority", icon="mdi:sort-variant", entity_category="diagnostic"),
    "pv_grid_connection_agreement": sensor("Settings - PV Grid Connection Agreement", icon="mdi:file-document-outline", entity_category="diagnostic"),
    "return_to_battery_mode_voltage_v": sensor("Settings - Return To Battery Mode Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-arrow-up-outline", entity_category="diagnostic"),
    "return_to_mains_mode_voltage_v": sensor("Settings - Return To Mains Mode Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:transmission-tower", entity_category="diagnostic"),
    "second_delay_time": sensor("Settings - Second Delay Time", icon="mdi:timer-outline", entity_category="diagnostic"),
    "second_output_battery_capacity": sensor("Settings - Second Output Battery Capacity", unit="%", state_class="measurement", icon="mdi:battery-50", entity_category="diagnostic"),
    "second_output_battery_voltage_v": sensor("Settings - Second Output Battery Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery", entity_category="diagnostic"),
    "second_output_discharge_time": sensor("Settings - Second Output Discharge Time", icon="mdi:timer-sand", entity_category="diagnostic"),
    "software_version": sensor("Settings - Software Version", icon="mdi:chip", entity_category="diagnostic"),
    "strong_charging_voltage_v": sensor("Settings - Strong Charging Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-charging-high", entity_category="diagnostic"),
    "system_time_hm": sensor("Settings - System Time (Hour Minute)", icon="mdi:clock-outline", entity_category="diagnostic"),
    "system_time_ymd": sensor("Settings - System Time (Year Month Day)", icon="mdi:calendar-range", entity_category="diagnostic"),
    "total_number_of_grid_connection": sensor("Settings - Total Number Of Grid Connection", state_class="measurement", icon="mdi:counter", entity_category="diagnostic"),
    "transformer_temperature_c": sensor("Settings - Transformer Temperature", unit="°C", device_class="temperature", state_class="measurement", icon="mdi:thermometer", entity_category="diagnostic"),
    "warning_light_status": sensor("Settings - Warning Light Status", icon="mdi:alarm-light-outline", entity_category="diagnostic"),
    "working_mode": sensor("Settings - Working Mode", icon="mdi:cog-transfer", entity_category="diagnostic"),

    # Raw / decoded helper sensors
    "mains_wdrr_token": sensor("Settings - Mains WdRR Token", icon="mdi:code-string", entity_category="diagnostic", enabled_by_default=False),
    "mains_wdrr_value": sensor("Settings - Mains WdRR Value", state_class="measurement", icon="mdi:numeric", entity_category="diagnostic", enabled_by_default=False),
    "mains_wdrr_abs": sensor("Settings - Mains WdRR Absolute", state_class="measurement", icon="mdi:counter", entity_category="diagnostic", enabled_by_default=False),
    "mains_eo8w_code": sensor("Settings - Mains eo8w Code", icon="mdi:code-tags", entity_category="diagnostic", enabled_by_default=False),
    "wdrr_status_bits": sensor("Settings - WdRR Status Bits", icon="mdi:code-brackets", entity_category="diagnostic", enabled_by_default=False),
    "eo8w_flags_raw": sensor("Settings - eo8w Flags Raw", icon="mdi:code-braces", entity_category="diagnostic", enabled_by_default=False),
    "eo8w_blob_raw": sensor("Settings - eo8w Blob Raw", icon="mdi:code-json", entity_category="diagnostic", enabled_by_default=False),
    "yavb_flags_raw": sensor("Settings - Yavb Flags Raw", icon="mdi:code-braces", entity_category="diagnostic", enabled_by_default=False),
    "yavb_code_raw": sensor("Settings - Yavb Code Raw", icon="mdi:code-tags", entity_category="diagnostic", enabled_by_default=False),
    "yavb_aux_raw": sensor("Settings - Yavb Aux Raw", icon="mdi:code-json", entity_category="diagnostic", enabled_by_default=False),
    "output_status_bits": sensor("Settings - Output Status Bits", icon="mdi:code-brackets", entity_category="diagnostic", enabled_by_default=False),
    "mains_flow_code": sensor("Settings - Mains Flow Code", icon="mdi:numeric", entity_category="diagnostic", enabled_by_default=False),
    "mains_input_range_code": sensor("Settings - Mains Input Range Code", icon="mdi:code-string", entity_category="diagnostic", enabled_by_default=False),

    # Compatibility aliases for older entity names
    "bat_temp": sensor("Settings - Inverter Temperature (legacy)", unit="°C", device_class="temperature", state_class="measurement", icon="mdi:thermometer", entity_category="diagnostic", enabled_by_default=False),
    "max_chg": sensor("Settings - Max Charge Current (legacy)", unit="A", device_class="current", state_class="measurement", icon="mdi:current-dc", entity_category="diagnostic", enabled_by_default=False),
    "util_chg": sensor("Settings - Utility Charge Current (candidate)", unit="A", device_class="current", state_class="measurement", icon="mdi:current-ac", entity_category="diagnostic", enabled_by_default=False),
    "bulk_v": sensor("Settings - Bulk Charging Voltage (legacy)", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-charging-high", entity_category="diagnostic", enabled_by_default=False),
    "float_v": sensor("Settings - Float Charging Voltage (legacy)", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-charging-medium", entity_category="diagnostic", enabled_by_default=False),
    "cut_v": sensor("Settings - Low Battery Cut-off (legacy)", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-off-outline", entity_category="diagnostic", enabled_by_default=False),
    "mains_flow_state": sensor("Settings - Mains Flow State (legacy)", icon="mdi:swap-horizontal-bold", entity_category="diagnostic", enabled_by_default=False),
}

SENSOR_GROUP_TITLES: Dict[str, str] = {
    "main": "Main",
    "battery": "Battery",
    "bms": "BMS",
    "grid": "Grid",
    "load": "Load",
    "pv": "PV",
    "diagnostics": "Diagnostics",
}

MAIN_SENSOR_KEYS = {
    "c_mains_power_w",
    "c_load_w",
    "c_generation_power_w",
    "mode",
    "bms_current_soc",
    "c_bms_total_capacity_ah",
    "c_bms_remaining_capacity_ah",
}

_BATTERY_HINTS = (
    "battery",
    "bms",
    "charge",
    "discharge",
    "soc",
    "cell",
    "equalization",
    "boost",
)

_GRID_HINTS = (
    "grid",
    "mains",
    "utility",
    "input",
    "relay",
    "ct_",
)

_PV_HINTS = (
    "pv",
    "solar",
    "mppt",
)

_LOAD_HINTS = (
    "output",
    "load",
    "inverter",
    "parallel",
)


def _settings_group_for_key(key: str) -> str:
    k = key.lower()
    if any(token in k for token in _BATTERY_HINTS):
        return "battery"
    if any(token in k for token in _GRID_HINTS):
        return "grid"
    if any(token in k for token in _PV_HINTS):
        return "pv"
    if any(token in k for token in _LOAD_HINTS):
        return "load"
    return "diagnostics"


def get_sensor_group(key: str) -> str:
    if key in MAIN_SENSOR_KEYS:
        return "main"
    meta = SENSORS.get(key)
    if not meta:
        return "diagnostics"

    name = str(meta.get("name", ""))
    if name.startswith("Battery Status - "):
        return "battery"
    if name.startswith("BMS Status - "):
        return "bms"
    if name.startswith("Grid Status - "):
        return "grid"
    if name.startswith("Load Status - "):
        return "load"
    if name.startswith("PV Panel Status - "):
        return "pv"
    if name.startswith("Settings - "):
        return _settings_group_for_key(key)
    return "diagnostics"


def get_group_title(group: str) -> str:
    return SENSOR_GROUP_TITLES.get(group, "Diagnostics")


def get_grouped_sensor_keys() -> Dict[str, list]:
    grouped: Dict[str, list] = {}
    for key in SENSORS:
        group = get_sensor_group(key)
        grouped.setdefault(group, []).append(key)
    for keys in grouped.values():
        keys.sort()
    return grouped


