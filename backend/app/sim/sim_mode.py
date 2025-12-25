# app/sim/sim_mode.py

"""
Global SIMULATION MODE flag.
Safe, isolated, easy to delete.
"""

_SIMULATION_ENABLED = False


def enable_simulation():
    global _SIMULATION_ENABLED
    _SIMULATION_ENABLED = True


def disable_simulation():
    global _SIMULATION_ENABLED
    _SIMULATION_ENABLED = False


def is_simulation_enabled() -> bool:
    return _SIMULATION_ENABLED
