# log-record-size-limiter

A `logging.Handler` wrapper that measures each record's serialized byte size and truncates the message field when the record exceeds a configured limit, before forwarding to a downstream handler.

## Usage

```python
import logging
from log_record_size_limiter import SizedLogHandler

target = logging.StreamHandler()
target.setFormatter(logging.Formatter("%(levelname)s %(message)s"))

handler = SizedLogHandler(target=target, max_bytes=512)
handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))

logger = logging.getLogger("bounded")
logger.addHandler(handler)
logger.setLevel(logging.INFO)

logger.info("short message")        # forwarded unchanged
logger.info("x" * 10_000)            # message truncated to fit 512 bytes
```

Exported names:

- `SizedLogHandler` — the handler class, in `log_record_size_limiter`.

## Why

Downstream log consumers (network shippers, fixed-size log buffers, systems with hard MTU-like limits) choke on oversized records. This handler caps each formatted record at a byte budget by truncating only the message field. Non-message fields (logger name, level, exception text, args) are preserved, because the message is usually the cheapest thing to shorten and the most likely to be the bulky one.

The trade-off: if the bulky part of a record is NOT the message (a giant exception traceback, for instance), this handler will truncate the message and the record may still exceed the limit. We do not attempt to shrink other fields. Choose a `max_bytes` large enough for your format's non-message overhead.

## Edge case you will hit

The limit is measured in UTF-8 bytes, not characters. A 100-character string containing multibyte codepoints is more than 100 bytes. Truncation therefore slices on byte boundaries: the longest UTF-8-valid prefix that fits is kept. If the truncation suffix itself cannot fit within the budget, you get an empty message plus the suffix (or an empty message if even the suffix does not fit — but the default suffix is short enough that this only happens with `max_bytes` near zero, which the constructor rejects).
