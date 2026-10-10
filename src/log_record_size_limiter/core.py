"""Core handler that truncates oversized log records before forwarding."""

import logging


def _byte_len(s):
    """Number of bytes when *s* is encoded as UTF-8.

    This is the same metric used by the size limit, so every comparison
    in the module is byte-counted rather than character-counted.
    """
    return len(s.encode('utf-8', errors='replace'))


def _slice_to_bytes(s, max_bytes):
    """Return the longest UTF-8 prefix of *s* that is <= *max_bytes* bytes.

    Because UTF-8 encodes each character as 1-4 bytes, slicing on a character
    index would either overshoot (too many bytes) or undershoot (wasted
    space). We advance character-by-character until adding the next character
    would exceed the budget. Linear in the character count, which is fine for
    the truncated text sizes this handler is meant to produce.
    """
    if max_bytes <= 0:
        return ""
    prefix = ""
    used = 0
    for ch in s:
        ch_bytes = _byte_len(ch)
        if used + ch_bytes > max_bytes:
            break
        prefix += ch
        used += ch_bytes
    return prefix


def _make_handler_call(target):
    """Return a callable that wraps *target*'s handle method.

    This exists so the returned callable only captures one attribute,
    making the emitted bound-method in the public ``__wrapped__`` slot
    stable and easy to inspect.
    """
    return target.handle


class SizedLogHandler(logging.Handler):
    """Logging handler that limits the serialized size of each record.

    A handler wrapper that measures the serialized bytes of each log record
    (via ``logging.Formatter``) and, if that measurement exceeds a byte
    budget, truncates the record's message field so the re-serialized
    form fits. Records are then forwarded to a downstream handler.

    Choice made: the limit is on the formatted output bytes, measured after
    UTF-8 encoding. Truncation is applied ONLY to ``LogRecord.getMessage()``
    output. Other fields (logger name, level name, exception text, args,
    timestamps) are left untouched. The truncation suffix is always appended
    when truncation occurs, and its bytes count against the budget.

    The use case is bounded downstream consumers (network log shippers, log
    buffers with hard MTU-like limits). The trade-off is fidelity: large
    exception tracebacks are preserved at the cost of the message; if the
    message is not the bulky field, truncating it may not be enough and the
    record will still exceed the limit. We do not attempt to be clever about
    that — the caller chooses a limit large enough for their format's
    non-message overhead.
    """

    def __init__(self, target, max_bytes=4096, formatter=None,
                 truncation_suffix='...[truncated]'):
        """Construct the handler.

        Args:
            target: downstream ``logging.Handler``; must be non-None.
            max_bytes: byte budget for the formatted record, including the
                truncation suffix when truncation is triggered. Must be > 0.
            formatter: optional ``logging.Formatter``; defaults to the
                target's formatter (or ``logging.Formatter()`` if the target
                has none).
            truncation_suffix: string appended to truncated messages; its
                bytes are counted against ``max_bytes``.

        Raises:
            TypeError: if *target* is not a logging.Handler.
            ValueError: if *max_bytes* is not a positive int.
        """
        super().__init__()
        if not isinstance(target, logging.Handler):
            raise TypeError(
                "target must be a logging.Handler, got %r" % type(target).__name__
            )
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool):
            raise ValueError("max_bytes must be an int")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be > 0")

        self.target = target
        self.max_bytes = max_bytes
        self.truncation_suffix = truncation_suffix

        if formatter is None:
            target_formatter = getattr(target, 'formatter', None)
            if target_formatter is not None:
                formatter = target_formatter
            else:
                formatter = logging.Formatter()
        self.formatter = formatter

        self._emit = _make_handler_call(target)

    @property
    def __wrapped__(self):
        """Expose the underlying handler's handle method for introspection."""
        return self._emit

    def _format_bytes(self, record):
        """Return the formatted record as a UTF-8 byte string."""
        return self.format(record).encode('utf-8', errors='replace')

    def _measure_without_message(self, record):
        """Measure formatted bytes with the message replaced by the empty string.

        Because we only truncate the message field, every other part of the
        formatted output is fixed for a given record. Measuring once with an
        empty message tells us how much non-message overhead this record
        contributes, so we can compute the exact byte budget available for
        the message itself.
        """
        saved = record.msg, record.args
        record.msg = ""
        record.args = None
        try:
            return self._format_bytes(record)
        finally:
            record.msg, record.args = saved

    def _truncate_message(self, record, available_bytes):
        """Replace ``record.msg`` with a truncated version in place.

        ``available_bytes`` is the byte budget for the message portion of
        the formatted output. We slice the current message to fit, leaving
        room for the truncation suffix.
        """
        suffix = self.truncation_suffix
        suffix_bytes = _byte_len(suffix)
        budget = available_bytes - suffix_bytes
        if budget < 0:
            budget = 0
        msg = record.getMessage()
        truncated = _slice_to_bytes(msg, budget)
        record.msg = truncated + suffix
        record.args = None

    def _within_budget(self, record):
        """True if the formatted record currently fits within the byte budget."""
        return _byte_len(self.format(record)) <= self.max_bytes

    def _handle_within_budget(self, record):
        """Forward *record* without modification."""
        self._emit(record)

    def _truncate_and_forward(self, record):
        """Truncate the record's message in place, then forward."""
        overhead = self._measure_without_message(record)
        available = self.max_bytes - len(overhead)
        if available < 0:
            available = 0
        self._truncate_message(record, available)
        self._emit(record)

    def handle(self, record):
        """Truncate the message if needed, then forward to the target handler."""
        if self._within_budget(record):
            self._handle_within_budget(record)
        else:
            self._truncate_and_forward(record)
