from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field

class LoginRequest(BaseModel):
    password: str

class LoginResponse(BaseModel):
    success: bool
    message: str

class StudentBase(BaseModel):
    student_no: str
    name: str
    anonymous_code: str
    class_name: str = "六(1)班"
    focus_tier: str = "critical"
    baseline_score: float = 50.0
    current_est_score: float = 50.0
    l3_summary_override: Optional[str] = ""

class StudentResponse(StudentBase):
    id: int
    created_at: Optional[str] = None
    module_status: Optional[Dict[str, str]] = None  # calc: 'green'|'yellow'|'red', etc.
    persistent_count: int = 0  # wrong_count >= 2
    primary_weakness: str = "未评估"
    next_action: str = "安排摸底测验"
    unmastered_count: int = 0
    total_mistakes_count: int = 0

class StudentOverviewResponse(BaseModel):
    critical_count: int
    passed_rate: float
    top_error_tags: List[Dict[str, Any]]
    students: List[StudentResponse]

class UpdateStudentSummaryRequest(BaseModel):
    l3_summary: str

class ExamQuestionSchema(BaseModel):
    id: Optional[int] = None
    q_no: str
    kb_mother_id: str
    module_tag: str
    max_score: float = 5.0
    quick_error_tags: List[str] = []

class ExamPaperSchema(BaseModel):
    id: Optional[int] = None
    title: str
    unit_tag: str = ""
    exam_date: Optional[str] = None
    total_score: float = 100.0
    questions: List[ExamQuestionSchema] = []

class TickMistakeItem(BaseModel):
    q_no: str
    exam_question_id: int
    kb_mother_id: Optional[str] = ""
    module_tag: Optional[str] = "calc"
    error_tags: List[str] = []

class BatchTickRequest(BaseModel):
    paper_id: int
    student_id: int
    score: float
    mistakes: List[TickMistakeItem] = []

class MistakeRecordSchema(BaseModel):
    id: int
    student_id: int
    source_type: str
    paper_id: Optional[int] = None
    exam_question_id: Optional[int] = None
    kb_mother_id: Optional[str] = None
    module_tag: Optional[str] = None
    question_snapshot: Optional[str] = None
    error_tags: List[str] = []
    photo_url: Optional[str] = None
    ocr_note: Optional[str] = None
    face_to_face_script: Optional[str] = None
    mastery_status: str = "unmastered"
    wrong_count: int = 1
    next_review_date: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class OcrParsedQuestion(BaseModel):
    temp_id: str
    q_no: Optional[str] = None
    question_text: str
    student_answer: str
    is_wrong: bool
    score_status: str  # "correct" | "wrong" | "partial"
    error_tags: List[str] = []
    face_to_face_script: str = ""
    kb_mother_id: str = ""
    module_tag: str = "calc"
    crop_url: Optional[str] = None

class OcrConfirmItem(BaseModel):
    kb_mother_id: str
    module_tag: str
    question_snapshot: str
    error_tags: List[str] = []
    photo_url: Optional[str] = None
    ocr_note: Optional[str] = None
    face_to_face_script: Optional[str] = None
    is_wrong: bool = True

class OcrConfirmRequest(BaseModel):
    student_id: int
    items: List[OcrConfirmItem]

class GenerateSheetsRequest(BaseModel):
    student_ids: List[int]
    batch_title: Optional[str] = "周末数学冲刺巩固单"

class SheetItemSchema(BaseModel):
    id: int
    sheet_id: int
    sort_order: int
    source_mistake_id: Optional[int] = None
    kb_mother_id: Optional[str] = None
    item_type: str
    question_md: str
    image_path: Optional[str] = None
    answer_steps_md: str
    sandbox_verified: int = 1
    check_result: str = "pending"

class ConsolidationSheetDetail(BaseModel):
    id: int
    batch_code: str
    student_id: int
    student_name: str
    student_no: str
    title: str
    status: str
    pdf_path: Optional[str] = None
    face_to_face_script: Optional[str] = None
    parent_wechat_msg: Optional[str] = None
    items: List[SheetItemSchema] = []
    created_at: Optional[str] = None
    checked_at: Optional[str] = None

class CheckItemResult(BaseModel):
    item_id: int
    passed: bool

class MondayCheckRequest(BaseModel):
    sheet_id: int
    checks: List[CheckItemResult]

class ParentReportResponse(BaseModel):
    student_name: str
    anonymous_code: str
    teacher_summary: str
    wechat_msg: str
