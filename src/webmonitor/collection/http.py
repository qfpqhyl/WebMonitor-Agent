"""HTML collection through the mandatory ACL egress proxy."""
import asyncio
from time import monotonic
from urllib.parse import urljoin

import httpx

from webmonitor.api.errors import DomainError
from webmonitor.collection.extraction import extract_fields
from webmonitor.collection.decoding import BoundedHTMLDecoder
from webmonitor.collection.validation import validate_collection
from webmonitor.collection.types import CollectionResult, MAX_HTML_BYTES, analyze_html, collection_coverage
from webmonitor.config import get_settings
from webmonitor.schemas.monitors import DraftSpec
from webmonitor.security.egress import validate_target


async def collect_http(url: str, spec: DraftSpec | None = None) -> CollectionResult:
    started = monotonic()
    try:
        async with asyncio.timeout(30):
            target = await validate_target(url)
            async with httpx.AsyncClient(proxy=get_settings().egress_proxy_url, trust_env=False,
                                         follow_redirects=False, timeout=30, headers={'Accept': 'text/html,application/xhtml+xml', 'Accept-Encoding': 'gzip, deflate'}) as client:
                for redirects in range(6):
                    async with client.stream('GET', target) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get('location')
                            if not location or redirects == 5:
                                raise DomainError('redirect_limit', 422)
                            target = await validate_target(urljoin(target, location))
                            continue
                        if response.status_code == 403 and response.headers.get('x-squid-error', '').startswith('ERR_ACCESS_DENIED'):
                            raise DomainError('url_forbidden', 422)
                        if response.status_code >= 400:
                            raise DomainError('target_http_error', 503 if response.status_code >= 500 else 422,
                                              details={'status_code': response.status_code})
                        if response.status_code != 200:
                            raise DomainError('target_http_error', 422, details={'status_code': response.status_code})
                        media_type = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
                        if media_type not in {'text/html', 'application/xhtml+xml'}:
                            raise DomainError('unsupported_content_type', 422)
                        decoder = BoundedHTMLDecoder(response.headers.get('content-encoding', 'identity'))
                        async for chunk in response.aiter_raw(chunk_size=65536):
                            decoder.feed(chunk)
                        html = decoder.finish().decode(response.encoding or 'utf-8', errors='replace')
                        if len(html.encode('utf-8')) > MAX_HTML_BYTES:
                            raise DomainError('collection_limit', 422, details={'limit': 'html_bytes'})
                        analysis, warnings = analyze_html(html)
                        analysis['collection_mode'] = 'http'
                        coverage = collection_coverage(spec, warnings, html=html)
                        result = CollectionResult(target, html, None, {}, coverage,
                                                  int((monotonic() - started) * 1000), warnings, analysis)
                        try:
                            if spec:
                                result.fields = extract_fields(html, spec, final_url=target)
                                validate_collection(spec, result.fields, coverage=coverage)
                        except DomainError as exc:
                            exc.collection_result = result
                            raise
                        return result
    except (TimeoutError, httpx.TimeoutException):
        raise DomainError('collection_timeout', 503) from None
    except httpx.ProxyError as exc:
        if str(exc).startswith('403'):
            raise DomainError('url_forbidden', 422) from None
        raise DomainError('target_unavailable', 503) from None
    except httpx.HTTPError:
        raise DomainError('target_unavailable', 503) from None
