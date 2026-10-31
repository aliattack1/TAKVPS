"""Typed errors with friendly, user-facing messages."""
from __future__ import annotations

import asyncio

import asyncssh


class TakError(Exception):
    """Base error. `message` is safe to show to the user."""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class UnreachableError(TakError): ...
class AuthError(TakError): ...
class HostKeyRejected(TakError): ...
class HostKeyChanged(TakError): ...
class NotConnectedError(TakError): ...
class CommandTimeout(TakError): ...
class RemotePermissionError(TakError): ...
class SftpError(TakError): ...


def translate(exc: BaseException) -> TakError:
    """Map low-level exceptions to a TakError. Never includes secrets."""
    if isinstance(exc, TakError):
        return exc
    if isinstance(exc, asyncssh.PermissionDenied):
        return AuthError("Authentication failed. Check username, password or key.")
    if isinstance(exc, asyncssh.HostKeyNotVerifiable):
        return HostKeyChanged("The server's host key does not match the trusted one.")
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return CommandTimeout("The operation timed out.")
    if isinstance(exc, asyncssh.SFTPPermissionDenied):
        return RemotePermissionError("Permission denied on the server.")
    if isinstance(exc, asyncssh.SFTPNoSuchFile):
        return SftpError("That file or folder does not exist.")
    if isinstance(exc, (asyncssh.ConnectionLost, asyncssh.DisconnectError, BrokenPipeError)):
        return NotConnectedError("The connection to the server was lost.")
    if isinstance(exc, asyncssh.KeyImportError):
        return AuthError("Could not read the private key (wrong passphrase or format).")
    if isinstance(exc, OSError):
        return UnreachableError("Cannot reach the server. Check host, port and network.")
    return TakError("Something went wrong.", detail=type(exc).__name__)
