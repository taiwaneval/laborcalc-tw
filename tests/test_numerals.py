import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from numerals import int_to_cn, format_salary


def test_int_to_cn_matches_generate_py_behaviour():
    assert int_to_cn(0) == "零"
    assert int_to_cn(8) == "八"
    assert int_to_cn(10) == "十"
    assert int_to_cn(12) == "十二"
    assert int_to_cn(36000) == "三萬六千"
    assert int_to_cn(115200) == "十一萬五千二百"
    assert int_to_cn(30005) == "三萬零五"


def test_format_salary_styles():
    assert format_salary(36000, "arabic") == "36,000"
    assert format_salary(36000, "arabic_plain") == "36000"
    assert format_salary(30000, "wan") == "3萬"
    assert format_salary(35000, "wan") == "3萬5"
    assert format_salary(35067, "wan") == "35,067"
    assert format_salary(36000, "cn") == "三萬六千"
