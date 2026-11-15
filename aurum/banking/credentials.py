# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                               Banking - Credentials                                                  #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Store Redbark API keys in the macOS login Keychain without files, shell arguments or extra dependencies.

Each workspace has a separate generic-password item. Native Security.framework performs access checks;
unavailable or denied Keychain access never falls back to plaintext storage.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections.abc import Iterator
from contextlib import contextmanager
import ctypes
import fcntl
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import stat
import sys

SERVICE = b"Aurum Redbark"
NOT_FOUND = -25300


# %% 1. Workspace Credentials --------------------------------------------------------------------------------
@contextmanager
def bank_connection_lock(workspace: Path) -> Iterator[None]:
    """
    Serialize a workspace's Keychain and database connection changes across windows and processes.

    Keep the empty lock file in place after release so waiting processes always reference the same inode.
    Refuse symbolic links and foreign-owned files; no secret is written to this owner-only lock file.
    """
    directory = Path(workspace).resolve() / "data"
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = None
    acquired = False
    try:
        try:
            descriptor = os.open(
                directory / ".redbark.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
            )
        except OSError:
            raise RuntimeError("Aurum could not safely open the bank connection lock.") from None
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError("The bank connection lock must be a regular file owned by this user.")
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "Another Aurum window is changing this bank connection. Try again shortly.",
            ) from None
        acquired = True
        yield
    finally:
        if descriptor is not None:
            try:
                if acquired:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def get_key(workspace: Path) -> str | None:
    """
    Read this workspace's key, returning None only when its Keychain item does not exist.
    """
    security, core = _frameworks()
    account = _account(workspace)
    length, content, item = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_void_p()
    status = security.SecKeychainFindGenericPassword(
        None, len(SERVICE), SERVICE, len(account), account,
        ctypes.byref(length), ctypes.byref(content), ctypes.byref(item),
    )
    try:
        if status == NOT_FOUND:
            return None
        _check_status(status)
        try:
            key = ctypes.string_at(content, length.value).decode("utf-8")
        except UnicodeError:
            raise RuntimeError(
                "The saved Redbark key is unreadable. Reconnect in Setup → Accounts.",
            ) from None
        if not key:
            raise RuntimeError("The saved Redbark key is empty. Reconnect in Setup → Accounts.")
        return key
    finally:
        if content:
            security.SecKeychainItemFreeContent(None, content)
        if item:
            core.CFRelease(item)


def has_key(workspace: Path) -> bool:
    """
    Check whether a credential item exists without retrieving or displaying its secret data.
    """
    security, core = _frameworks()
    status, item = _find_item(security, workspace)
    try:
        if status == NOT_FOUND:
            return False
        _check_status(status)
        return True
    finally:
        if item:
            core.CFRelease(item)


def save_key(workspace: Path, key: str) -> None:
    """
    Add or replace this workspace's Keychain password after an explicit connection save.
    """
    if not isinstance(key, str) or not key or not key.isascii() or len(key) > 4096 or any(
        ord(char) <= 32 for char in key
    ):
        raise ValueError("Enter a valid Redbark API key.")
    security, core = _frameworks()
    account, encoded = _account(workspace), key.encode("utf-8")
    status, item = _find_item(security, workspace)
    try:
        if status == NOT_FOUND:
            status = security.SecKeychainAddGenericPassword(
                None, len(SERVICE), SERVICE, len(account), account, len(encoded), encoded, None,
            )
        else:
            _check_status(status)
            status = security.SecKeychainItemModifyAttributesAndData(item, None, len(encoded), encoded)
        _check_status(status)
    finally:
        if item:
            core.CFRelease(item)


def delete_key(workspace: Path) -> None:
    """
    Remove Aurum's local credential while retaining bank records and the remote Redbark connection.
    """
    security, core = _frameworks()
    status, item = _find_item(security, workspace)
    try:
        if status == NOT_FOUND:
            return
        _check_status(status)
        _check_status(security.SecKeychainItemDelete(item))
    finally:
        if item:
            core.CFRelease(item)


# %% 2. Helpers ----------------------------------------------------------------------------------------------
def _account(workspace: Path) -> bytes:
    """
    Derive a stable, non-secret workspace identifier without exposing its path in the Keychain label.
    """
    return hashlib.sha256(str(Path(workspace).resolve()).encode("utf-8")).hexdigest().encode("ascii")


def _find_item(security, workspace: Path) -> tuple[int, ctypes.c_void_p]:
    """
    Obtain an item reference without requesting its password contents.
    """
    account, item = _account(workspace), ctypes.c_void_p()
    status = security.SecKeychainFindGenericPassword(
        None, len(SERVICE), SERVICE, len(account), account, None, None, ctypes.byref(item),
    )
    return status, item


def _check_status(status: int) -> None:
    """
    Give actionable local errors without asking Security.framework to include credential details.
    """
    if status == 0:
        return
    if status in (-128, -25293, -25308):
        raise RuntimeError("Allow Aurum to access its Redbark item in macOS Keychain, then try again.")
    raise RuntimeError(f"macOS Keychain could not complete the Redbark action (code {status}).")


@lru_cache(maxsize=1)
def _frameworks():
    """
    Load and type the small native Keychain interface only when a credential action is requested.
    """
    if sys.platform != "darwin":
        raise RuntimeError("Redbark credential storage currently requires macOS Keychain.")
    security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
    core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    pointer, uint32, string = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p
    security.SecKeychainFindGenericPassword.argtypes = [
        pointer, uint32, string, uint32, string, ctypes.POINTER(uint32),
        ctypes.POINTER(pointer), ctypes.POINTER(pointer),
    ]
    security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
    security.SecKeychainAddGenericPassword.argtypes = [
        pointer, uint32, string, uint32, string, uint32, pointer, ctypes.POINTER(pointer),
    ]
    security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
    security.SecKeychainItemModifyAttributesAndData.argtypes = [pointer, pointer, uint32, pointer]
    security.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
    security.SecKeychainItemDelete.argtypes = [pointer]
    security.SecKeychainItemDelete.restype = ctypes.c_int32
    security.SecKeychainItemFreeContent.argtypes = [pointer, pointer]
    security.SecKeychainItemFreeContent.restype = ctypes.c_int32
    core.CFRelease.argtypes = [pointer]
    core.CFRelease.restype = None
    return security, core
