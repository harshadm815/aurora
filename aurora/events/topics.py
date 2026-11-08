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
ROUTE_DECIDED = "routing.decided"
PLAN_CREATED = "planning.created"
PLAN_STEP_COMPLETED = "planning.step_completed"
PLAN_COMPLETED = "planning.completed"
# A structured workflow ran; payload is WorkflowTrace.to_dict() (no user text).
WORKFLOW_COMPLETED = "workflow.completed"
RESPONSE_GENERATED = "conversation.response_generated"
CONVERSATION_CLEARED = "conversation.cleared"

MEMORY_LEARNED = "memory.learned"

MOTION_DETECTED = "vision.motion_detected"

# Sensor service (background watching): readings with their times and sources,
# alerts when a reading becomes poor, and watching switched on or off.
# Health readings are never published.
SENSOR_READINGS = "sensor.readings"
SENSOR_ALERT = "sensor.alert"
SENSOR_MONITORING = "sensor.monitoring"

PERMISSION_DENIED = "permission.denied"

TOOL_EXECUTED = "tool.executed"
TOOL_FAILED = "tool.failed"
CONFIRMATION_REQUESTED = "tool.confirmation_requested"
CONFIRMATION_ANSWERED = "tool.confirmation_answered"
