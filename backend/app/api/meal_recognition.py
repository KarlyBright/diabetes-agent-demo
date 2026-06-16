from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.orm import Session

from app.db.database import SessionLocal

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


MEAL_RECOGNITION_PROMPT = """你是一位专业的食物营养分析师。请分析这张食物照片，返回以下 JSON 格式：
{
  "foods": [
    {
      "name": "食物名称",
      "portion_g": 估算克数,
      "calories": 估算热量(kcal),
      "carbs_g": 碳水化合物(g),
      "protein_g": 蛋白质(g),
      "fat_g": 脂肪(g),
      "gi": GI值估算,
      "gl": GL值估算
    }
  ],
  "total_calories": 总热量,
  "total_carbs": 总碳水,
  "meal_assessment": "对糖尿病患者的简短评价"
}
请尽量准确估算份量和营养成分。如果无法识别，返回 {"foods": [], "error": "无法识别食物"}。"""


@router.post("/meal/recognize")
async def recognize_meal(
    user_id: int = Form(default=1),
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if image.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(status_code=400, detail="仅支持 JPEG/PNG/WebP 格式图片")

    content = await image.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片大小不能超过 5MB")

    # Demo 阶段：返回模拟识别结果
    # 生产环境应调用多模态 LLM 或专业食物识别 API
    mock_result = {
        "foods": [
            {
                "name": "米饭",
                "portion_g": 150,
                "calories": 174,
                "carbs_g": 39.0,
                "protein_g": 3.9,
                "fat_g": 0.5,
                "gi": 73,
                "gl": 28.5,
            },
            {
                "name": "青菜",
                "portion_g": 100,
                "calories": 25,
                "carbs_g": 3.0,
                "protein_g": 2.0,
                "fat_g": 0.3,
                "gi": 15,
                "gl": 0.5,
            },
        ],
        "total_calories": 199,
        "total_carbs": 42.0,
        "meal_assessment": "主食偏多，建议增加蛋白质摄入",
        "recognition_source": "demo_mock",
        "prompt_for_llm": MEAL_RECOGNITION_PROMPT,
    }

    return {"message": "食物识别完成（Demo 模式）", "data": mock_result}


@router.post("/meal/confirm")
def confirm_meal_recognition(
    user_id: int = 1,
    db: Session = Depends(get_db),
):
    return {
        "message": "识别结果已确认并入库",
        "data": {"status": "confirmed"},
    }
