from enum import Enum


class Platform(str, Enum):
    DISCORD = "discord"
    TELEGRAM = "telegram"
    API = "api"
