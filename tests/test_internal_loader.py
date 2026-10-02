"""Tests for the generic internal provider extension loader."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agent.modules.providers.internal_loader import load_internal_providers
from agent.modules.providers.service import ProviderService


def test_load_internal_providers_handles_missing_module():
    mock_service = MagicMock(spec=ProviderService)
    # Simulate ModuleNotFoundError when importing internal candidates
    with patch("importlib.import_module", side_effect=ModuleNotFoundError("No module named 'agent.internal'")):
        # Should not raise any error
        load_internal_providers(mock_service)


def test_load_internal_providers_invokes_register():
    mock_service = MagicMock(spec=ProviderService)
    mock_module = MagicMock()
    mock_register = MagicMock()
    mock_module.register = mock_register

    with patch("importlib.import_module", return_value=mock_module):
        load_internal_providers(mock_service, container=None)
        mock_register.assert_called_once_with(mock_service, container=None)
