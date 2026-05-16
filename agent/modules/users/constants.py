from enum import Enum


class Platform(str, Enum):
    DISCORD = "discord"
    GITHUB = "github"
    TELEGRAM = "telegram"
    API = "api"
