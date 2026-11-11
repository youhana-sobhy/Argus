"""Redaction, proposal extraction and provider switching."""

from autohero.ai.assistant import Assistant, extract_proposal
from autohero.ai.providers import LLMProvider, LLMReply
from autohero.ai.redaction import Redactor
from autohero.core.inventory import get_inventory


def test_redaction_roundtrip():
    r = Redactor()
    text = "leaf 10.0.0.1/24 serial FDO2611L101K password=hunter2 again 10.0.0.1/24"
    red = r.redact(text)
    assert "10.0.0.1" not in red and "FDO2611L101K" not in red and "hunter2" not in red
    assert red.count("<IP_1>") == 2
    assert r.restore(red).startswith("leaf 10.0.0.1/24 serial FDO2611L101K")


def test_extract_proposal():
    text = 'Sure.\n```autohero-change\n{"kind":"delete_object","tenant":"VF-5GC","object_type":"bridge_domain","name":"LEGACY_BD"}\n```'
    clean, prop = extract_proposal(text)
    assert clean == "Sure." and prop["name"] == "LEGACY_BD"


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self):
        self.seen = ""

    def complete(self, system, messages):
        self.seen = system + messages[-1]["content"]
        return LLMReply("Gateway is <IP_1>", "fake", "fake-1")


def test_external_provider_gets_masked_data():
    fake = FakeProvider()
    out = Assistant(fake, redact=True).ask("where is 10.10.10.1?", [], get_inventory().snapshot(refresh=True))
    assert "10.10.10.1" not in fake.seen and "172.16.100." not in fake.seen
    assert out["masked"] > 0 and "<IP_" not in out["answer"]
