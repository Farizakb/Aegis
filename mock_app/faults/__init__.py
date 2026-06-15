from mock_app.faults.base import Fault
from mock_app.faults.db_deadlock import DbDeadlockFault
from mock_app.faults.error_spike import ErrorSpikeFault
from mock_app.faults.memory_leak import MemoryLeakFault
from stream.schema import FaultKind

FAULTS: dict[FaultKind, Fault] = {
    FaultKind.memory_leak: MemoryLeakFault(),
    FaultKind.db_deadlock: DbDeadlockFault(),
    FaultKind.error_spike: ErrorSpikeFault(),
}

__all__ = ["FAULTS", "Fault", "MemoryLeakFault", "DbDeadlockFault", "ErrorSpikeFault"]
