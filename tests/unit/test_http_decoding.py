import gzip
import zlib
import pytest
from webmonitor.api.errors import DomainError
from webmonitor.collection.decoding import BoundedHTMLDecoder


def test_compressed_expansion_rejected_before_full_allocation():
    decoder=BoundedHTMLDecoder('gzip',limit=65536)
    compressed=gzip.compress(b'x' * (8 * 1024 * 1024))
    with pytest.raises(DomainError) as error:
        decoder.feed(compressed)
    assert error.value.code=='collection_limit'
    assert len(decoder.data)<=65536


def test_valid_gzip_members_and_truncated_stream():
    decoder=BoundedHTMLDecoder('gzip')
    decoder.feed(gzip.compress(b'<h1>')+gzip.compress(b'hello</h1>'))
    assert decoder.finish()==b'<h1>hello</h1>'
    truncated=BoundedHTMLDecoder('gzip')
    truncated.feed(gzip.compress(b'page')[:-4])
    with pytest.raises(DomainError) as error:
        truncated.finish()
    assert error.value.code=='collection_invalid'


@pytest.mark.parametrize('wrapped',[True,False])
def test_deflate_handles_supported_encodings(wrapped):
    content=b'<p>actual HTML</p>'
    compressor=zlib.compressobj(wbits=zlib.MAX_WBITS if wrapped else -zlib.MAX_WBITS)
    compressed=compressor.compress(content)+compressor.flush()
    decoder=BoundedHTMLDecoder('deflate')
    decoder.feed(compressed)
    assert decoder.finish()==content
