"""Public interface for the channels module."""

from agent.modules.channels.diagnostics import TestResult, test_channel_connection
from agent.modules.channels.contracts import (
    ChannelSettingField,
    ChannelSettingSection,
    ChatChannelAdapter,
    InboundMessage,
    OutboundMessage,
    ParsedCommand,
)
from agent.modules.channels.commands import (
    CommandRegistry,
    CommandSpec,
    get_default_command_registry,
)
from agent.modules.channels.manager import (
    ChannelManager,
    ChannelRunner,
    ChannelStatus,
    ManagedChannel,
)
from agent.modules.channels.service import (
    get_registered_channel_catalog,
    get_channel_status,
    get_channel_webhook_runtime,
    list_channel_statuses,
    load_channel_adapter,
    register_builtin_channel_adapters,
    register_builtin_channels,
    register_channels,
    start_all_channels,
    start_channel,
    start_enabled_channels,
    stop_all_channels,
    stop_channel,
)
from agent.modules.channels.registry import (
    ChannelRegistry,
    get_channel_registry,
    get_channel_setting_field,
    list_channel_catalog,
    register_channel_descriptors,
)
from agent.modules.channels.service_specs import (
    BUILTIN_CHANNEL_DESCRIPTORS,
    BUILTIN_CHANNEL_SPECS,
    ChannelDescriptor,
)
from agent.modules.channels.models import BotSettings

__all__ = [
    "BUILTIN_CHANNEL_DESCRIPTORS",
    "BUILTIN_CHANNEL_SPECS",
    "BotSettings",
    "ChannelDescriptor",
    "ChannelManager",
    "ChannelRegistry",
    "ChannelRunner",
    "ChannelSettingField",
    "ChannelSettingSection",
    "ChannelStatus",
    "ChatChannelAdapter",
    "CommandRegistry",
    "CommandSpec",
    "InboundMessage",
    "ManagedChannel",
    "OutboundMessage",
    "ParsedCommand",
    "TestResult",
    "get_channel_registry",
    "get_channel_status",
    "get_channel_webhook_runtime",
    "get_channel_setting_field",
    "get_default_command_registry",
    "get_registered_channel_catalog",
    "list_channel_catalog",
    "list_channel_statuses",
    "load_channel_adapter",
    "register_builtin_channel_adapters",
    "register_builtin_channels",
    "register_channel_descriptors",
    "register_channels",
    "start_all_channels",
    "start_channel",
    "start_enabled_channels",
    "stop_all_channels",
    "stop_channel",
    "test_channel_connection",
]
