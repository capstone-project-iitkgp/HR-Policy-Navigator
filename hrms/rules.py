"""Leave rules for the two policy sets in the corpus (DEF and BLDEDU).

Every number here comes from one of the two real HR manuals in the corpus:
  DEF    = DOC-008, Digital Empowerment Foundation HR Policy Manual v2.0
  BLDEDU = DOC-009, BLDE Association Revised HR Policy 2020
The `ref` field names the section. Where BLDEDU contradicts itself, the value
used is noted in `note` (see docs/hrms_design.md).

This file is the single source of truth: seed.py copies it into the
leave_types table and service.py reads from it.
"""

import re
from datetime import date, timedelta

# --- employee categories -------------------------------------------------
DEF_CATEGORIES = ["FT_PERMANENT", "FT_PROBATION", "FT_CONTRACT", "PART_TIME"]
BLDEDU_CATEGORIES = [
    "TEACHING", "NON_TEACHING", "PRINCIPAL",
    "TEACHING_PROBATION", "NON_TEACHING_PROBATION",
]
CATEGORIES = {"DEF": DEF_CATEGORIES, "BLDEDU": BLDEDU_CATEGORIES}

DEF_ALL = DEF_CATEGORIES
BLDEDU_PERMANENT = ["TEACHING", "NON_TEACHING", "PRINCIPAL"]

# --- the rules -----------------------------------------------------------
# credit: how the allowance is credited
#   monthly      accrues every month (annual / 12 per month)
#   half_yearly  half on 1 Jan, half on 1 Jul
#   annual       whole year on 1 Jan (pro rata for joiners)
#   event        full allowance is available once eligible (maternity, etc.)
#   earned       earned case by case (comp-off); value comes from seed data
# Values for annual / max_stretch can be a dict keyed by category.
LEAVES = {
    "DEF": {
        "CL": dict(
            name="Casual Leave", annual=8, credit="half_yearly", carry_cap=0,
            categories=DEF_ALL, max_stretch=5, min_notice=0, max_backdate=5,
            half_day=True, doc_rule=None, pay_factor=1,
            ref="DEF HR Manual 5.2.1, 5.5",
        ),
        "SL": dict(
            name="Sick Leave", annual=10, credit="half_yearly", carry_cap=0,
            categories=DEF_ALL, max_stretch=10, min_notice=0, max_backdate=5,
            half_day=True, no_advance=True,
            doc_rule="Medical certificate from the 3rd instance (after 2 full days in the calendar year)",
            pay_factor=1, ref="DEF HR Manual 5.2.2, 5.4.3",
        ),
        "EL": dict(
            name="Earned Leave", annual=15, credit="monthly", carry_cap=150,
            categories=["FT_PERMANENT"], max_stretch=30, min_notice=15,
            max_backdate=0, half_day=False, doc_rule=None, pay_factor=1,
            min_days=1, ref="DEF HR Manual 5.2.3, 5.3, 5.4.4",
        ),
        "COFF": dict(
            name="Compensatory Off", annual=0, credit="earned", carry_cap=5,
            categories=["FT_PERMANENT", "FT_CONTRACT"],
            grade_pattern=r"^(W0[1-7]|S(0[1-9]|10)|E0[1-3])$",
            max_stretch=10, min_notice=3, max_backdate=0, half_day=True,
            doc_rule="Attendance or Official Duty application as proof of work on the non-working day",
            pay_factor=1, ref="DEF HR Manual 5.2.4",
            note="Max 15 earned per half-year, valid 6 months, 5 carry forward.",
        ),
        "MAT": dict(
            name="Maternity Leave", annual=90, credit="event", carry_cap=0,
            categories=["FT_PERMANENT"], gender="F", max_children=1,
            max_stretch=90, min_notice=0, max_backdate=15, half_day=False,
            doc_rule="Medical certificate mandatory", pay_factor=1,
            min_days=15, calendar_days=True, doc_always=True,
            ref="DEF HR Manual 5.2.5, 5.4.6",
            note="Fewer than 2 surviving children and 80 days worked in the 12 months before delivery.",
        ),
        "PAT": dict(
            name="Paternity Leave", annual=10, credit="event", carry_cap=0,
            categories=["FT_PERMANENT"], gender="M", max_children=1,
            max_stretch=10, min_notice=0, max_backdate=15, half_day=False,
            doc_rule="Medical certificate mandatory", pay_factor=1,
            min_days=1, doc_always=True,
            ref="DEF HR Manual 5.2.6, 5.4.9",
        ),
    },
    "BLDEDU": {
        "EL": dict(
            name="Earned Leave",
            annual={"TEACHING": 10, "NON_TEACHING": 30, "PRINCIPAL": 30,
                    "TEACHING_PROBATION": 12},
            credit="monthly", carry_cap=180,
            categories=["TEACHING", "NON_TEACHING", "PRINCIPAL", "TEACHING_PROBATION"],
            max_stretch={"TEACHING": 5, "NON_TEACHING": 15, "PRINCIPAL": 15,
                         "TEACHING_PROBATION": 5},
            min_notice=7, max_backdate=0, half_day=False, doc_rule=None,
            pay_factor=1, ref="BLDEDU HR Policy 5.A, 'Leave Entitlements' table, 5.F",
            note="CONFLICT: the table says 30 days for everyone, section 5.A says 10 for "
                 "teaching staff. Section values used. Probationary teaching staff get 1 day a month.",
        ),
        "CL": dict(
            name="Casual Leave", annual=15, credit="annual", carry_cap=0,
            categories=BLDEDU_PERMANENT, max_stretch=5, min_notice=0,
            max_backdate=3, half_day=True, doc_rule=None, pay_factor=1,
            ref="BLDEDU HR Policy 5.B, 'Leave Entitlements' table",
            note="CONFLICT: elsewhere the policy mentions 7 days per half-year. Table value used. "
                 "Cannot be combined with other leave except Special Casual Leave.",
        ),
        "COMMUTED": dict(
            name="Commuted Leave (half pay)", annual=10, credit="annual", carry_cap=120,
            categories=BLDEDU_PERMANENT, max_stretch=None, min_notice=7,
            max_backdate=0, half_day=False,
            doc_rule="Medical certificate required", pay_factor=2,
            doc_always=True, ref="BLDEDU HR Policy 5.C, table",
            note="CONFLICT: 5.C says 20 days per completed year; the table says 10. Table value used. "
                 "Debited at twice the days taken.",
        ),
        "SPECIAL_CL": dict(
            name="Special Casual Leave", annual=15, credit="event", carry_cap=0,
            categories=["TEACHING"], max_stretch=15, min_notice=0,
            max_backdate=0, half_day=False,
            doc_rule="Attendance certificate", pay_factor=1,
            doc_always=True, ref="BLDEDU HR Policy 5.D, 9",
        ),
        "BEREAVEMENT": dict(
            name="Bereavement Leave", annual=3, credit="event", carry_cap=0,
            categories=BLDEDU_PERMANENT, max_stretch=3, min_notice=0,
            max_backdate=15, half_day=False, doc_rule=None, pay_factor=1,
            ref="BLDEDU HR Policy 8",
            note="3 working days per financial year, within 15 days of the death.",
        ),
        "MAT": dict(
            name="Maternity Leave", annual=182, credit="event", carry_cap=0,
            categories=BLDEDU_PERMANENT, gender="F", max_children=1,
            max_stretch=182, min_notice=70, max_backdate=0, half_day=False,
            doc_rule="Doctor's certificate confirming pregnancy", pay_factor=1,
            calendar_days=True, doc_always=True, ref="BLDEDU HR Policy 6",
            note="26 weeks. UNVERIFIED reading of 'only up to two existing child' as fewer than 2 existing children.",
        ),
    },
}


# --- helpers -------------------------------------------------------------
def _pick(value, category):
    """Return value, or value[category] when it is a per-category dict."""
    if isinstance(value, dict):
        return value.get(category)
    return value


def _d(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def leave_codes(org):
    return list(LEAVES[org].keys())


def eligible(emp, code):
    """Return (True, "") or (False, reason). `emp` is a dict with the employee columns."""
    rule = LEAVES.get(emp["policy_org"], {}).get(code)
    if rule is None:
        return False, "%s is not a leave type under the %s policy." % (code, emp["policy_org"])
    if emp["category"] not in rule["categories"]:
        return False, "%s is not available to category %s (%s)." % (
            rule["name"], emp["category"], rule["ref"])
    if rule.get("gender") and emp["gender"] != rule["gender"]:
        return False, "%s applies to gender %s only (%s)." % (
            rule["name"], rule["gender"], rule["ref"])
    if "max_children" in rule and emp["num_children"] > rule["max_children"]:
        return False, "%s needs fewer than %d existing children (%s)." % (
            rule["name"], rule["max_children"] + 1, rule["ref"])
    if rule.get("grade_pattern") and not re.match(rule["grade_pattern"], emp.get("grade") or ""):
        return False, "%s is limited to certain grades (%s)." % (rule["name"], rule["ref"])
    return True, ""


def annual_for(emp, code):
    """Full-year allowance for this employee, 0 if not eligible."""
    ok, _ = eligible(emp, code)
    if not ok:
        return 0.0
    return float(_pick(LEAVES[emp["policy_org"]][code]["annual"], emp["category"]) or 0)


def first_month_fraction(org, join_date):
    """Share of the joining month's credit (DEF 5.3, BLDEDU 5.A).

    DEF: full month if joined on the 1st-15th, nothing otherwise.
    BLDEDU: full month if joined on or before the 15th, half otherwise.
    """
    day = _d(join_date).day
    if day <= 15:
        return 1.0
    return 0.0 if org == "DEF" else 0.5


def _months_credited(org, join, upto):
    """Months of credit from the start of upto.year up to and including upto.month."""
    if join.year < upto.year:
        return float(upto.month)
    if join.year > upto.year or join.month > upto.month:
        return 0.0
    return first_month_fraction(org, join) + (upto.month - join.month)


def accrued_to_date(emp, code, today):
    """Days credited so far in today's calendar year (before carry-forward and usage)."""
    annual = annual_for(emp, code)
    if annual == 0:
        return 0.0
    org = emp["policy_org"]
    kind = LEAVES[org][code]["credit"]
    join = _d(emp["join_date"])
    today = _d(today)
    if kind == "event":
        return annual
    if kind == "earned":
        return 0.0
    if kind == "monthly":
        return round(annual / 12 * _months_credited(org, join, today), 2)
    if kind == "annual":
        return round(annual / 12 * _months_credited(org, join, date(today.year, 12, 1)), 2)
    if kind == "half_yearly":
        total = 0.0
        for first_month, last_month in ((1, 6), (7, 12)):
            if today < date(today.year, first_month, 1):
                continue
            if join <= date(today.year, first_month, 1):
                total += annual / 2
            elif join.year == today.year and join.month <= last_month:
                months = first_month_fraction(org, join) + (last_month - join.month)
                total += annual / 12 * months
        return round(total, 2)
    raise ValueError("unknown credit kind: %s" % kind)


# --- applying for leave --------------------------------------------------
def min_days(emp, code):
    """Smallest request allowed: the rule's own minimum, else half a day (a full day if no half days)."""
    rule = LEAVES[emp["policy_org"]][code]
    if "min_days" in rule:
        return float(rule["min_days"])
    return 0.5 if rule.get("half_day") else 1.0


def max_stretch(emp, code):
    """Longest request allowed in one go, or None for no limit."""
    value = _pick(LEAVES[emp["policy_org"]][code]["max_stretch"], emp["category"])
    return None if value is None else float(value)


def weekly_off(emp, day):
    """Sunday is always off; Saturday is off too on a 5-day work week (DEF 4.1)."""
    if day.weekday() == 6:
        return True
    return day.weekday() == 5 and float(emp["work_week_days"]) <= 5


def count_leave_days(emp, code, start, end, half_day, holiday_dates):
    """Work out how many leave days a request uses.

    * Weekly offs and holidays inside the range are not counted, except for
      maternity leave, which counts every calendar day (DEF 5.2.5).
    * DEF sandwich rule (5.4.1.5): leave on a working Friday and the working Monday
      after it also counts Saturday and Sunday.
    * A half day is 0.5 and only valid for a single working day.
    `holiday_dates` is a set of date objects (national / public / compulsory holidays).
    """
    start, end = _d(start), _d(end)
    rule = LEAVES[emp["policy_org"]][code]
    counted, skipped = [], []
    day = start
    while day <= end:
        if rule.get("calendar_days"):
            counted.append(day)
        elif day in holiday_dates:
            skipped.append((day, "holiday"))
        elif weekly_off(emp, day):
            skipped.append((day, "weekly off"))
        else:
            counted.append(day)
        day += timedelta(days=1)
    sandwiched = []
    if emp["policy_org"] == "DEF" and not rule.get("calendar_days"):
        for friday in counted:
            if friday.weekday() == 4 and friday + timedelta(days=3) in counted:
                for off_day in (friday + timedelta(days=1), friday + timedelta(days=2)):
                    if (off_day, "weekly off") in skipped:
                        sandwiched.append(off_day)
    days = float(len(counted) + len(sandwiched))
    if half_day:
        days = 0.5 if counted else 0.0
    return dict(days=days, counted=counted, sandwiched=sandwiched,
                skipped=[(d.isoformat(), why) for d, why in skipped])


def needs_document(emp, code, used_so_far):
    """(True, note) when a supporting document must be attached before submitting."""
    rule = LEAVES[emp["policy_org"]][code]
    if rule.get("doc_always"):
        return True, rule["doc_rule"]
    if emp["policy_org"] == "DEF" and code == "SL" and used_so_far >= 2:
        # DEF 5.4.3: after two full sick days this year, a medical certificate is mandatory
        return True, "Medical certificate required (2 or more sick days already taken this year)."
    return False, rule.get("doc_rule") or ""
