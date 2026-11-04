from aurora.plugins.plugin import BasePlugin


class WeatherPlugin(BasePlugin):

    def __init__(self):
        super().__init__("Weather")

    def initialize(self):
        print("Weather Plugin Initialized")

    def shutdown(self):
        print("Weather Plugin Shutdown")