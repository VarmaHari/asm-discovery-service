from dataclasses import dataclass
from typing import Iterator, List, Optional, Protocol

import dns.exception
import dns.resolver

DNS_RECORD_TYPES = ("A", "AAAA", "NS", "MX")


class DNSAnswer(Protocol):
    @property
    def rrset(self) -> object:
        ...

    def __iter__(self) -> Iterator[object]:
        ...


class Resolver(Protocol):
    def resolve(
        self,
        qname: str,
        rdtype: str,
        *,
        lifetime: float,
        raise_on_no_answer: bool = True,
    ) -> DNSAnswer:
        ...


class DNSLookupTimeout(Exception):
    pass


class DNSResolutionError(Exception):
    pass


@dataclass(frozen=True)
class DiscoveredRecord:
    record_type: str
    value: str


def discover_dns_records(
    domain_name: str,
    *,
    timeout_seconds: float,
    resolver: Optional[Resolver] = None,
) -> List[DiscoveredRecord]:
    dns_resolver = resolver or dns.resolver.Resolver()
    discovered = set()
    for record_type in DNS_RECORD_TYPES:
        try:
            answers = dns_resolver.resolve(
                domain_name,
                record_type,
                lifetime=timeout_seconds,
                raise_on_no_answer=False,
            )
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            continue
        except dns.exception.Timeout as error:
            raise DNSLookupTimeout(
                f"Timed out resolving {record_type} for domain"
            ) from error
        except dns.exception.DNSException as error:
            raise DNSResolutionError(
                f"DNS error resolving {record_type} for domain"
            ) from error

        if getattr(answers, "rrset", None) is None:
            continue

        for answer in answers:
            value = _record_value(record_type, answer)
            discovered.add(DiscoveredRecord(record_type, value))

    return sorted(
        discovered,
        key=lambda record: (record.record_type, record.value),
    )


def _record_value(record_type: str, answer: object) -> str:
    if record_type == "MX":
        preference = getattr(answer, "preference")
        exchange = str(getattr(answer, "exchange")).rstrip(".").lower()
        return f"{preference} {exchange}"
    value = str(answer).rstrip(".")
    if record_type == "NS":
        value = value.lower()
    return value
