"""Decode HTML with the output limit applied inside zlib, before expansion allocation."""
import zlib
from webmonitor.api.errors import DomainError
from webmonitor.collection.types import MAX_HTML_BYTES


class BoundedHTMLDecoder:
    def __init__(self, encoding: str, limit: int = MAX_HTML_BYTES):
        self.encoding = encoding.strip().lower()
        if self.encoding not in {"", "identity", "gzip", "deflate"}:
            raise DomainError("unsupported_content_encoding", 422)
        self.limit = limit
        self.data = bytearray()
        self.raw_size = 0
        self.decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if self.encoding == "gzip" else None
        self.prefix = b""

    def feed(self, raw: bytes):
        self.raw_size += len(raw)
        if self.raw_size > self.limit:
            raise DomainError("collection_limit", 422, details={"limit": "response_bytes"})
        if self.encoding in {"", "identity"}:
            self.data.extend(raw)
            return
        if self.decoder is None:
            raw = self.prefix + raw
            if len(raw) < 2:
                self.prefix = raw
                return
            self.prefix = b""
            # HTTP deflate exists with both RFC zlib wrapping and legacy raw DEFLATE.
            wrapped = len(raw) >= 2 and raw[0] & 15 == 8 and (raw[0] * 256 + raw[1]) % 31 == 0
            self.decoder = zlib.decompressobj(zlib.MAX_WBITS if wrapped else -zlib.MAX_WBITS)
        try:
            while raw:
                if self.decoder.eof:
                    if self.encoding != "gzip":
                        raise DomainError("collection_invalid", 422, details={"reason": "trailing_compressed_data"})
                    self.decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                remaining = self.limit - len(self.data)
                decoded = self.decoder.decompress(raw, remaining + 1)
                if len(decoded) > remaining:
                    raise DomainError("collection_limit", 422, details={"limit": "html_bytes"})
                self.data.extend(decoded)
                raw = self.decoder.unused_data if self.decoder.eof else self.decoder.unconsumed_tail
        except zlib.error:
            raise DomainError("collection_invalid", 422, details={"reason": "invalid_content_encoding"}) from None

    def finish(self) -> bytes:
        if self.encoding not in {"", "identity"} and (self.decoder is None or not self.decoder.eof):
            raise DomainError("collection_invalid", 422, details={"reason": "incomplete_content_encoding"})
        return bytes(self.data)
