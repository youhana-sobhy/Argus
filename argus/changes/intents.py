"""Change intents: *what* the engineer wants, in vendor-neutral terms.

The planner turns an intent into a vendor payload, a rollback payload, a diff and checks.
MVP intents (agreed scope): create Application Profile, create Bridge Domain, delete object.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter


class EpgSpec(BaseModel):
    name: str
    bridge_domain: str
    descr: str = ""


class CreateAppProfile(BaseModel):
    kind: Literal["create_app_profile"] = "create_app_profile"
    tenant: str
    name: str
    descr: str = ""
    epgs: list[EpgSpec] = Field(default_factory=list)

    def title(self) -> str:
        return f"Create Application Profile {self.name} in {self.tenant}"


class CreateBridgeDomain(BaseModel):
    kind: Literal["create_bridge_domain"] = "create_bridge_domain"
    tenant: str
    name: str
    vrf: str
    subnets: list[str] = Field(default_factory=list)
    descr: str = ""
    unicast_route: bool = True
    arp_flood: bool = False
    l2_unknown_unicast: Literal["proxy", "flood"] = "proxy"

    def title(self) -> str:
        return f"Create Bridge Domain {self.name} in {self.tenant}"


class DeleteObject(BaseModel):
    kind: Literal["delete_object"] = "delete_object"
    tenant: str
    object_type: Literal["app_profile", "bridge_domain", "epg"]
    name: str
    app_profile: str = ""  # required when object_type == "epg"

    def title(self) -> str:
        label = {"app_profile": "Application Profile", "bridge_domain": "Bridge Domain", "epg": "EPG"}[self.object_type]
        return f"Delete {label} {self.name} from {self.tenant}"


Intent = Annotated[Union[CreateAppProfile, CreateBridgeDomain, DeleteObject], Field(discriminator="kind")]
IntentAdapter: TypeAdapter[Intent] = TypeAdapter(Intent)


def parse_intent(data: dict) -> CreateAppProfile | CreateBridgeDomain | DeleteObject:
    return IntentAdapter.validate_python(data)
