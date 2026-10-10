"""Tests for the SizedLogHandler."""

import io
import logging
import unittest

from log_record_size_limiter import SizedLogHandler


class _CollectingHandler(logging.Handler):
    """Handler that stores formatted record text in a list."""

    def __init__(self, formatter=None):
        super().__init__()
        if formatter is None:
            formatter = logging.Formatter()
        self.formatter = formatter
        self.records = []

    def emit(self, record):
        self.records.append(record)


class _FailingHandler(logging.Handler):
    """Handler whose emit always raises."""

    def __init__(self):
        super().__init__()
        self.formatter = logging.Formatter()
        self.emitted = 0

    def emit(self, record):
        self.emitted += 1
        raise RuntimeError("downstream failure")


def _make_record(logger, level, msg, args=None):
    """Build a LogRecord the way logging.Logger normally does."""
    return logger.makeRecord(
        name=logger.name,
        level=level,
        fn="path/to/source.py",
        lno=10,
        msg=msg,
        args=args,
        exc_info=None,
    )


class TestSizedLogHandler(unittest.TestCase):

    def _logger_and_target(self, formatter=None):
        target = _CollectingHandler(formatter=formatter)
        logger = logging.getLogger("sized-test")
        logger.handlers = [target]
        logger.setLevel(logging.DEBUG)
        return logger, target

    def test_constructor_rejects_non_handler_target(self):
        with self.assertRaises(TypeError):
            SizedLogHandler(target="not a handler")

    def test_constructor_rejects_zero_max_bytes(self):
        target = _CollectingHandler()
        with self.assertRaises(ValueError):
            SizedLogHandler(target=target, max_bytes=0)

    def test_constructor_rejects_negative_max_bytes(self):
        target = _CollectingHandler()
        with self.assertRaises(ValueError):
            SizedLogHandler(target=target, max_bytes=-100)

    def test_constructor_rejects_bool_max_bytes(self):
        target = _CollectingHandler()
        with self.assertRaises(ValueError):
            SizedLogHandler(target=target, max_bytes=True)

    def test_constructor_accepts_custom_formatter(self):
        fmt = logging.Formatter("LVL=%(levelname)s MSG=%(message)s")
        target = _CollectingHandler()
        handler = SizedLogHandler(target=target, max_bytes=100, formatter=fmt)
        self.assertIs(handler.formatter, fmt)

    def test_constructor_inherits_target_formatter(self):
        fmt = logging.Formatter("T=%(message)s")
        target = _CollectingHandler(formatter=fmt)
        handler = SizedLogHandler(target=target, max_bytes=100)
        self.assertIs(handler.formatter, fmt)

    def test_constructor_defaults_formatter(self):
        target = _CollectingHandler()
        handler = SizedLogHandler(target=target, max_bytes=100)
        self.assertIsInstance(handler.formatter, logging.Formatter)

    def test_short_record_forwarded_unchanged(self):
        fmt = logging.Formatter("%(message)s")
        logger, target = self._logger_and_target(formatter=fmt)
        handler = SizedLogHandler(target=target, max_bytes=100, formatter=fmt)
        record = _make_record(logger, logging.INFO, "hello world")
        handler.handle(record)
        self.assertEqual(len(target.records), 1)
        self.assertEqual(record.msg, "hello world")
        self.assertEqual(handler.format(record), "hello world")

    def test_long_record_truncated_with_suffix(self):
        fmt = logging.Formatter("%(message)s")
        logger, target = self._logger_and_target(formatter=fmt)
        handler = SizedLogHandler(
            target=target, max_bytes=20, formatter=fmt,
            truncation_suffix="...",
        )
        message = "a" * 50
        record = _make_record(logger, logging.INFO, message)
        handler.handle(record)
        self.assertEqual(len(target.records), 1)
        formatted = handler.format(target.records[0])
        self.assertLessEqual(len(formatted.encode('utf-8')), 20)
        self.assertTrue(formatted.endswith("..."))

    def test_truncation_respects_byte_boundaries(self):
        fmt = logging.Formatter("%(message)s")
        logger, target = self._logger_and_target(formatter=fmt)
        handler = SizedLogHandler(
            target=target, max_bytes=10, formatter=fmt,
            truncation_suffix="",
        )
        # 'á' is 2 bytes in UTF-8; 4 chars = 8 bytes, adding a 5th would
        # reach 10 bytes exactly, which still fits.
        message = "á" * 4 + "á"
        record = _make_record(logger, logging.INFO, message)
        handler.handle(record)
        formatted = handler.format(target.records[0])
        self.assertEqual(len(formatted.encode('utf-8')), 10)

    def test_multibyte_record_truncated_within_budget(self):
        fmt = logging.Formatter("%(message)s")
        logger, target = self._logger_and_target(formatter=fmt)
        handler = SizedLogHandler(
            target=target, max_bytes=10, formatter=fmt,
            truncation_suffix="...",
        )
        # 'á' is 2 bytes each; 5 of them = 10 bytes, which is at the limit
        # without truncation. Add one more to force truncation.
        message = "á" * 6
        record = _make_record(logger, logging.INFO, message)
        handler.handle(record)
        formatted = handler.format(target.records[0])
        self.assertLessEqual(len(formatted.encode('utf-8')), 10)
        self.assertTrue(formatted.endswith("..."))

    def test_getmessage_args_truncated_correctly(self):
        fmt = logging.Formatter("%(message)s")
        logger, target = self._logger_and_target(formatter=fmt)
        handler = SizedLogHandler(
            target=target, max_bytes=20, formatter=fmt,
            truncation_suffix="...",
        )
        base = "x" * 30
        record = _make_record(logger, logging.INFO, "repeated %s", args=(base,))
        handler.handle(record)
        forwarded = target.records[0]
        formatted = handler.format(forwarded)
        self.assertLessEqual(len(formatted.encode('utf-8')), 20)
        self.assertTrue(formatted.endswith("..."))
        self.assertEqual(forwarded.args, None)

    def test_exactly_at_limit_not_truncated(self):
        fmt = logging.Formatter("%(message)s")
        logger, target = self._logger_and_target(formatter=fmt)
        handler = SizedLogHandler(target=target, max_bytes=10, formatter=fmt)
        record = _make_record(logger, logging.INFO, "a" * 10)
        handler.handle(record)
        self.assertEqual(record.msg, "a" * 10)
        self.assertEqual(handler.format(record), "a" * 10)

    def test_exception_text_preserved(self):
        fmt = logging.Formatter("%(message)s")
        logger, target = self._logger_and_target(formatter=fmt)
        handler = SizedLogHandler(
            target=target, max_bytes=60, formatter=fmt,
            truncation_suffix="...",
        )
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            exc_info = sys.exc_info()
        record = _make_record(logger, logging.ERROR, "x" * 200)
        record.exc_info = exc_info
        handler.handle(record)
        forwarded = target.records[0]
        # The message should be truncated, but exc_info should be preserved
        # exactly as it was passed in.
        self.assertIs(forwarded.exc_info, exc_info)
        self.assertTrue(forwarded.getMessage().endswith("..."))

    def test_empty_message_within_budget(self):
        fmt = logging.Formatter("PREFIX %(message)s")
        logger, target = self._logger_and_target(formatter=fmt)
        handler = SizedLogHandler(target=target, max_bytes=100, formatter=fmt)
        record = _make_record(logger, logging.INFO, "")
        handler.handle(record)
        self.assertEqual(len(target.records), 1)
        self.assertEqual(record.msg, "")
        self.assertEqual(handler.format(record), "PREFIX ")

    def test_wrapped_property_exposes_target_handle(self):
        target = _CollectingHandler()
        handler = SizedLogHandler(target=target, max_bytes=100)
        self.assertEqual(handler.__wrapped__, target.handle)

    def test_downstream_failure_propagates(self):
        logger = logging.getLogger("sized-fail")
        target = _FailingHandler()
        handler = SizedLogHandler(target=target, max_bytes=100)
        record = _make_record(logger, logging.INFO, "hello")
        with self.assertRaises(RuntimeError):
            handler.handle(record)
        self.assertEqual(target.emitted, 1)

    def test_suffix_bytes_counted_against_budget(self):
        fmt = logging.Formatter("%(message)s")
        logger, target = self._logger_and_target(formatter=fmt)
        # max_bytes=10, suffix="[T]" (4 bytes including nothing extra)
        # leaves 6 bytes for message content.
        handler = SizedLogHandler(
            target=target, max_bytes=10, formatter=fmt,
            truncation_suffix="[T]",
        )
        record = _make_record(logger, logging.INFO, "a" * 50)
        handler.handle(record)
        formatted = handler.format(target.records[0])
        self.assertLessEqual(len(formatted.encode('utf-8')), 10)
        self.assertTrue(formatted.endswith("[T]"))
        # 10 byte budget, 3 byte suffix => 7 byte message prefix.
        self.assertEqual(formatted, "aaaaaaa[T]")


if __name__ == "__main__":
    unittest.main()
