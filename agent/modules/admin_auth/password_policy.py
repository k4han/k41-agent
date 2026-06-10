"""Password policy validation for admin authentication."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class PasswordRequirement:
    """A single password requirement with check function and error message."""

    name: str
    check: callable
    message: str


class PasswordPolicy:
    """Password policy validator with configurable requirements."""

    def __init__(
        self,
        min_length: int = 8,
        require_uppercase: bool = True,
        require_lowercase: bool = True,
        require_digit: bool = True,
        require_special: bool = True,
    ):
        self.min_length = min_length
        self.requirements: list[PasswordRequirement] = []

        # Minimum length requirement
        self.requirements.append(
            PasswordRequirement(
                name="length",
                check=lambda pwd: len(pwd) >= min_length,
                message=f"Password must be at least {min_length} characters long",
            )
        )

        # Uppercase letter requirement
        if require_uppercase:
            self.requirements.append(
                PasswordRequirement(
                    name="uppercase",
                    check=lambda pwd: bool(re.search(r"[A-Z]", pwd)),
                    message="Password must contain at least one uppercase letter (A-Z)",
                )
            )

        # Lowercase letter requirement
        if require_lowercase:
            self.requirements.append(
                PasswordRequirement(
                    name="lowercase",
                    check=lambda pwd: bool(re.search(r"[a-z]", pwd)),
                    message="Password must contain at least one lowercase letter (a-z)",
                )
            )

        # Digit requirement
        if require_digit:
            self.requirements.append(
                PasswordRequirement(
                    name="digit",
                    check=lambda pwd: bool(re.search(r"\d", pwd)),
                    message="Password must contain at least one digit (0-9)",
                )
            )

        # Special character requirement
        if require_special:
            self.requirements.append(
                PasswordRequirement(
                    name="special",
                    check=lambda pwd: bool(re.search(r'[!@#$%^&*(),.?":{}|<>_\-+=\[\]\\\/~`]', pwd)),
                    message="Password must contain at least one special character (!@#$%^&*...)",
                )
            )

    def validate(self, password: str) -> tuple[bool, list[str]]:
        """
        Validate password against all requirements.

        Returns:
            (is_valid, error_messages) - is_valid is True if all requirements pass,
            error_messages contains list of requirement failures.
        """
        if not password:
            return False, ["Password cannot be empty"]

        errors = []
        for requirement in self.requirements:
            if not requirement.check(password):
                errors.append(requirement.message)

        return len(errors) == 0, errors

    def validate_or_raise(self, password: str) -> None:
        """
        Validate password and raise ValueError if invalid.

        Raises:
            ValueError: If password does not meet requirements, with all error messages.
        """
        is_valid, errors = self.validate(password)
        if not is_valid:
            raise ValueError(
                "Password does not meet security requirements:\n" + "\n".join(f"- {err}" for err in errors)
            )


# Default password policy instance
_default_policy: PasswordPolicy | None = None


def get_password_policy() -> PasswordPolicy:
    """Get the default password policy instance."""
    global _default_policy
    if _default_policy is None:
        _default_policy = PasswordPolicy(
            min_length=8,
            require_uppercase=True,
            require_lowercase=True,
            require_digit=True,
            require_special=True,
        )
    return _default_policy


def validate_password(password: str) -> tuple[bool, list[str]]:
    """
    Validate password against default policy.

    Returns:
        (is_valid, error_messages)
    """
    return get_password_policy().validate(password)


def validate_password_or_raise(password: str) -> None:
    """
    Validate password against default policy and raise ValueError if invalid.

    Raises:
        ValueError: If password does not meet requirements.
    """
    get_password_policy().validate_or_raise(password)
