from dataclasses import dataclass

from agent.modules.channels.contracts import ChatChannelAdapter
from agent.modules.channels.contracts import ChannelSettingField, ChannelSettingSection
from agent.shared.integrations import IntegrationDescriptor


@dataclass(frozen=True, slots=True)
class ChannelDescriptor(IntegrationDescriptor):
    adapter_loader: str = ""
    has_runner: bool = False


TELEGRAM_SETTINGS_SECTIONS = (
    ChannelSettingSection(
        id="authentication",
        title="Authentication",
        subtitle="Bot credentials from @BotFather",
    ),
    ChannelSettingSection(
        id="agents",
        title="Agents",
        subtitle="Default agent and command routing",
        default_collapsed=True,
    ),
    ChannelSettingSection(
        id="webhook",
        title="Update Mode",
        subtitle="Polling or webhook delivery",
        default_collapsed=True,
    ),
)

TELEGRAM_SETTINGS_SCHEMA = (
    ChannelSettingField(
        name="enabled",
        label="Telegram Enabled",
        description="Enable Telegram channel integration",
        input_type="boolean",
        section="authentication",
        default=True,
    ),
    ChannelSettingField(
        name="bot_token",
        label="Telegram Bot Token",
        description="Telegram bot token from @BotFather",
        input_type="password",
        required=True,
        secret=True,
        section="authentication",
    ),
    ChannelSettingField(
        name="default_agent",
        label="Telegram Default Agent",
        description="Default agent for Telegram messages",
        section="agents",
    ),
    ChannelSettingField(
        name="code_agent",
        label="Telegram Code Agent",
        description="Agent triggered by /code command",
        section="agents",
    ),
    ChannelSettingField(
        name="research_agent",
        label="Telegram Research Agent",
        description="Agent triggered by /research command",
        section="agents",
    ),
    ChannelSettingField(
        name="update_mode",
        label="Telegram Update Mode",
        description="Telegram update mode: polling or webhook",
        section="webhook",
        default="polling",
    ),
    ChannelSettingField(
        name="webhook_url",
        label="Telegram Webhook URL",
        description="Public HTTPS endpoint for Telegram webhook mode",
        input_type="url",
        section="webhook",
    ),
    ChannelSettingField(
        name="webhook_secret",
        label="Telegram Webhook Secret",
        description="Secret token checked against Telegram webhook requests",
        input_type="password",
        secret=True,
        section="webhook",
    ),
)


DISCORD_SETTINGS_SECTIONS = (
    ChannelSettingSection(
        id="authentication",
        title="Authentication",
        subtitle="Bot token from Discord Developer Portal",
    ),
    ChannelSettingSection(
        id="agents",
        title="Agents",
        subtitle="Default agent and command routing",
        default_collapsed=True,
    ),
)

DISCORD_SETTINGS_SCHEMA = (
    ChannelSettingField(
        name="enabled",
        label="Discord Enabled",
        description="Enable Discord channel integration",
        input_type="boolean",
        section="authentication",
        default=True,
    ),
    ChannelSettingField(
        name="bot_token",
        label="Discord Bot Token",
        description="Discord bot token from Developer Portal",
        input_type="password",
        required=True,
        secret=True,
        section="authentication",
    ),
    ChannelSettingField(
        name="default_agent",
        label="Discord Default Agent",
        description="Default agent for Discord messages",
        section="agents",
    ),
    ChannelSettingField(
        name="code_agent",
        label="Discord Code Agent",
        description="Agent triggered by /code command",
        section="agents",
    ),
    ChannelSettingField(
        name="research_agent",
        label="Discord Research Agent",
        description="Agent triggered by /research command",
        section="agents",
    ),
)


BUILTIN_CHANNEL_DESCRIPTORS = (
    ChannelDescriptor(
        kind="channel",
        name="telegram",
        title="Telegram",
        summary="Chat with your agents from Telegram private chats.",
        tagline="Bot platform",
        config_prefix="channels.telegram",
        loader="agent.modules.channels.telegram.adapter:get_telegram_adapter",
        adapter_loader="agent.modules.channels.telegram.adapter:get_telegram_adapter",
        capabilities=frozenset({"chat", "outbound", "streaming", "command_suggestions", "private_only"}),
        dependency_imports=("aiogram",),
        install_extra="channel-telegram",
        settings_schema=TELEGRAM_SETTINGS_SCHEMA,
        settings_sections=TELEGRAM_SETTINGS_SECTIONS,
        has_runner=True,
    ),
    ChannelDescriptor(
        kind="channel",
        name="discord",
        title="Discord",
        summary="Run agents inside Discord servers and DMs.",
        tagline="Bot platform",
        config_prefix="channels.discord",
        loader="agent.modules.channels.discord.adapter:get_discord_adapter",
        adapter_loader="agent.modules.channels.discord.adapter:get_discord_adapter",
        capabilities=frozenset({"chat", "outbound", "streaming"}),
        dependency_imports=("discord",),
        install_extra="channel-discord",
        settings_schema=DISCORD_SETTINGS_SCHEMA,
        settings_sections=DISCORD_SETTINGS_SECTIONS,
        has_runner=True,
    ),
)

BUILTIN_CHANNEL_SPECS = BUILTIN_CHANNEL_DESCRIPTORS


def load_channel_adapter(descriptor: ChannelDescriptor) -> ChatChannelAdapter:
    from agent.modules.channels.registry import get_channel_registry

    return get_channel_registry().load_adapter(descriptor.name)
