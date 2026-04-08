"""Email validation — verify before sending to protect domain reputation.

Checks:
1. Format valid
2. MX record exists (domain can receive email)
3. Not a disposable/temp email domain
"""

import re
import logging
import dns.resolver

log = logging.getLogger("outreach.validator")

DISPOSABLE_DOMAINS = {
    'mailinator.com', 'guerrillamail.com', 'tempmail.com', 'throwaway.email',
    'yopmail.com', 'sharklasers.com', 'guerrillamailblock.com', 'grr.la',
    'dispostable.com', 'trashmail.com', 'temp-mail.org', 'fakeinbox.com',
    'mailnesia.com', 'maildrop.cc', 'discard.email', 'tempail.com',
}

EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')


def validate_email(email):
    """Validate email format + MX record. Returns (valid, reason)."""
    email = email.lower().strip()

    # Format check
    if not EMAIL_REGEX.match(email):
        return False, "invalid format"

    domain = email.split('@')[1]

    # Disposable check
    if domain in DISPOSABLE_DOMAINS:
        return False, "disposable domain"

    # MX record check
    try:
        mx = dns.resolver.resolve(domain, 'MX')
        if not mx:
            return False, "no MX record"
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        return False, "domain does not exist"
    except dns.resolver.NoNameservers:
        return False, "DNS failure"
    except Exception:
        # DNS timeout — give benefit of doubt for common domains
        if domain in ('gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com'):
            return True, "ok (DNS timeout, known domain)"
        return False, "DNS error"

    return True, "ok"


def validate_batch(emails):
    """Validate a list of emails. Returns (valid_list, invalid_list)."""
    valid = []
    invalid = []
    for email in emails:
        is_valid, reason = validate_email(email)
        if is_valid:
            valid.append(email)
        else:
            invalid.append((email, reason))
            log.info(f"  Invalid: {email} — {reason}")
    return valid, invalid
