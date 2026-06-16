import re


def parse_medication_instruction(text: str, user_id: int = 1):
    """
    先做比赛版最小解析：
    支持类似：
    - 每天早餐后提醒我吃二甲双胍0.5g
    - 每天晚上8点提醒我吃阿卡波糖1片
    """

    result = {
        "user_id": user_id,
        "drug_name": None,
        "dosage": None,
        "time_text": None,
        "remind_time": None,
        "frequency": "daily",
        "confirm_status": "pending"
    }

    # 药名简单识别
    drug_candidates = ["二甲双胍", "阿卡波糖", "格列美脲", "胰岛素"]
    for drug in drug_candidates:
        if drug in text:
            result["drug_name"] = drug
            break

    # 剂量简单识别
    dosage_match = re.search(r"(\d+(\.\d+)?\s*(g|mg|片|单位))", text)
    if dosage_match:
        result["dosage"] = dosage_match.group(1)

    # 服药时机 / 时间简单识别
    if "早餐后" in text:
        result["time_text"] = "早餐后"
        result["remind_time"] = "08:00"
    elif "午饭后" in text or "午餐后" in text:
        result["time_text"] = "午餐后"
        result["remind_time"] = "12:30"
    elif "晚饭后" in text or "晚餐后" in text:
        result["time_text"] = "晚餐后"
        result["remind_time"] = "18:30"
    else:
        time_match = re.search(r"(\d{1,2})[:点](\d{0,2})", text)
        if time_match:
            hour = int(time_match.group(1))
            minute = time_match.group(2)
            minute = "00" if minute == "" else minute.zfill(2)
            result["time_text"] = f"{hour}:{minute}"
            result["remind_time"] = f"{str(hour).zfill(2)}:{minute}"

    # 简单校验
    missing_fields = []
    for field in ["drug_name", "dosage", "remind_time"]:
        if not result[field]:
            missing_fields.append(field)

    result["missing_fields"] = missing_fields
    result["is_valid"] = len(missing_fields) == 0

    return result