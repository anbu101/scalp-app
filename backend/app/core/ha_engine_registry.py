# backend/app/core/ha_engine_registry.py
"""
HA Engine Registry
==================
Singleton list that holds live HAOptionsTickEngine instances.

Mirrors BB_ENGINE_REGISTRY pattern so ZerodhaTickEngine can forward
ticks to HA engines alongside BB engines without any coupling.
"""

HA_ENGINE_REGISTRY = []