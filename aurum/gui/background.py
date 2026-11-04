# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  GUI - Background                                                    #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Keep the desktop responsive while a single Aurum operation runs in a worker thread.

Workers publish plain progress messages and results through Qt signals. They own their database connections
inside the supplied operation. Cancellation is cooperative: completed events stay saved and the operation
stops at its next safe boundary instead of terminating a thread during a database write.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections.abc import Callable
from threading import Event
import sqlite3

from PySide6.QtCore import QObject, QRunnable, Signal, Slot
from pydantic import ValidationError

# Aurum imports
from aurum.ai.chatgpt import ChatGPTError


# %% 1. Background Operations --------------------------------------------------------------------------------
class JobSignals(QObject):
    """
    Carry worker updates to the main thread, where Qt widgets can safely receive them.
    """

    progress = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()


class Job(QRunnable):
    """
    Execute one operation with progress reporting and a cooperative stop flag.
    """

    def __init__(self, operation: Callable) -> None:
        """
        Retain the operation until the thread pool finishes it.
        """
        super().__init__()
        self.operation = operation
        self.signals = JobSignals()
        self.stop_requested = Event()

    def stop(self) -> None:
        """
        Ask the operation to stop at its next safe boundary.
        """
        self.stop_requested.set()

    @Slot()
    def run(self) -> None:
        """
        Publish the operation's result or a safe error without displaying private validation input.
        """
        try:
            result = self.operation(self.signals.progress.emit, self.stop_requested.is_set)
        except Exception as error:
            self.signals.failed.emit(error_message(error))
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit()


# %% 2. Helpers ----------------------------------------------------------------------------------------------
def error_message(error: Exception) -> str:
    """
    Show actionable expected errors while keeping credentials and raw validation values out of diagnostics.
    """
    if isinstance(error, ValidationError):
        return "\n".join(
            f"{'.'.join(map(str, item['loc'])) or 'Input'}: {item['msg']}"
            for item in error.errors(include_input=False, include_url=False)[:8]
        )
    if isinstance(error, (ChatGPTError, ValueError, OSError, RuntimeError, sqlite3.Error)):
        return str(error)
    return f"{type(error).__name__}: the operation could not finish. Saved work is retained."
