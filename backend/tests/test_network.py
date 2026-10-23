import socket
import ssl
from types import SimpleNamespace

import httpx
import pytest

from backend.app.services import network


def setup(monkeypatch):
    monkeypatch.setenv('ECOPULSE_DNS_FALLBACK','true')
    monkeypatch.setenv('SUPABASE_URL','https://project.supabase.co')
    monkeypatch.setattr(network,'resolve_ipv4',lambda host:['1.1.1.1'])
    transport=network.DNSFallbackTransport()
    transport.primary.close();transport.fallback.close()
    return transport


def fail_dns(request):
    try:
        raise socket.gaierror(11001,'Name resolution failed')
    except socket.gaierror as cause:
        raise httpx.ConnectError('DNS failed',request=request) from cause


def test_dns_fallback_preserves_tls_hostname_host_and_body(monkeypatch):
    transport=setup(monkeypatch)
    seen=[]
    transport.primary=httpx.MockTransport(fail_dns)
    transport.fallback=httpx.MockTransport(lambda request:seen.append(request) or httpx.Response(200,json={'ok':True}))
    with httpx.Client(transport=transport) as client:
        result=client.post('https://project.supabase.co/auth/v1/token',json={'password':'test-only'})
    assert result.status_code==200
    assert result.request.url.host=='project.supabase.co'
    assert seen[0].url.host=='1.1.1.1'
    assert seen[0].headers['host']=='project.supabase.co'
    assert seen[0].extensions['sni_hostname']=='project.supabase.co'
    assert b'test-only' in seen[0].content


@pytest.mark.parametrize('url,enabled',[('https://untrusted.example','true'),('http://project.supabase.co','true'),('https://project.supabase.co','false')])
def test_no_fallback_for_unknown_hosts_http_or_disabled_setting(monkeypatch,url,enabled):
    transport=setup(monkeypatch);monkeypatch.setenv('ECOPULSE_DNS_FALLBACK',enabled)
    transport.primary=httpx.MockTransport(fail_dns)
    transport.fallback=httpx.MockTransport(lambda request:pytest.fail('Unexpected fallback'))
    with httpx.Client(transport=transport) as client,pytest.raises(httpx.ConnectError):
        client.get(url)


def test_certificate_failures_never_trigger_dns_workaround(monkeypatch):
    transport=setup(monkeypatch)
    def bad_certificate(request):
        try: raise ssl.SSLCertVerificationError('Certificate mismatch')
        except ssl.SSLCertVerificationError as cause: raise httpx.ConnectError('TLS failed') from cause
    transport.primary=httpx.MockTransport(bad_certificate)
    transport.fallback=httpx.MockTransport(lambda request:pytest.fail('TLS validation was bypassed'))
    with httpx.Client(transport=transport) as client,pytest.raises(httpx.ConnectError):
        client.get('https://project.supabase.co')


def test_service_http_errors_are_not_retried(monkeypatch):
    transport=setup(monkeypatch)
    transport.primary=httpx.MockTransport(lambda request:httpx.Response(400,json={'error':'invalid_credentials'}))
    transport.fallback=httpx.MockTransport(lambda request:pytest.fail('Unexpected auth retry'))
    with httpx.Client(transport=transport) as client:
        assert client.post('https://project.supabase.co').status_code==400


def test_secure_dns_rejects_private_answers_and_caches_with_ttl(monkeypatch):
    monkeypatch.setenv('ECOPULSE_DNS_FALLBACK','true')
    monkeypatch.setenv('SUPABASE_URL','https://project.supabase.co')
    monkeypatch.setattr(network,'_cache',{})
    calls=[]
    answers=[{'type':1,'TTL':60,'data':'192.168.0.1'}]
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url,**kwargs):
            calls.append(url)
            return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'Status':0,'Answer':answers})
    monkeypatch.setattr(network.httpx,'Client',Client)
    with pytest.raises(socket.gaierror): network.resolve_ipv4('project.supabase.co')
    answers[0]['data']='1.1.1.1'
    assert network.resolve_ipv4('project.supabase.co')==['1.1.1.1']
    assert network.resolve_ipv4('project.supabase.co')==['1.1.1.1']
    assert len(calls)==2


@pytest.mark.anyio
async def test_async_transport_keeps_loopback_calls_unchanged(monkeypatch):
    transport=network.AsyncDNSFallbackTransport()
    transport.transport.close()
    transport.transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'host':request.url.host}))
    async with httpx.AsyncClient(transport=transport) as client:
        assert (await client.get('http://localhost:8000')).json()=={'host':'localhost'}


@pytest.mark.anyio
async def test_async_transport_decodes_compressed_responses_once():
    import gzip
    transport=network.AsyncDNSFallbackTransport()
    transport.transport.close()
    transport.transport=httpx.MockTransport(lambda request:httpx.Response(200,
        headers={'content-encoding':'gzip','content-type':'application/json'},
        content=gzip.compress(b'{"ok":true}')))
    async with httpx.AsyncClient(transport=transport) as client:
        assert (await client.get('https://example.com')).json()=={'ok':True}
