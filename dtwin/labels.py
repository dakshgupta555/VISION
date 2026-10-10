"""
User-facing names for the eight channels and their states. Code keeps the
short keys (oil_temp, IR, ...); anything a person reads goes through here.
"""

CHANNEL = {"IR": "R-phase current", "IY": "Y-phase current", "IB": "B-phase current",
           "VR": "R-phase voltage", "VY": "Y-phase voltage", "VB": "B-phase voltage",
           "oil_temp": "Top-oil temperature", "ambient_temp": "Ambient temperature"}

# the reading itself, for "<x> reading outside ..." phrases
READING = {**CHANNEL, "oil_temp": "Top-oil", "ambient_temp": "Ambient"}

STATE = {"ok": "trusted", "step": "trusted (step seen)", "dead": "no valid reading", "stuck": "frozen",
         "suspect": "disagrees with the other phases", "absent": "not recorded"}


def channel(ch):
    """'oil_temp' -> 'Top-oil temperature'; 'oil_temp|ambient_temp' -> '... or ...'."""
    if not ch:
        return ""
    return " or ".join(CHANNEL.get(c, c) for c in str(ch).split("|"))


def state(s):
    return STATE.get(s, s)


def inr(x):
    """Rupees with Indian digit grouping and no decimals: 899647 -> '₹8,99,647'."""
    return "₹" + group_in(round(float(x)))


def group_in(n, nd=0):
    """Indian digit grouping (lakh, crore): 1234567.8 -> '12,34,567.8'."""
    neg = n < 0
    s = f"{abs(float(n)):.{nd}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return ("-" if neg else "") + whole + ("." + frac if frac else "")
