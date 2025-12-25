# app/sim/sim_flags.py

_SIMULATION_ENABLED = False

def enable_simulation():
    global _SIMULATION_ENABLED
    _SIMULATION_ENABLED = True

def disable_simulation():
    global _SIMULATION_ENABLED
    _SIMULATION_ENABLED = False

def is_simulation_enabled() -> bool:
    return _SIMULATION_ENABLED
