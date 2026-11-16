"""One-off generator for employees.csv and leave_usage.csv.

Run once from the Code/ folder:  python3 -I hrms/seed_data/generate_seed_data.py
The CSV files are the source of truth after that; edit them by hand if needed.
Re-running overwrites them. All people are fictional.

Edge-case employees are written by hand below (they mirror eval questions);
the "used" and "carried forward" numbers for everyone else come from a fixed
random seed, so the output is the same every time.
"""

import csv
import random
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))  # Code/ so `import hrms` works under -I

from hrms import rules  # noqa: E402

TODAY = date(2026, 11, 16)

EMP_COLS = ["emp_id", "name", "email", "policy_org", "category", "grade", "designation",
            "department", "manager_id", "join_date", "gender", "num_children",
            "work_week_days", "status", "notice_start", "role"]

# emp_id, name, category, grade, designation, department, manager, join, gender,
# children, work_week_days, status, notice_start, role
DEF_ROWS = [
    ("D001", "Meera Iyer", "FT_PERMANENT", "M01", "HR Manager", "Human Resources", "D002", "2018-04-02", "F", 2, 5, "active", "", "hr"),
    ("D002", "Rohan Deshmukh", "FT_PERMANENT", "M02", "Executive Director", "Management", "", "2015-06-01", "M", 2, 5, "active", "", "manager"),
    ("D003", "Anita Rao", "FT_PERMANENT", "S09", "Programme Lead", "Programmes", "D002", "2019-08-12", "F", 1, 5, "active", "", "manager"),
    ("D004", "Vikram Shetty", "FT_PERMANENT", "S08", "Finance Lead", "Finance", "D002", "2017-01-16", "M", 2, 5, "active", "", "manager"),
    ("D005", "Farah Khan", "FT_PERMANENT", "S09", "IT Lead", "IT", "D002", "2020-02-03", "F", 0, 5, "active", "", "manager"),
    ("D006", "Sanjay Patil", "FT_PERMANENT", "S03", "Programme Officer", "Programmes", "D003", "2021-06-14", "M", 2, 5, "active", "", "employee"),
    ("D007", "Lakshmi Nair", "FT_PERMANENT", "W05", "Field Coordinator", "Programmes", "D003", "2022-01-10", "F", 1, 6, "active", "", "employee"),
    ("D008", "Imran Sheikh", "FT_PERMANENT", "S04", "Programme Officer", "Programmes", "D003", "2023-03-06", "M", 0, 5, "active", "", "employee"),
    ("D009", "Divya Menon", "FT_PERMANENT", "E02", "Programme Associate", "Programmes", "D003", "2024-05-20", "F", 0, 5, "active", "", "employee"),
    # Q001: joined 18 March, no credit for March
    ("D010", "Arjun Verma", "FT_PERMANENT", "S02", "Programme Officer", "Programmes", "D003", "2026-03-18", "M", 0, 5, "active", "", "employee"),
    # joined 12 March: full credit for March
    ("D011", "Kavya Reddy", "FT_PERMANENT", "S02", "Programme Officer", "Programmes", "D003", "2026-03-12", "F", 0, 5, "active", "", "employee"),
    # Q029: about 16 months of service, not yet eligible for work from home (2 years)
    ("D012", "Nikhil Joshi", "FT_PERMANENT", "S05", "Accounts Executive", "Finance", "D004", "2025-07-10", "M", 0, 5, "active", "", "employee"),
    # about 26 months of service, eligible for work from home
    ("D013", "Pooja Bansal", "FT_PERMANENT", "S05", "Accounts Executive", "Finance", "D004", "2024-09-02", "F", 0, 5, "active", "", "employee"),
    # probation: no earned leave
    ("D014", "Rahul Khanna", "FT_PROBATION", "W03", "Data Entry Operator", "Finance", "D004", "2026-09-01", "M", 0, 5, "active", "", "employee"),
    # serving notice: leave becomes loss of pay
    ("D015", "Sunita Das", "FT_PERMANENT", "S04", "Finance Associate", "Finance", "D004", "2019-11-04", "F", 2, 5, "notice", "2026-11-02", "employee"),
    # maternity-eligible (1 child) and not eligible (2 children)
    ("D016", "Neha Kulkarni", "FT_PERMANENT", "S06", "Programme Officer", "IT", "D005", "2021-02-22", "F", 1, 5, "active", "", "employee"),
    ("D017", "Ritu Saxena", "FT_PERMANENT", "S06", "Systems Analyst", "IT", "D005", "2020-09-14", "F", 2, 5, "active", "", "employee"),
    # paternity-eligible
    ("D018", "Karan Malhotra", "FT_PERMANENT", "S06", "Developer", "IT", "D005", "2022-04-04", "M", 1, 5, "active", "", "employee"),
    # earned leave carried forward close to the 150-day cap
    ("D019", "Deepak Chauhan", "FT_PERMANENT", "S07", "Senior Analyst", "IT", "D005", "2016-03-01", "M", 2, 5, "active", "", "employee"),
    # all casual leave used (zero balance), 2 sick days used (certificate needed from the 3rd instance)
    ("D020", "Shreya Gupta", "FT_PERMANENT", "S03", "QA Engineer", "IT", "D005", "2023-07-17", "F", 0, 5, "active", "", "employee"),
    ("D021", "Mohan Pillai", "PART_TIME", "W02", "Office Assistant", "Finance", "D004", "2022-10-03", "M", 2, 5, "active", "", "employee"),
    ("D022", "Tanvi Jain", "FT_CONTRACT", "E01", "Consultant", "Finance", "D004", "2025-12-01", "F", 0, 5, "active", "", "employee"),
    # 6-day work week, handy for sandwich-rule examples
    ("D023", "Alok Mishra", "FT_PERMANENT", "S08", "Project Manager", "IT", "D005", "2018-09-10", "M", 1, 6, "active", "", "employee"),
    # has a pending request (see leave_requests.csv)
    ("D024", "Prerna Bose", "FT_PERMANENT", "S04", "Programme Associate", "Programmes", "D003", "2023-11-20", "F", 0, 5, "active", "", "employee"),
]

BLDEDU_ROWS = [
    ("B001", "Dr. Suresh Patil", "PRINCIPAL", "P1", "Principal", "Administration", "", "2012-06-01", "M", 2, 6, "active", "", "manager"),
    ("B002", "Shobha Kulkarni", "NON_TEACHING", "A2", "HR Officer", "Administration", "B001", "2016-07-11", "F", 2, 6, "active", "", "hr"),
    ("B003", "Dr. Anand Hiremath", "TEACHING", "T5", "HOD Computer Science", "Computer Science", "B001", "2010-08-02", "M", 2, 6, "active", "", "manager"),
    ("B004", "Dr. Rekha Jadhav", "TEACHING", "T5", "HOD Mechanical", "Mechanical", "B001", "2011-08-01", "F", 2, 6, "active", "", "manager"),
    ("B005", "Manjunath Naik", "NON_TEACHING", "A3", "Accounts Officer", "Accounts", "B001", "2014-04-01", "M", 1, 6, "active", "", "manager"),
    ("B006", "Dr. Vidya Kulkarni", "TEACHING", "T3", "Associate Professor", "Computer Science", "B003", "2014-08-04", "F", 2, 6, "active", "", "employee"),
    ("B007", "Santosh Gaonkar", "NON_TEACHING", "A4", "Library Clerk", "Library", "B005", "2017-03-13", "M", 1, 6, "active", "", "employee"),
    ("B008", "Dr. Pallavi Rane", "TEACHING", "T2", "Assistant Professor", "Mechanical", "B004", "2019-08-19", "F", 1, 6, "active", "", "employee"),
    ("B009", "Ganesh Surve", "NON_TEACHING", "A4", "Peon", "Administration", "B005", "2015-01-05", "M", 2, 6, "active", "", "employee"),
    # Q007: joined 18 March, non-teaching: half credit for March
    ("B010", "Pradeep Shinde", "NON_TEACHING", "A4", "Lab Assistant", "Mechanical", "B005", "2026-03-18", "M", 0, 6, "active", "", "employee"),
    # joined 12 March: full credit for March
    ("B011", "Dr. Anjali Rao", "TEACHING", "T2", "Assistant Professor", "Computer Science", "B003", "2026-03-12", "F", 0, 6, "active", "", "employee"),
    # probation teaching: 1 day a month
    ("B012", "Vinayak Kamat", "TEACHING_PROBATION", "T1", "Assistant Professor", "Computer Science", "B003", "2025-08-18", "M", 0, 6, "active", "", "employee"),
    # probation non-teaching: no leave except emergencies
    ("B013", "Sneha Hegde", "NON_TEACHING_PROBATION", "A5", "Clerk", "Accounts", "B005", "2026-01-12", "F", 0, 6, "active", "", "employee"),
    # Q048: non-teaching earned leave is 30 days
    ("B014", "Ramesh Gouda", "NON_TEACHING", "A3", "Office Superintendent", "Administration", "B005", "2013-06-03", "M", 2, 6, "active", "", "employee"),
    # bereavement leave fully used
    ("B015", "Dr. Latha Bhat", "TEACHING", "T3", "Associate Professor", "Mechanical", "B004", "2015-07-20", "F", 2, 6, "active", "", "employee"),
    # maternity-eligible (1 child) and not eligible (2 children, B019)
    ("B016", "Dr. Priya Desai", "TEACHING", "T2", "Assistant Professor", "Mechanical", "B004", "2021-08-09", "F", 1, 6, "active", "", "employee"),
    # commuted leave used (debited at 2x)
    ("B017", "Dr. Mahesh Walvekar", "TEACHING", "T4", "Professor", "Computer Science", "B003", "2009-08-03", "M", 2, 6, "active", "", "employee"),
    # serving notice
    ("B018", "Kiran Patil", "NON_TEACHING", "A4", "Library Assistant", "Library", "B005", "2018-02-05", "M", 1, 6, "active", "", "employee"),
    ("B019", "Dr. Savita Joshi", "TEACHING", "T2", "Assistant Professor", "Mechanical", "B004", "2020-08-17", "F", 2, 6, "active", "", "employee"),
    # earned leave carried forward close to the 180-day cap
    ("B020", "Basavaraj Nandi", "NON_TEACHING", "A4", "Lab Technician", "Mechanical", "B005", "2012-09-10", "M", 2, 6, "active", "", "employee"),
    ("B021", "Dr. Farhan Mulla", "TEACHING", "T2", "Assistant Professor", "Computer Science", "B003", "2022-08-22", "M", 0, 6, "active", "", "employee"),
    ("B022", "Geeta Kamble", "NON_TEACHING", "A4", "Staff Nurse", "Administration", "B005", "2019-05-06", "F", 1, 6, "active", "", "employee"),
    ("B023", "Dr. Nitin Bhosale", "TEACHING", "T2", "Assistant Professor", "Mechanical", "B004", "2024-08-12", "M", 0, 6, "active", "", "employee"),
    # has a pending request
    ("B024", "Smita Pawar", "NON_TEACHING", "A3", "Accountant", "Accounts", "B005", "2023-01-09", "F", 0, 6, "active", "", "employee"),
]

# Hand-set usage: (emp_id, leave_code) -> dict(used, carried_forward, earned)
OVERRIDES = {
    ("D019", "EL"): dict(used=3, carried_forward=148),
    ("D020", "CL"): dict(used=8, carried_forward=0),
    ("D020", "SL"): dict(used=2, carried_forward=0),
    ("D006", "COFF"): dict(used=1, carried_forward=0, earned=3),
    ("D008", "COFF"): dict(used=0, carried_forward=0, earned=1.5),
    ("B015", "BEREAVEMENT"): dict(used=3, carried_forward=0),
    ("B017", "COMMUTED"): dict(used=8, carried_forward=0),
    ("B020", "EL"): dict(used=10, carried_forward=175),
}


def build_employees():
    employees = []
    for org, rows in (("DEF", DEF_ROWS), ("BLDEDU", BLDEDU_ROWS)):
        for r in rows:
            emp_id, name, category, grade, desig, dept, mgr, join, gender, kids, wk, status, notice, role = r
            email = name.lower().replace("dr. ", "").replace(" ", ".") + "@example.org"
            employees.append(dict(
                emp_id=emp_id, name=name, email=email, policy_org=org, category=category,
                grade=grade, designation=desig, department=dept, manager_id=mgr,
                join_date=join, gender=gender, num_children=kids, work_week_days=wk,
                status=status, notice_start=notice, role=role))
    return employees


def half(x):
    return round(x * 2) / 2


def build_usage(employees):
    rng = random.Random(42)
    rows = []
    for emp in employees:
        org = emp["policy_org"]
        for code in rules.leave_codes(org):
            if rules.annual_for(emp, code) == 0 and code != "COFF":
                continue
            ok, _ = rules.eligible(emp, code)
            if not ok:
                continue
            kind = rules.LEAVES[org][code]["credit"]
            key = (emp["emp_id"], code)
            if key in OVERRIDES:
                o = OVERRIDES[key]
                rows.append(dict(emp_id=emp["emp_id"], leave_code=code, used=o["used"],
                                 carried_forward=o["carried_forward"], earned=o.get("earned", 0)))
                continue
            if kind == "event" or kind == "earned":
                continue  # event leaves start unused; comp-off only where hand-set
            entitled = rules.accrued_to_date(emp, code, TODAY)
            used = half(rng.uniform(0, 0.6) * entitled)
            carried = 0
            if code == "EL":
                carried = rng.choice([0, 5, 10, 20, 35]) if org == "DEF" else rng.choice([0, 3, 8, 15, 30])
                if emp["category"] == "FT_PERMANENT" and emp["join_date"] >= "2026-01-01":
                    carried = 0
                if emp["category"].endswith("PROBATION"):
                    carried = 0
            if emp["join_date"] >= "2026-01-01":
                carried = 0
            rows.append(dict(emp_id=emp["emp_id"], leave_code=code, used=used,
                             carried_forward=carried, earned=0))
    return rows


def main():
    employees = build_employees()
    usage = build_usage(employees)
    with open(HERE / "employees.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=EMP_COLS)
        w.writeheader()
        w.writerows(employees)
    with open(HERE / "leave_usage.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["emp_id", "leave_code", "used", "carried_forward", "earned"])
        w.writeheader()
        w.writerows(usage)
    print("wrote %d employees, %d usage rows" % (len(employees), len(usage)))


if __name__ == "__main__":
    main()
