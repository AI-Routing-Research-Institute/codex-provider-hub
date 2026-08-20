"""Backward-compatible Claude names for the shared curl transport."""

from .curl import CurlClient, CurlRequest, CurlResponse

ClaudeCurlRequest = CurlRequest
ClaudeCurlResponse = CurlResponse
ClaudeCurlClient = CurlClient
