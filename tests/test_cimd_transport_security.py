"""CIMD transport security (MAK-0008 §8.3, conformance M32): the client
metadata fetch verifies TLS for the client_id host name and connects only to
the address that passed the public-address check, never re-resolving it.

Unlike `test_oauth_acceptance.py` (which replaces the HTTPS connection to
test size/redirect/timeout limits over plain HTTP), these tests run the real
`_PinnedHTTPSConnection.connect()` and TLS handshake against a local TLS
server whose certificates come from a throwaway CA. The only seam is the
public-address check (the local server is on loopback, which that check
rightly refuses); the address-pinning tests exercise that check itself."""

from __future__ import annotations

import datetime as dt
import http.server
import ssl
import threading
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from mak4i.oauth import cimd
from mak4i.oauth.cimd import ClientMetadataError

HOST = "client.example.com"
URL = f"https://{HOST}/oauth/metadata.json"
NOW = dt.datetime.now(dt.timezone.utc)


def _key():
    return ec.generate_private_key(ec.SECP256R1())


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _ca():
    key = _key()
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name("MAK4I test CA")).issuer_name(_name("MAK4I test CA"))
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(NOW - dt.timedelta(days=1)).not_valid_after(NOW + dt.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                     data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                     crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _leaf(ca_key, ca_cert, dns_name, *, expired=False):
    key = _key()
    before, after = (NOW - dt.timedelta(days=10), NOW - dt.timedelta(days=1)) if expired else (
        NOW - dt.timedelta(days=1), NOW + dt.timedelta(days=10))
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(dns_name)).issuer_name(ca_cert.subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(before).not_valid_after(after)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(dns_name)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def _write(tmp: Path, name: str, key, cert) -> tuple[Path, Path]:
    cert_path, key_path = tmp / f"{name}.crt", tmp / f"{name}.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return cert_path, key_path


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b'{"client_id": "x"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture
def tls(tmp_path):
    """Start a local TLS server presenting a chosen certificate; yields a
    function `serve(kind) -> port` and the CA file."""
    ca_key, ca_cert = _ca()
    ca_file = tmp_path / "ca.pem"
    ca_file.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    servers = []

    def serve(kind: str) -> int:
        if kind == "self-signed":
            key, cert = _ca()  # not issued by the trusted CA
            cert_path, key_path = _write(tmp_path, kind, key, cert)
        else:
            dns = "other.example.com" if kind == "wrong-host" else HOST
            key, cert = _leaf(ca_key, ca_cert, dns, expired=(kind == "expired"))
            cert_path, key_path = _write(tmp_path, kind, key, cert)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server.server_address[1]

    yield serve, ca_file
    for server in servers:
        server.shutdown()
        server.server_close()


def _fetch_from_local(monkeypatch, port: int, *, trust: Path | None):
    """Run the real fetch: only the public-address check is replaced (the
    server is on loopback); `trust` adds the test CA to the default context
    exactly as a public CA would be trusted."""
    monkeypatch.setattr(cimd, "public_addresses", lambda host, p, resolver: ["127.0.0.1"])
    real_context = ssl.create_default_context

    def context():
        ctx = real_context()
        if trust is not None:
            ctx.load_verify_locations(trust)
        return ctx

    monkeypatch.setattr(cimd.ssl, "create_default_context", context)
    return cimd.fetch_document(f"https://{HOST}:{port}/oauth/metadata.json")


def test_trusted_certificate_for_the_client_host_is_accepted(tls, monkeypatch):  # control case
    serve, ca = tls
    fetched = _fetch_from_local(monkeypatch, serve("valid"), trust=ca)
    assert fetched.body == b'{"client_id": "x"}'


@pytest.mark.parametrize("kind", ["self-signed", "wrong-host", "expired"])
def test_invalid_tls_is_refused(tls, monkeypatch, kind):  # M32: invalid TLS never yields a document
    serve, ca = tls
    with pytest.raises(ClientMetadataError, match="could not be fetched") as info:
        _fetch_from_local(monkeypatch, serve(kind), trust=ca)
    assert isinstance(info.value.__cause__, ssl.SSLError)


def test_untrusted_ca_is_refused_by_default(tls, monkeypatch):  # M32: only the system trust store
    serve, _ca_file = tls
    with pytest.raises(ClientMetadataError):
        _fetch_from_local(monkeypatch, serve("valid"), trust=None)


def test_connection_is_pinned_to_the_validated_address(monkeypatch):  # M32: no DNS rebinding
    calls = []

    def rebinding_resolver(host, port):
        calls.append(host)
        return ["93.184.216.34"] if len(calls) == 1 else ["127.0.0.1"]

    connected = []

    def create_connection(address, timeout=None, *args, **kwargs):
        connected.append(address)
        raise OSError("stop after recording the target")

    def no_more_dns(*_a, **_k):
        raise AssertionError("the fetch must not resolve the host name again")

    monkeypatch.setattr(cimd.socket, "create_connection", create_connection)
    monkeypatch.setattr(cimd.socket, "getaddrinfo", no_more_dns)
    with pytest.raises(ClientMetadataError, match="could not be fetched"):
        cimd.fetch_document(URL, resolver=rebinding_resolver)
    assert calls == [HOST]
    assert connected == [("93.184.216.34", 443)]


@pytest.mark.parametrize(
    "answer",
    [
        ["127.0.0.1"],
        ["10.0.0.5"],
        ["169.254.169.254"],  # cloud metadata
        ["93.184.216.34", "192.168.1.1"],  # one unsafe address poisons the answer
        ["::1"],
        ["fd00::1"],
        ["::ffff:127.0.0.1"],
        ["100.64.0.1"],  # carrier-grade NAT
    ],
)
def test_unsafe_addresses_are_refused_before_connecting(monkeypatch, answer):  # M32
    def create_connection(*_a, **_k):
        raise AssertionError("must not connect")

    monkeypatch.setattr(cimd.socket, "create_connection", create_connection)
    with pytest.raises(ClientMetadataError, match="non-public address"):
        cimd.fetch_document(URL, resolver=lambda host, port: answer)


@pytest.mark.parametrize("url", ["https://127.0.0.1/m.json", "https://[::1]/m.json", "https://10.1.2.3/m.json"])
def test_literal_unsafe_hosts_are_refused(monkeypatch, url):  # M32
    monkeypatch.setattr(cimd.socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    with pytest.raises(ClientMetadataError, match="non-public address"):
        cimd.fetch_document(url, resolver=lambda host, port: ["93.184.216.34"])
