#!/usr/bin/env python3
"""Decode World Cup 1998 binary access logs (packed 20-byte records).

OS/arch assumptions: macOS (darwin) or Linux, Python 3.14+ (repo venv).
Does not use HP vendor decode tools — only the ITA-published struct:

  struct request {
    uint32_t timestamp;
    uint32_t clientID;
    uint32_t objectID;
    uint32_t size;
    uint8_t method;
    uint8_t status;
    uint8_t type;
    uint8_t server;
  };

Packed size: 4*4 + 4*1 = 20 bytes, big-endian (>IIIIBBBB).
"""

from __future__ import annotations

import argparse
import gzip
import struct
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator

RECORD_SIZE = 20
RECORD_STRUCT = struct.Struct(">IIIIBBBB")

# ITA collection bounds (GMT epoch seconds, inclusive).
TS_MIN = int(datetime(1998, 4, 30, 0, 0, 0, tzinfo=timezone.utc).timestamp())
TS_MAX = int(datetime(1998, 7, 26, 23, 59, 59, tzinfo=timezone.utc).timestamp())


@dataclass(frozen=True)
class WC98Record:
    timestamp: int
    client_id: int
    object_id: int
    size: int
    method: int
    status: int
    type: int
    server: int


def decode_record(chunk: bytes) -> WC98Record:
    if len(chunk) != RECORD_SIZE:
        raise ValueError(f"WC98_RECORD_CHUNK_SIZE expected={RECORD_SIZE} got={len(chunk)}")
    ts, client_id, object_id, size, method, status, typ, server = RECORD_STRUCT.unpack(chunk)
    return WC98Record(
        timestamp=ts,
        client_id=client_id,
        object_id=object_id,
        size=size,
        method=method,
        status=status,
        type=typ,
        server=server,
    )


def iter_records_from_stream(stream: BinaryIO) -> Iterator[WC98Record]:
    while True:
        chunk = stream.read(RECORD_SIZE)
        if not chunk:
            return
        if len(chunk) != RECORD_SIZE:
            raise RuntimeError(
                f"WC98_RECORD_SIZE_MISMATCH file_size_partial remainder={len(chunk)} "
                f"record_size={RECORD_SIZE}"
            )
        yield decode_record(chunk)


def read_log_bytes(path: Path) -> bytes:
    if path.suffix == ".gz" or path.name.endswith(".gz"):
        with gzip.open(path, "rb") as handle:
            return handle.read()
    return path.read_bytes()


def decode_bytes(data: bytes) -> list[WC98Record]:
    file_size = len(data)
    remainder = file_size % RECORD_SIZE
    if remainder != 0:
        raise RuntimeError(
            f"WC98_RECORD_SIZE_MISMATCH file_size={file_size} record_size={RECORD_SIZE} "
            f"remainder={remainder}"
        )
    expected_count = file_size // RECORD_SIZE
    records: list[WC98Record] = []
    for index in range(expected_count):
        offset = index * RECORD_SIZE
        records.append(decode_record(data[offset : offset + RECORD_SIZE]))
    if len(records) != expected_count:
        raise RuntimeError(
            f"WC98_RECORD_COUNT_MISMATCH decoded={len(records)} expected={expected_count}"
        )
    return records


def validate_records(records: list[WC98Record], file_size: int) -> None:
    expected_count = file_size // RECORD_SIZE
    if len(records) != expected_count:
        raise RuntimeError(
            f"WC98_RECORD_COUNT_MISMATCH decoded={len(records)} expected={expected_count}"
        )
    previous_ts: int | None = None
    for index, record in enumerate(records):
        if previous_ts is not None and record.timestamp < previous_ts:
            raise RuntimeError(
                f"WC98_TIMESTAMP_NOT_MONOTONIC at_index={index} "
                f"prev={previous_ts} curr={record.timestamp}"
            )
        if record.timestamp < TS_MIN or record.timestamp > TS_MAX:
            raise RuntimeError(f"WC98_TIMESTAMP_OUT_OF_RANGE ts={record.timestamp}")
        previous_ts = record.timestamp


@dataclass(frozen=True)
class FileValidationStats:
    path: Path
    file_size: int
    decoded_count: int


def _open_log_stream(path: Path) -> BinaryIO:
    if path.suffix == ".gz" or path.name.endswith(".gz"):
        return gzip.open(path, "rb")
    return path.open("rb")


def iter_validated_records(path: Path) -> Iterator[WC98Record]:
    """Stream records while applying Step 3 decode asserts (size, monotonic, range)."""
    previous_ts: int | None = None
    decoded = 0
    bytes_read = 0
    with _open_log_stream(path) as stream:
        while True:
            chunk = stream.read(RECORD_SIZE)
            if not chunk:
                break
            bytes_read += len(chunk)
            if len(chunk) != RECORD_SIZE:
                raise RuntimeError(
                    f"WC98_RECORD_SIZE_MISMATCH file_size_partial remainder={len(chunk)} "
                    f"record_size={RECORD_SIZE} path={path}"
                )
            record = decode_record(chunk)
            if previous_ts is not None and record.timestamp < previous_ts:
                raise RuntimeError(
                    f"WC98_TIMESTAMP_NOT_MONOTONIC at_index={decoded} "
                    f"prev={previous_ts} curr={record.timestamp} path={path}"
                )
            if record.timestamp < TS_MIN or record.timestamp > TS_MAX:
                raise RuntimeError(
                    f"WC98_TIMESTAMP_OUT_OF_RANGE ts={record.timestamp} path={path}"
                )
            previous_ts = record.timestamp
            decoded += 1
            yield record
    if bytes_read % RECORD_SIZE != 0:
        raise RuntimeError(
            f"WC98_RECORD_SIZE_MISMATCH file_size={bytes_read} record_size={RECORD_SIZE} "
            f"remainder={bytes_read % RECORD_SIZE} path={path}"
        )
    expected_count = bytes_read // RECORD_SIZE
    if decoded != expected_count:
        raise RuntimeError(
            f"WC98_RECORD_COUNT_MISMATCH decoded={decoded} expected={expected_count} path={path}"
        )


def validate_file_streaming(path: Path) -> FileValidationStats:
    """Run all Step 3 asserts in one streaming pass; return summary stats."""
    decoded = 0
    bytes_read = 0
    for _record in iter_validated_records(path):
        decoded += 1
        bytes_read += RECORD_SIZE
    return FileValidationStats(path=path, file_size=bytes_read, decoded_count=decoded)


def format_record_human(record: WC98Record) -> str:
    gmt = datetime.fromtimestamp(record.timestamp, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )
    return (
        f"ts={record.timestamp} gmt={gmt} client_id={record.client_id} "
        f"object_id={record.object_id} size={record.size} method={record.method} "
        f"status={record.status} type={record.type} server={record.server}"
    )


def validate_file(path: Path, show_first: int = 3, summary_only: bool = False) -> int:
    data = read_log_bytes(path)
    file_size = len(data)
    expected_count = file_size // RECORD_SIZE
    records = decode_bytes(data)
    validate_records(records, file_size)

    print(
        f"RECORD_SIZE={RECORD_SIZE} file_size={file_size} "
        f"expected_count={expected_count} decoded_count={len(records)}"
    )
    if summary_only:
        print("WC98_DECODE_SELF_CHECK=PASS")
        return 0
    print("WC98_DECODE_SELF_CHECK=PASS")
    for index in range(min(show_first, len(records))):
        print(f"RECORD[{index}] {format_record_human(records[index])}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Binary log or .gz file")
    parser.add_argument(
        "--show-first",
        type=int,
        default=3,
        help="Print first N decoded records with human-readable timestamps",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print RECORD_SIZE summary and PASS only (no sample records)",
    )
    args = parser.parse_args()
    if not args.path.is_file():
        print(f"ERROR: file not found: {args.path}", file=sys.stderr)
        return 1
    try:
        return validate_file(
            args.path,
            show_first=args.show_first,
            summary_only=args.summary_only,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
