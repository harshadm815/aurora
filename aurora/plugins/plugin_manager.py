import logging

from aurora.events import topics
from aurora.kernel.context import KernelContext
from aurora.plugins.plugin import BasePlugin

logger = logging.getLogger(__name__)


class PluginManager:
    """
    Registers plugins and initializes / shuts them down.

    - A plugin missing any of its ``required_permissions`` is blocked
      (disabled, never initialized).
    - A plugin that raises during initialization is disabled.
    - Either way the other plugins still run.
    """

    def __init__(self, context: KernelContext | None = None):
        self.context = context or KernelContext()
        self.plugins: dict[str, BasePlugin] = {}
        self._initialized: set[str] = set()

    def register(self, plugin: BasePlugin):
        if plugin.name in self.plugins:
            raise ValueError(f"Plugin '{plugin.name}' is already registered.")

        self.plugins[plugin.name] = plugin

    def _emit(self, name, plugin, **data):
        self.context.events.emit(
            name, {"plugin": plugin.name, **data}, source="PluginManager"
        )

    def initialize_all(self):
        for plugin in self.plugins.values():
            if not plugin.enabled or plugin.name in self._initialized:
                continue

            missing = self.context.permissions.missing(
                plugin.name, plugin.required_permissions
            )
            if missing:
                plugin.enabled = False
                names = [p.value for p in missing]
                logger.warning(
                    "Plugin %s blocked: missing permissions %s "
                    "(grant them in settings.PERMISSION_GRANTS)",
                    plugin.name, ", ".join(names),
                )
                self._emit(topics.PLUGIN_BLOCKED, plugin, missing=names)
                continue

            try:
                plugin.attach(self.context)
                plugin.initialize()
            except Exception as error:
                plugin.enabled = False
                logger.exception("Plugin failed to initialize: %s", plugin.name)
                self._emit(topics.PLUGIN_FAILED, plugin, error=str(error))
                continue

            self._initialized.add(plugin.name)
            logger.info("Plugin initialized: %s", plugin.name)
            self._emit(topics.PLUGIN_INITIALIZED, plugin)

    def shutdown_all(self):
        # Shut down in reverse order of initialization.
        for plugin in reversed(list(self.plugins.values())):
            if plugin.name not in self._initialized:
                continue
            try:
                plugin.shutdown()
                logger.info("Plugin shut down: %s", plugin.name)
            except Exception:
                logger.exception("Plugin failed to shut down: %s", plugin.name)
            finally:
                self._initialized.discard(plugin.name)
                self._emit(topics.PLUGIN_SHUTDOWN, plugin)

    def list_plugins(self):
        return list(self.plugins.keys())

    def status(self):
        def state(plugin):
            if plugin.name in self._initialized:
                return "Running"
            return "Disabled" if not plugin.enabled else "Stopped"

        return {name: state(plugin) for name, plugin in self.plugins.items()}
