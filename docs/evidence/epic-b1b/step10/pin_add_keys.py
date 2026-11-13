"""B1b-10 (NOTES §10.2, SM -31 condition 4): add B1b-7's three wallet keys to
the user list pin, with the values D2 measured; every existing key unchanged.
The file is re-written the way the pin writer writes it
(json.dumps(indent=2, sort_keys=True) + "\\n"), so existing lines are
byte-identical and the diff is only + lines."""

import json
from pathlib import Path

PIN = Path("users/test_data/user_list_payload_pin.json")
D2 = {  # email: (balance_status, reserved_credits, spendable), step10/D2_cac63b958.log
    "a.individual.pin@x.example": (None, 1500, 15750),
    "b.licteacher.pin@x.example": (None, 0, 8999),
    "c.schooladmin.pin@x.example": (None, 0, 5000),
    "d.student.pin@x.example": ("out", 0, 0),
    "f.nosettings.pin@x.example": ("out", 0, 0),
    "g.empty.pin@x.example": ("out", 0, 0),
}

raw = PIN.read_text()
pin = json.loads(raw)
assert json.dumps(pin, indent=2, sort_keys=True) + "\n" == raw, "pin not in writer form"
assert sorted(pin) == sorted([*D2, "e.nowallet.pin@x.example"])
assert pin["e.nowallet.pin@x.example"]["credit_wallet"] is None
for email, (status, reserved, spendable) in D2.items():
    wallet = pin[email]["credit_wallet"]
    assert not {"balance_status", "reserved_credits", "spendable"} & set(wallet)
    wallet.update(balance_status=status, reserved_credits=reserved, spendable=spendable)
PIN.write_text(json.dumps(pin, indent=2, sort_keys=True) + "\n")
print("added 3 keys to", len(D2), "rows")
