from aurora.plugins.plugin_manager import PluginManager
from aurora.plugins.weather_plugin import WeatherPlugin


manager = PluginManager()

manager.register(
    WeatherPlugin()
)

print(manager.list_plugins())

manager.initialize_all()

manager.shutdown_all()