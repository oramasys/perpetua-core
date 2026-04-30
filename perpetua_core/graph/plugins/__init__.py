"""Graph plugin registry — load by name, never imported by engine.py."""
_registry: dict[str, object] = {}


def register(name: str, plugin: object) -> None:
    _registry[name] = plugin


def get(name: str) -> object:
    if name not in _registry:
        raise KeyError(f"Plugin '{name}' not registered. Call register() first.")
    return _registry[name]
