import json
from fastapi import APIRouter, Depends
from backend.routers.auth import verify_teacher_auth
import backend.services.kb_retriever as kb_retriever

router = APIRouter(prefix="/api/kb", tags=["kb"], dependencies=[Depends(verify_teacher_auth)])

@router.get("/mothers")
def list_mother_questions():
    kb_retriever.load_kb_vectors()
    # Return mother question records without the raw float embeddings to save payload size
    result = []
    for item in kb_retriever._KB_CACHE:
        clean_item = {k: v for k, v in item.items() if k != "embedding"}
        result.append(clean_item)
    return result
