import enum


class CaseStatus(str, enum.Enum):
    """Status of a revenue risk case."""
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RECOVERY_ATTEMPTED = "RECOVERY_ATTEMPTED"
    RECOVERED = "RECOVERED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"
    CLOSED = "CLOSED"


class CasePriority(str, enum.Enum):
    """Priority level for revenue risk cases."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class TransactionStatus(str, enum.Enum):
    """Status of a payment transaction."""
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    CANCELLED = "CANCELLED"


class RecoveryActionType(str, enum.Enum):
    """Types of recovery actions that can be taken."""
    NO_ACTION = "NO_ACTION"
    RETRY = "RETRY"
    CREATE_PAYMENT_LINK = "CREATE_PAYMENT_LINK"
    SEND_PAYMENT_REMINDER = "SEND_PAYMENT_REMINDER"
    WAIT_AND_RETRY = "WAIT_AND_RETRY"
    ESCALATE_TO_HUMAN = "ESCALATE_TO_HUMAN"
    MARK_UNRECOVERABLE = "MARK_UNRECOVERABLE"


class RecoveryOutcome(str, enum.Enum):
    """Outcome of a recovery action."""
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PENDING = "PENDING"
    BLOCKED_BY_POLICY = "BLOCKED_BY_POLICY"
    ESCALATED = "ESCALATED"
    EXPIRED = "EXPIRED"


class AuditEventType(str, enum.Enum):
    """Types of audit events."""
    CASE_CREATED = "CASE_CREATED"
    CASE_UPDATED = "CASE_UPDATED"
    RISK_ASSESSED = "RISK_ASSESSED"
    DIAGNOSIS_COMPLETED = "DIAGNOSIS_COMPLETED"
    ACTION_RECOMMENDED = "ACTION_RECOMMENDED"
    POLICY_CHECKED = "POLICY_CHECKED"
    ACTION_EXECUTED = "ACTION_EXECUTED"
    ACTION_FAILED = "ACTION_FAILED"
    CASE_ESCALATED = "CASE_ESCALATED"
    CASE_CLOSED = "CASE_CLOSED"
