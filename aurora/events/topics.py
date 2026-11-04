"""
Event names published by AURORA's kernel.

Using these constants instead of string literals keeps publishers and
subscribers in sync.
"""

KERNEL_STARTED = "kernel.started"
KERNEL_STOPPING = "kernel.stopping"
KERNEL_STOPPED = "kernel.stopped"

SERVICE_STARTED = "service.started"
SERVICE_FAILED = "service.failed"
SERVICE_STOPPED = "service.stopped"

PLUGIN_INITIALIZED = "plugin.initialized"
PLUGIN_BLOCKED = "plugin.blocked"
PLUGIN_FAILED = "plugin.failed"
PLUGIN_SHUTDOWN = "plugin.shutdown"

MESSAGE_RECEIVED = "conversation.message_received"
RESPONSE_GENERATED = "conversation.response_generated"
CONVERSATION_CLEARED = "conversation.cleared"

PERMISSION_DENIED = "permission.denied"
