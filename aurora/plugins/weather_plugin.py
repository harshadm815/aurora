import logging

from aurora.plugins.plugin import BasePlugin

logger = logging.getLogger(__name__)


class WeatherPlugin(BasePlugin):
    """
    Placeholder. No weather data source is connected yet
    (planned for Milestone 4).
    """

    def __init__(self):
        super().__init__("Weather")

    def initialize(self):
        logger.info("Weather plugin initialized (no data source yet)")

    def shutdown(self):
        logger.info("Weather plugin shut down")
