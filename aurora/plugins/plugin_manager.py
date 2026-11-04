import logging

from aurora.plugins.plugin import BasePlugin

logger = logging.getLogger(__name__)


class PluginManager:
    """
    Registers plugins and initializes / shuts them down.

    A failing plugin is disabled and logged; the others still run.
    """

    def __init__(self):
        self.plugins: dict[str, BasePlugin] = {}

    def register(self, plugin: BasePlugin):
        if plugin.name in self.plugins:
            raise ValueError(f"Plugin '{plugin.name}' is already registered.")

        self.plugins[plugin.name] = plugin

    def initialize_all(self):
        for plugin in self.plugins.values():
            if not plugin.enabled:
                continue
            try:
                plugin.initialize()
                logger.info("Plugin initialized: %s", plugin.name)
            except Exception:
                plugin.enabled = False
                logger.exception("Plugin failed to initialize: %s", plugin.name)

    def shutdown_all(self):
        for plugin in self.plugins.values():
            if not plugin.enabled:
                continue
            try:
                plugin.shutdown()
                logger.info("Plugin shut down: %s", plugin.name)
            except Exception:
                logger.exception("Plugin failed to shut down: %s", plugin.name)

    def list_plugins(self):
        return list(self.plugins.keys())
