"""Leeswaarnemingen mogen geen afgeleide instelling of herstelactie opslaan."""

from contextvars import ContextVar

observation_read = ContextVar("observation_read", default=False)
