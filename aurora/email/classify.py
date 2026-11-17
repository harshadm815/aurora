"""
Sorting emails into the categories of the plan, and signs that one may be phishing.

Gmail's own labels come first (it has seen far more mail than AURORA): SPAM, IMPORTANT and its
CATEGORY_* tabs. Then simple rules: calendar invitations are Meetings, mailing lists with an
unsubscribe link are Newsletters, senders at domains you named are School/Work, people you've written
to are Personal. Suspicious signs (failed sender checks, a look-alike address, links that say one
place and go to another, pressure about money or passwords) are reported as reasons: "possibly
suspicious", never certain.
"""

import re
from dataclasses import dataclass, field

IMPORTANT, PERSONAL, WORK, MEETINGS = "Important", "Personal", "School / Work", "Meetings"
NOTIFICATIONS, NEWSLETTERS, PROMOTIONS, SUSPICIOUS = "Notifications", "Newsletters", "Promotions", "Spam / Suspicious"
CATEGORIES = (IMPORTANT, PERSONAL, WORK, MEETINGS, NOTIFICATIONS, NEWSLETTERS, PROMOTIONS, SUSPICIOUS)

_MEETING = re.compile(r"\b(invitation|invite|meeting|webinar|calendar|zoom|google meet|teams call|appointment|"
                      r"interview|rescheduled?|agenda)\b", re.I)
_NOTIFY = re.compile(r"\b(no-?reply|notifications?|alerts?|noreply|do-not-reply|mailer-daemon|accounts?)\b", re.I)
_PRESSURE = re.compile(r"\b(verify your account|confirm your (password|identity|account)|account (will be )?"
                       r"(suspended|locked|closed)|urgent(ly)? (action|payment)|wire transfer|gift ?cards?|"
                       r"you have won|lottery|claim your (prize|reward)|update (your )?(payment|billing|kyc)|"
                       r"otp|one[- ]time password)\b", re.I)
_LINK = re.compile(r"""<a\s[^>]*href=["']https?://([^/"'?#]+)[^"']*["'][^>]*>\s*(?:https?://)?([a-z0-9.-]+\.[a-z]{2,})""",
                   re.I)
_BRANDS = ("google", "paypal", "amazon", "microsoft", "apple", "netflix", "sbi", "hdfc", "icici", "axis", "paytm",
           "phonepe", "airtel", "jio", "flipkart", "irctc", "incometax", "uidai")


@dataclass
class Sorted:
    category: str
    reasons: list[str] = field(default_factory=list)       # why (for "why is this here?")
    suspicious: list[str] = field(default_factory=list)    # signs of phishing, if any


def domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


def suspicious_signs(email, raw_html: str = "") -> list[str]:
    """Reasons an email may be phishing (empty when nothing stands out). Never a verdict."""
    signs = []
    auth = email.headers.get("Authentication-Results", "").lower()
    for check in ("spf", "dkim", "dmarc"):
        if re.search(rf"\b{check}=(fail|softfail)\b", auth):
            signs.append(f"the sender's {check.upper()} check failed (the address may be forged)")
    reply_to = email.headers.get("Reply-To", "")
    if reply_to and domain(reply_to.strip("<> ").split()[-1]) not in ("", domain(email.sender)):
        signs.append("replies would go to a different address than the sender's")
    sender_domain = domain(email.sender)
    for brand in _BRANDS:
        name = email.sender_name.lower()
        if brand in name and brand not in sender_domain:
            signs.append(f"the name says {brand.title()} but the address is at {sender_domain or 'an unknown domain'}")
            break
    if re.search(r"\d", sender_domain.split(".")[0] if sender_domain else "") and any(b in sender_domain for b in _BRANDS):
        signs.append("the sender's domain looks like an imitation of a known company")
    if _PRESSURE.search(f"{email.subject}\n{email.body[:4000]}"):
        signs.append("it pressures you about an account, payment, prize or code")
    for target, shown in _LINK.findall(raw_html or ""):
        if shown.lower() not in target.lower() and target.lower() not in shown.lower():
            signs.append(f"a link shows {shown} but goes to {target}")
            break
    return signs


def sort(email, my_address: str = "", work_domains: tuple[str, ...] = (), known: set[str] | None = None,
         raw_html: str = "") -> Sorted:
    """The email's category, with the reasons."""
    labels = set(email.labels)
    signs = suspicious_signs(email, raw_html)
    if "SPAM" in labels:
        return Sorted(SUSPICIOUS, ["Gmail put it in Spam"], signs)
    if len(signs) >= 2 or (signs and any("failed" in s for s in signs)):
        return Sorted(SUSPICIOUS, ["it shows signs of phishing"], signs)
    text = f"{email.subject}\n{email.snippet}"
    if any(a.lower().endswith((".ics",)) for a in email.attachments) or _MEETING.search(email.subject):
        return Sorted(MEETINGS, ["it's an invitation or about a meeting"], signs)
    if "CATEGORY_PROMOTIONS" in labels:
        return Sorted(PROMOTIONS, ["Gmail's Promotions tab"], signs)
    if email.headers.get("List-Unsubscribe") or email.headers.get("List-Id") or "CATEGORY_FORUMS" in labels:
        return Sorted(NEWSLETTERS, ["a mailing list (it has an unsubscribe link)"], signs)
    sender_domain = domain(email.sender)
    if work_domains and any(sender_domain == d or sender_domain.endswith("." + d) for d in work_domains):
        return Sorted(WORK, [f"from {sender_domain}, one of your school/work domains"], signs)
    if "IMPORTANT" in labels and not _NOTIFY.search(email.sender):
        return Sorted(IMPORTANT, ["Gmail marked it important"], signs)
    if "CATEGORY_UPDATES" in labels or "CATEGORY_SOCIAL" in labels or _NOTIFY.search(email.sender):
        return Sorted(NOTIFICATIONS, ["an automatic notification"], signs)
    if known and email.sender in known:
        return Sorted(PERSONAL, ["from someone you've written to"], signs)
    if "CATEGORY_PERSONAL" in labels:
        return Sorted(PERSONAL, ["Gmail's Primary tab"], signs)
    return Sorted(NOTIFICATIONS if _NOTIFY.search(text) else PERSONAL, ["by its sender and wording"], signs)
