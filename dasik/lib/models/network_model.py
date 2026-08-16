from typing import List, Literal, Optional
from pydantic import BaseModel, Field

class NetworkModel(BaseModel):
    type : Literal["NetworkManager", "systemd-networkd"]
    # Defaults ON, per Network_configuration(7): nss-myhostname covers most
    # software, but some reads /etc/hosts directly and would otherwise resolve
    # the machine's own name OVER THE NETWORK. The flag survives for the case
    # where something else manages the file.
    add_default_hosts : bool = True