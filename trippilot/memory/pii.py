"""记忆内容的确定性 PII 分类。"""

from __future__ import annotations

import re


_PII_MARKERS = re.compile(
    r"住址|家庭地址|我家|身份证|护照|银行卡|卡号|电话|手机|手机号|"
    r"生日|出生日期|密码|验证码"
)

_PRECISE_ADDRESS = re.compile(
    r"(?:[\u4e00-\u9fa5]{2,}(?:省|自治区|市))"
    r"[\u4e00-\u9fa5]{1,12}(?:区|县)"
    r"[\u4e00-\u9fa5A-Za-z0-9]{1,24}(?:路|街|道|大街|巷)"
    r"\d+[号弄]"
)

_BUILDING_ADDRESS = re.compile(
    r"[\u4e00-\u9fa5A-Za-z0-9]{1,30}(?:大厦|小区)"
    r"[^，。！？\n]{0,24}(?:\d+号?楼|\d+单元|\d{2,5}室)"
)

_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_IDENTITY_CARD = re.compile(
    r"(?<!\d)(?:\d{17}[\dXx]|\d{15})(?![\dXx])"
)
_PASSPORT = re.compile(
    r"(?<![A-Za-z0-9])(?:[EeGgDd]\d{8}|[Pp][Ee]\d{7}|[Ss]\d{7,8})"
    r"(?![A-Za-z0-9])"
)
_BANK_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){15,18}\d(?!\d)")
_LICENSE_PLATE = re.compile(
    r"(?<![\u4e00-\u9fa5A-Za-z0-9])"
    r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼]"
    r"[A-Z][A-HJ-NP-Z0-9]{5,6}"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)

_PII_PATTERNS = (
    _PII_MARKERS,
    _PRECISE_ADDRESS,
    _BUILDING_ADDRESS,
    _PHONE,
    _IDENTITY_CARD,
    _PASSPORT,
    _BANK_CARD,
    _LICENSE_PLATE,
)


def classify_pii(content: str) -> bool:
    """内容含明确或疑似 PII 时返回 True。"""
    if not content:
        return False
    return any(pattern.search(content) for pattern in _PII_PATTERNS)


__all__ = ["classify_pii"]
