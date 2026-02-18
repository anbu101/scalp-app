# Shared WebSocket engine registry
_WS_ENGINE_REGISTRY = []

def register_ws_engine(engine):
    if engine not in _WS_ENGINE_REGISTRY:
        _WS_ENGINE_REGISTRY.append(engine)

def get_ws_engines():
    return _WS_ENGINE_REGISTRY
