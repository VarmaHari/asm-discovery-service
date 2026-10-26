from typing import Dict, Iterator, List, Tuple

import dns.exception
import dns.resolver
import pytest

from app.services.dns_discovery import (
    DNS_RECORD_TYPES,
    DNSLookupTimeout,
    DNSResolutionError,
    DNSAnswer,
    discover_dns_records,
)


class FakeAnswer:
    def __init__(self, values: List[object]) -> None:
        self._values: List[object] = values

    @property
    def rrset(self) -> object:
        return self._values

    def __iter__(self) -> Iterator[object]:
        return iter(self._values)


class FakeMX:
    def __init__(self, preference: int, exchange: str) -> None:
        self.preference = preference
        self.exchange = exchange

    def __str__(self) -> str:
        return f"{self.preference} {self.exchange}"


class FakeResolver:
    def __init__(self, results: Dict[str, object]) -> None:
        self.results = results
        self.calls: List[Tuple[str, str, float, bool]] = []

    def resolve(
        self,
        qname: str,
        rdtype: str,
        *,
        lifetime: float,
        raise_on_no_answer: bool = True,
    ) -> DNSAnswer:
        self.calls.append((qname, rdtype, lifetime, raise_on_no_answer))
        result = self.results.get(rdtype, FakeAnswer([]))
        if isinstance(result, Exception):
            raise result
        assert isinstance(result, FakeAnswer)
        return result


def test_queries_only_required_types_and_normalizes_record_values() -> None:
    resolver = FakeResolver(
        {
            "A": FakeAnswer(["192.0.2.12"]),
            "AAAA": FakeAnswer(["2001:db8::12"]),
            "NS": FakeAnswer(["Ns1.Example.COM."]),
            "MX": FakeAnswer([FakeMX(10, "Mail.Example.COM.")]),
        }
    )

    records = discover_dns_records(
        "example.com",
        timeout_seconds=2.5,
        resolver=resolver,
    )

    assert {(record.record_type, record.value) for record in records} == {
        ("A", "192.0.2.12"),
        ("AAAA", "2001:db8::12"),
        ("NS", "ns1.example.com"),
        ("MX", "10 mail.example.com"),
    }
    assert [call[1] for call in resolver.calls] == list(DNS_RECORD_TYPES)
    assert all(call[2:] == (2.5, False) for call in resolver.calls)


@pytest.mark.parametrize(
    "missing",
    [dns.resolver.NoAnswer(), dns.resolver.NXDOMAIN()],
)
def test_missing_records_are_nonfatal(missing: Exception) -> None:
    resolver = FakeResolver({record_type: missing for record_type in DNS_RECORD_TYPES})

    records = discover_dns_records(
        "empty.example",
        timeout_seconds=1,
        resolver=resolver,
    )

    assert records == []


def test_dns_timeouts_are_classified_separately() -> None:
    resolver = FakeResolver({"A": dns.exception.Timeout("timeout")})

    with pytest.raises(DNSLookupTimeout):
        discover_dns_records(
            "timeout.example",
            timeout_seconds=1,
            resolver=resolver,
        )


def test_dns_protocol_errors_are_classified_as_resolution_errors() -> None:
    resolver = FakeResolver(
        {"A": dns.exception.DNSException("nameserver failure")}
    )

    with pytest.raises(DNSResolutionError):
        discover_dns_records(
            "failure.example",
            timeout_seconds=1,
            resolver=resolver,
        )


def test_unexpected_resolver_errors_remain_unexpected() -> None:
    resolver = FakeResolver({"A": RuntimeError("unexpected")})

    with pytest.raises(RuntimeError, match="unexpected"):
        discover_dns_records(
            "failure.example",
            timeout_seconds=1,
            resolver=resolver,
        )
