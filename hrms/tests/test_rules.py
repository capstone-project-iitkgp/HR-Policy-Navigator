"""Rule tests. Golden cases come from the eval set (Q001, Q007, Q029, Q048, Q050)."""

from datetime import date

from hrms import rules

TODAY = date(2026, 11, 16)


def emp(org="DEF", category="FT_PERMANENT", join="2020-01-06", gender="M", kids=0, grade="S03"):
    return dict(policy_org=org, category=category, join_date=join, gender=gender,
                num_children=kids, grade=grade)


def test_def_join_after_15th_gets_no_credit_for_joining_month():
    # Q001: joined 18 March, so earned leave starts accruing in April: 8 months x 1.25
    assert rules.first_month_fraction("DEF", "2026-03-18") == 0.0
    assert rules.accrued_to_date(emp(join="2026-03-18"), "EL", TODAY) == 10.0


def test_def_join_on_or_before_15th_gets_full_credit_for_joining_month():
    assert rules.first_month_fraction("DEF", "2026-03-14") == 1.0
    assert rules.accrued_to_date(emp(join="2026-03-14"), "EL", TODAY) == 11.25


def test_bldedu_join_after_15th_gets_half_credit_for_joining_month():
    # Q007: non-teaching staff, joined 18 March: 30/12 = 2.5 a month, 0.5 + 8 months
    e = emp(org="BLDEDU", category="NON_TEACHING", join="2026-03-18")
    assert rules.first_month_fraction("BLDEDU", "2026-03-18") == 0.5
    assert rules.accrued_to_date(e, "EL", TODAY) == 21.25


def test_def_casual_leave_is_8_days_no_carry_forward():
    # Q050
    r = rules.LEAVES["DEF"]["CL"]
    assert r["annual"] == 8 and r["carry_cap"] == 0 and r["credit"] == "half_yearly"
    assert rules.accrued_to_date(emp(), "CL", TODAY) == 8.0


def test_def_casual_leave_credited_half_yearly():
    assert rules.accrued_to_date(emp(), "CL", date(2026, 5, 1)) == 4.0
    assert rules.accrued_to_date(emp(), "CL", date(2026, 7, 1)) == 8.0


def test_def_midyear_joiner_gets_prorated_casual_leave():
    # joined 12 March: March..June = 4 months of 8/12, then the full July credit of 4
    assert rules.accrued_to_date(emp(join="2026-03-12"), "CL", TODAY) == 6.67


def test_bldedu_earned_leave_depends_on_staff_category():
    # Q048: section 5.A says 10 days for teaching and 30 for non-teaching
    assert rules.annual_for(emp("BLDEDU", "TEACHING"), "EL") == 10
    assert rules.annual_for(emp("BLDEDU", "NON_TEACHING"), "EL") == 30


def test_earned_leave_not_available_during_def_probation():
    ok, reason = rules.eligible(emp(category="FT_PROBATION"), "EL")
    assert not ok and "FT_PROBATION" in reason


def test_maternity_needs_a_woman_with_fewer_than_two_children():
    assert rules.eligible(emp(gender="F", kids=1), "MAT")[0]
    assert not rules.eligible(emp(gender="F", kids=2), "MAT")[0]
    assert not rules.eligible(emp(gender="M", kids=0), "MAT")[0]


def test_paternity_is_for_men_only():
    assert rules.eligible(emp(gender="M", kids=1), "PAT")[0]
    assert not rules.eligible(emp(gender="F", kids=0), "PAT")[0]


def test_bldedu_has_no_sick_or_paternity_leave():
    ok, reason = rules.eligible(emp("BLDEDU", "TEACHING"), "SL")
    assert not ok and "not a leave type" in reason
    assert "PAT" not in rules.LEAVES["BLDEDU"]


def test_comp_off_is_limited_to_certain_grades():
    assert rules.eligible(emp(grade="S04"), "COFF")[0]
    assert not rules.eligible(emp(grade="M01"), "COFF")[0]


def test_every_rule_names_its_source_section():
    for org, leaves in rules.LEAVES.items():
        for code, r in leaves.items():
            assert r["ref"], "%s %s has no source reference" % (org, code)
