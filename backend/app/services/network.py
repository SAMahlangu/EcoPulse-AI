"""Verified HTTPS with an opt-in, app-scoped fallback for broken system DNS.

No Windows network settings, hosts files, certificate checks or auth checks are
changed. DNS answers are fetched over verified HTTPS, cached for their TTL and
used only for configured service hosts after a genuine name-resolution failure.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import threading
import time
from urllib.parse import urlsplit

import httpx
import anyio

_cache: dict[str, tuple[float, list[str]]] = {}
_cache_lock = threading.Lock()


def fallback_enabled() -> bool:
    return os.getenv('ECOPULSE_DNS_FALLBACK', 'false').lower() == 'true'


def allowed_hosts() -> set[str]:
    hosts = {'generativelanguage.googleapis.com', 'api.open-meteo.com',
             'archive-api.open-meteo.com', 'geocoding-api.open-meteo.com',
             'customer-api.open-meteo.com', 'customer-archive-api.open-meteo.com',
             'api.met.no',
             'nominatim.openstreetmap.org', 'overpass-api.de', 'overpass.private.coffee',
             'hips-gis.health.gov.za', 'services7.arcgis.com'}
    for setting in ('SUPABASE_URL', 'DATABASE_URL'):
        host = urlsplit(os.getenv(setting, '')).hostname
        if host:
            hosts.add(host.lower())
    return hosts


def dns_error(exc: BaseException) -> bool:
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, socket.gaierror):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def resolve_ipv4(host: str) -> list[str]:
    host = host.lower()
    if not fallback_enabled() or host not in allowed_hosts():
        raise socket.gaierror('Secure DNS fallback is not enabled for this service')
    with _cache_lock:
        cached = _cache.get(host)
        if cached and cached[0] > time.monotonic():
            return list(cached[1])
    # The resolver's certificate is valid for this IP. No system DNS is needed
    # to reach it, and normal certificate verification remains enabled.
    with httpx.Client(transport=httpx.HTTPTransport(), timeout=5, trust_env=False) as client:
        response = client.get('https://1.1.1.1/dns-query',
                              params={'name': host, 'type': 'A'},
                              headers={'Accept': 'application/dns-json'})
        response.raise_for_status()
        payload = response.json()
    if payload.get('Status') != 0:
        raise socket.gaierror('The configured service could not be resolved')
    addresses, ttls = [], []
    for answer in payload.get('Answer', []):
        if answer.get('type') != 1:
            continue
        address = ipaddress.ip_address(answer['data'])
        if address.version != 4 or not address.is_global:
            continue
        addresses.append(str(address))
        ttls.append(max(0, min(int(answer.get('TTL', 0)), 300)))
    if not addresses:
        raise socket.gaierror('No public address was returned for the configured service')
    with _cache_lock:
        _cache[host] = (time.monotonic() + min(ttls), addresses)
    return list(addresses)


class DNSFallbackTransport(httpx.BaseTransport):
    def __init__(self):
        self.primary = httpx.HTTPTransport()
        self.fallback = httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return self.primary.handle_request(request)
        except httpx.ConnectError as exc:
            host = request.url.host.lower()
            if not (fallback_enabled() and request.url.scheme == 'https'
                    and host in allowed_hosts() and dns_error(exc)):
                raise
            try:
                addresses = resolve_ipv4(host)
            except (httpx.HTTPError, ValueError, KeyError, OSError) as resolver_error:
                raise httpx.ConnectError('Secure DNS lookup for the configured service failed', request=request) from resolver_error
            # DNS failed before any request body was sent. Keep the original
            # Host header and TLS SNI/verification hostname when connecting by IP.
            last_error = exc
            for address in addresses:
                routed = httpx.Request(request.method, request.url.copy_with(host=address),
                    headers=request.headers, stream=request.stream,
                    extensions={**request.extensions, 'sni_hostname': host})
                try:
                    return self.fallback.handle_request(routed)
                except (httpx.ConnectError, httpx.ConnectTimeout) as connection_error:
                    last_error = connection_error
            raise httpx.ConnectError('The configured service is temporarily unreachable', request=request) from last_error

    def close(self):
        self.primary.close()
        self.fallback.close()


class AsyncDNSFallbackTransport(httpx.AsyncBaseTransport):
    """Run bounded upstream requests in a worker without blocking FastAPI."""
    def __init__(self):
        self.transport = DNSFallbackTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        data = await request.aread()
        sync_request = httpx.Request(request.method, request.url, headers=request.headers,
                                    content=data, extensions=request.extensions)
        def send():
            response = self.transport.handle_request(sync_request)
            try:
                body = response.read()
                # read() has already decompressed the sync response. Do not
                # make AsyncClient try to decode the same payload a second time.
                headers = [(key,value) for key,value in response.headers.raw
                           if key.lower() not in (b'content-encoding',b'content-length')]
                return httpx.Response(response.status_code, headers=headers,
                                      content=body, request=request)
            finally:
                response.close()
        return await anyio.to_thread.run_sync(send)

    async def aclose(self):
        await anyio.to_thread.run_sync(self.transport.close)


def request(method: str, url: str, **kwargs) -> httpx.Response:
    timeout = kwargs.pop('timeout', 15)
    with httpx.Client(transport=DNSFallbackTransport(), timeout=timeout,
                      trust_env=False, follow_redirects=False) as client:
        return client.request(method, url, **kwargs)
