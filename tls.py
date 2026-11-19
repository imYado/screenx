"""Local HTTPS for Desk Deck.

Safari only lets a page use the microphone over HTTPS, so Desk Deck makes its own
small certificate authority (CA) the first time it runs and uses it to sign a
certificate for the PC's Wi-Fi address. You install the CA on the iPhone once.

The CA is name-constrained: it can only vouch for private/local addresses
(192.168.x.x, 10.x.x.x, 172.16-31.x.x, 127.x.x.x and "localhost"), so even if
its key leaked it could not be used to impersonate real websites.

Files live in ./certs (git-ignored).
"""

import datetime
import ipaddress
import socket
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

PRIVATE_NETS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"]


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _write_key(path, key):
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))


def _load_key(path):
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def _make_ca(d):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, f"Desk Deck ({socket.gethostname()})"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Desk Deck local CA"),
    ])
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now() - datetime.timedelta(days=1))
        .not_valid_after(_now() + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                     content_commitment=False, key_encipherment=False,
                                     data_encipherment=False, key_agreement=False,
                                     encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.NameConstraints(
            permitted_subtrees=[x509.IPAddress(ipaddress.ip_network(n)) for n in PRIVATE_NETS]
            + [x509.DNSName("localhost")],
            excluded_subtrees=None), critical=True)
        .add_extension(ski, critical=False)
        .sign(key, hashes.SHA256())
    )
    _write_key(d / "ca.key", key)
    (d / "ca.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert, key


def _server_ok(path, ip):
    if not path.exists():
        return False
    try:
        cert = x509.load_pem_x509_certificate(path.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        has_ip = ipaddress.ip_address(ip) in san.get_values_for_type(x509.IPAddress)
        return has_ip and cert.not_valid_after_utc - _now() > datetime.timedelta(days=30)
    except Exception:
        return False


def ensure_certs(root, ip):
    """Returns (server_cert_path, server_key_path, ca_cert_path), creating/refreshing as needed."""
    d = Path(root) / "certs"
    d.mkdir(exist_ok=True)
    if (d / "ca.crt").exists() and (d / "ca.key").exists():
        ca = x509.load_pem_x509_certificate((d / "ca.crt").read_bytes())
        ca_key = _load_key(d / "ca.key")
    else:
        ca, ca_key = _make_ca(d)

    crt, key_path = d / "server.crt", d / "server.key"
    if not _server_ok(crt, ip):
        key = ec.generate_private_key(ec.SECP256R1())
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ip)]))
            .issuer_name(ca.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_now() - datetime.timedelta(days=1))
            # iOS rejects server certificates valid for more than 825 days.
            .not_valid_after(_now() + datetime.timedelta(days=800))
            .add_extension(x509.SubjectAlternativeName([
                x509.IPAddress(ipaddress.ip_address(ip)),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                x509.DNSName("localhost"),
            ]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        _write_key(key_path, key)
        crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM)
                        + ca.public_bytes(serialization.Encoding.PEM))
    return crt, key_path, d / "ca.crt"


def ca_der(ca_path):
    """The CA certificate in the binary form iOS Safari offers to install as a profile."""
    return x509.load_pem_x509_certificate(Path(ca_path).read_bytes()).public_bytes(serialization.Encoding.DER)
