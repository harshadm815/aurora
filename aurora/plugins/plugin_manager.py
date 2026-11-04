from aurora.plugins.plugin import BasePlugin


class PluginManager:

    def __init__(self):
        self.plugins = {}

    def register(self, plugin: BasePlugin):
        self.plugins[plugin.name] = plugin

    def initialize_all(self):
        for plugin in self.plugins.values():
            plugin.initialize()

    def shutdown_all(self):
        for plugin in self.plugins.values():
            plugin.shutdown()

    def list_plugins(self):
        return list(self.plugins.keys())