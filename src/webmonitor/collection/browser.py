"""Bounded fresh-context Chromium collection, without a sandbox fallback."""
import asyncio
from contextlib import suppress
from time import monotonic

from playwright.async_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError, async_playwright

from webmonitor.api.errors import DomainError
from webmonitor.collection.extraction import extract_fields
from webmonitor.collection.validation import validate_collection
from webmonitor.collection.types import CollectionResult, MAX_HTML_BYTES, analyze_html, collection_coverage
from webmonitor.config import get_settings
from webmonitor.schemas.monitors import DraftSpec
from webmonitor.security.egress import validate_target

_BROWSER_SLOTS = asyncio.Semaphore(2)
MAX_REQUESTS = 100
MAX_RESOURCE_BYTES = 20 * 1024 * 1024


async def collect_browser(url: str, spec: DraftSpec | None = None) -> CollectionResult:
    async with _BROWSER_SLOTS:
        started = monotonic()
        browser = context = None
        tasks = set()
        failure = None
        request_count = resource_bytes = 0

        def spawn(coro):
            task = asyncio.create_task(coro)
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            return task

        async def fail(error):
            nonlocal failure
            if failure is None:
                failure = error
                if context:
                    with suppress(PlaywrightError):
                        await context.close()

        try:
            async with asyncio.timeout(60):
                target = await validate_target(url)
                async with async_playwright() as playwright:
                    try:
                        browser = await playwright.chromium.launch(
                            headless=True, chromium_sandbox=True,
                            proxy={'server': get_settings().egress_proxy_url},
                            args=['--proxy-bypass-list=<-loopback>', '--disable-quic',
                                  '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])
                    except PlaywrightError:
                        raise DomainError('browser_unavailable', 503) from None
                    try:
                        context = await browser.new_context(viewport={'width': 1280, 'height': 800},
                                                            service_workers='block', accept_downloads=False,
                                                            permissions=[])
                        # Fixed collector policy, never page/model supplied JavaScript. Workers
                        # are unsupported because their separate targets evade page CDP budgets.
                        await context.add_init_script("""for (const name of ['Worker', 'SharedWorker']) {
                            Object.defineProperty(globalThis, name, {value: class {
                                constructor() { throw new Error('Workers disabled by collector'); }
                            }, configurable: false});
                        }""")
                        page = await context.new_page()
                        page.set_default_timeout(10000)
                        page.set_default_navigation_timeout(30000)
                        page.on('popup', lambda popup: spawn(popup.close()))
                        page.on('download', lambda download: spawn(download.cancel()))
                        page.on('dialog', lambda dialog: spawn(dialog.dismiss()))
                        async def block_socket(route):
                            await route.close()
                        await context.route_web_socket('**/*', block_socket)

                        async def guard(route):
                            try:
                                # Iframe documents and popup targets are not extracted. Blocking
                                # them also prevents separate OOPIF CDP targets escaping budgets.
                                if route.request.frame != page.main_frame:
                                    await route.abort('blockedbyclient')
                                    return
                                await validate_target(route.request.url)
                                await route.continue_()
                            except DomainError as exc:
                                with suppress(PlaywrightError):
                                    await route.abort('blockedbyclient')
                                await fail(exc)
                            except PlaywrightError:
                                return
                        await context.route('**/*', guard)
                        cdp = await context.new_cdp_session(page)
                        await cdp.send('Network.enable')
                        # Fetch intercepts each redirect hop (unlike Playwright route handlers).
                        await cdp.send('Fetch.enable', {'patterns': [{'urlPattern': '*', 'requestStage': 'Request'}]})

                        async def paused(event):
                            nonlocal request_count
                            try:
                                request_count += 1
                                if request_count > MAX_REQUESTS:
                                    raise DomainError('collection_limit', 422, details={'limit': 'requests'})
                                await validate_target(event['request']['url'])
                                await cdp.send('Fetch.continueRequest', {'requestId': event['requestId']})
                            except DomainError as exc:
                                with suppress(PlaywrightError):
                                    await cdp.send('Fetch.failRequest', {'requestId': event['requestId'], 'errorReason': 'BlockedByClient'})
                                await fail(exc)
                            except PlaywrightError:
                                return
                        cdp.on('Fetch.requestPaused', lambda event: spawn(paused(event)))

                        def data_received(event):
                            nonlocal resource_bytes
                            resource_bytes += max(event.get('dataLength', 0), event.get('encodedDataLength', 0))
                            if resource_bytes > MAX_RESOURCE_BYTES:
                                spawn(fail(DomainError('collection_limit', 422, details={'limit': 'resource_bytes'})))
                        cdp.on('Network.dataReceived', data_received)

                        def response_received(event):
                            headers = event.get('response', {}).get('headers', {})
                            proxy_error = next((str(value) for key, value in headers.items() if key.lower() == 'x-squid-error'), '')
                            if event.get('response', {}).get('status') == 403 and proxy_error.startswith('ERR_ACCESS_DENIED'):
                                spawn(fail(DomainError('url_forbidden', 422)))
                            length = next((value for key, value in headers.items() if key.lower() == 'content-length'), None)
                            try:
                                oversized = length is not None and int(length) + resource_bytes > MAX_RESOURCE_BYTES
                            except (ValueError, TypeError):
                                oversized = False
                            if oversized:
                                spawn(fail(DomainError('collection_limit', 422, details={'limit': 'resource_bytes'})))
                        cdp.on('Network.responseReceived', response_received)
                        response = await page.goto(target, wait_until='networkidle', timeout=30000)
                        if response and response.status == 403 and response.headers.get('x-squid-error', '').startswith('ERR_ACCESS_DENIED'):
                            raise DomainError('url_forbidden', 422)
                        if response is None or response.status >= 400:
                            status = response.status if response else 0
                            raise DomainError('target_http_error', 503 if status >= 500 else 422,
                                              details={'status_code': status})
                        snapshots = []
                        paginated = False

                        async def capture():
                            if len(snapshots) >= 3:
                                raise DomainError('collection_limit', 422, details={'limit': 'pages'})
                            html = await page.content()
                            if sum(len(doc.encode('utf-8')) for doc, _ in snapshots) + len(html.encode('utf-8')) > MAX_HTML_BYTES:
                                raise DomainError('collection_limit', 422, details={'limit': 'html_bytes'})
                            final_url = await validate_target(page.url)
                            snapshots.append((html, final_url))

                        if spec:
                            for step in spec.browser_steps:
                                if step.type == 'wait_for_selector':
                                    try:
                                        await page.locator('css=' + step.selector).first.wait_for(state='attached', timeout=step.timeout_ms)
                                    except PlaywrightTimeoutError:
                                        error = DomainError('collection_invalid', 422, details={'reason': 'selector_wait_timeout'})
                                        html = await page.content()
                                        if len(html.encode('utf-8')) <= MAX_HTML_BYTES:
                                            error.collection_result = CollectionResult(page.url, html, None, {}, spec.coverage.model_dump(),
                                                int((monotonic() - started) * 1000), ['selector_wait_timeout'], {})
                                        raise error from None
                                elif step.type == 'scroll':
                                    for _ in range(step.max_steps):
                                        await page.mouse.wheel(0, 800)
                                        await page.wait_for_timeout(250)
                                elif step.type == 'click_next':
                                    paginated = True
                                    for _ in range(step.max_pages - 1):
                                        if len(snapshots) >= 2:
                                            raise DomainError('collection_limit', 422, details={'limit': 'pages'})
                                        button = page.locator('css=' + step.selector)
                                        if await button.count() != 1:
                                            raise DomainError('collection_invalid', 422, details={'reason': 'next_selector_not_unique'})
                                        if not await button.is_enabled() or not await button.is_visible():
                                            break
                                        await capture()
                                        await button.click()
                                        await page.wait_for_load_state('networkidle', timeout=10000)
                        await capture()
                        html, final_url = snapshots[-1]
                        analysis, warnings = analyze_html(html)
                        structure = await page.evaluate("""() => ({shadow: Array.from(document.querySelectorAll('*')).some(e => e.shadowRoot),
                            frames: document.querySelectorAll('iframe').length})""")
                        if structure['shadow'] and 'shadow_dom_not_extracted' not in warnings:
                            warnings.append('shadow_dom_not_extracted')
                        if structure['frames'] and 'iframe_content_not_extracted' not in warnings:
                            warnings.append('iframe_content_not_extracted')
                        if paginated and 'pagination_coverage_unproven' not in warnings:
                            warnings.append('pagination_coverage_unproven')
                        coverage = collection_coverage(spec, warnings, paginated=paginated, html=html)
                        screenshot = await page.screenshot(type='png', full_page=False, timeout=10000)
                        if failure:
                            raise failure
                        analysis.update(collection_mode='browser', unsupported=warnings.copy(),
                                        pages_observed=len(snapshots), requests=request_count, resource_bytes=resource_bytes)
                        # Multi-page DOMs are retained as inert evidence, not executable markup.
                        evidence_html = '\n<!-- COLLECTED PAGE BOUNDARY -->\n'.join(doc for doc, _ in snapshots)
                        if len(evidence_html.encode('utf-8')) > MAX_HTML_BYTES:
                            raise DomainError('collection_limit', 422, details={'limit': 'html_bytes'})
                        result = CollectionResult(final_url, evidence_html, screenshot, {}, coverage,
                                                  int((monotonic() - started) * 1000), warnings, analysis)
                        try:
                            if spec:
                                for document, document_url in snapshots:
                                    extracted = extract_fields(document, spec, final_url=document_url)
                                    for field in spec.fields:
                                        if field.name not in extracted:
                                            continue
                                        if field.type == 'list':
                                            existing = result.fields.setdefault(field.name, [])
                                            if spec.business_key and spec.business_key.field == field.name:
                                                keys = spec.business_key.item_fields
                                                seen = {tuple(str(row.get(key)) for key in keys): row for row in existing}
                                                page_keys = set()
                                                for row in extracted[field.name]:
                                                    key = tuple(str(row.get(name)) for name in keys)
                                                    if key in page_keys:
                                                        raise DomainError('collection_invalid', 422, details={'reason': 'duplicate_business_key'})
                                                    page_keys.add(key)
                                                    if key in seen and seen[key] != row:
                                                        raise DomainError('collection_invalid', 422, details={'reason': 'pagination_row_changed'})
                                                    if key not in seen:
                                                        existing.append(row)
                                            else:
                                                existing.extend(extracted[field.name])
                                        else:
                                            result.fields[field.name] = extracted[field.name]
                                validate_collection(spec, result.fields, coverage=coverage)
                        except DomainError as exc:
                            exc.collection_result = result
                            raise
                        return result
                    finally:
                        if context:
                            with suppress(PlaywrightError):
                                await context.close()
                        if browser:
                            with suppress(PlaywrightError):
                                await browser.close()
        except TimeoutError:
            raise failure or DomainError('collection_timeout', 503) from None
        except PlaywrightError:
            raise failure or DomainError('browser_collection_failed', 503) from None
        finally:
            for task in tuple(tasks):
                task.cancel()
            if tasks:
                await asyncio.gather(*tuple(tasks), return_exceptions=True)
