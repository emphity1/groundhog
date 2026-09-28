# SPDX-License-Identifier: Apache-2.0
"""Detection over a stream of samples: ``python -m groundhog.detect --config FILE``.

Reads the replay's JSON lines on stdin and writes a run header, then events, as
JSON lines on stdout (docs/adr/0006-event-stream.md).
"""
