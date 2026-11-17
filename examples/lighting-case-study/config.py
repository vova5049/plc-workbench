"""Actual Phase 1 configuration: three identical, independent local stations."""
N_BUTTONS = 24
WINDOW = "K4"          # 400 ms silence window, 100 ms timer base
LONG_PRESS = "K20"     # 2 seconds
STATIONS = ("PLC1", "PLC2", "PLC3")
SINGLE_ZONE_OVERRIDES = {}
DOUBLE_ZONE_OVERRIDES = {}
LONG_OFF_ZONE_OVERRIDES = {}
AUTO_OFF_HOURS = {}
ENABLE_CMD_DIAG = True
CMD_SEQ_REG = "D100"
CMD_LAST_REG = "D101"


def single_zones_for(station, button):
    return SINGLE_ZONE_OVERRIDES.get(station, {}).get(button, [button])


def double_zones_for(station, button):
    return DOUBLE_ZONE_OVERRIDES.get(station, {}).get(button, [])


def long_off_zones_for(station, button):
    return LONG_OFF_ZONE_OVERRIDES.get(station, {}).get(button, [])


def auto_off_hours_for(station, zone):
    return AUTO_OFF_HOURS.get(station, {}).get(zone)
