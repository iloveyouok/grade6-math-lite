import os
from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    # Debug / Mock Switch
    MOCK_EXTERNAL_APIS: bool = Field(default=True)
    SEED_DEMO_DATA: bool = Field(default=True)

    # Auth & MCP Bridge
    TEACHER_ACCESS_PASSWORD: str = Field(default="math2026")
    SESSION_COOKIE_NAME: str = Field(default="g6_teacher_session")
    MCP_AUTH_TOKEN: str = Field(default="dev-mcp-token-change-me")
    
    # DeepSeek
    DEEPSEEK_API_KEY: str = Field(default="sk-mock")
    DEEPSEEK_BASE_URL: str = Field(default="https://api.deepseek.com")
    DEEPSEEK_MODEL: str = Field(default="deepseek-chat")
    
    # DashScope
    DASHSCOPE_API_KEY: str = Field(default="sk-mock")
    EMBEDDING_MODEL: str = Field(default="text-embedding-v4")
    RERANK_MODEL: str = Field(default="qwen3-rerank")
    
    # Tencent Cloud
    TENCENTCLOUD_SECRET_ID: str = Field(default="mock_id")
    TENCENTCLOUD_SECRET_KEY: str = Field(default="mock_key")
    TENCENTCLOUD_REGION: str = Field(default="ap-guangzhou")
    
    # Server & Storage
    APP_HOST: str = Field(default="0.0.0.0")
    APP_PORT: int = Field(default=8000)
    SQLITE_DB_PATH: str = Field(default="data/app.db")
    KB_VECTORS_PATH: str = Field(default="data/kb_vectors.json")
    KB_DIR: str = Field(default="knowledge_base")
    UPLOAD_DIR: str = Field(default="data/uploads")
    CROPPED_DIR: str = Field(default="data/cropped")
    PDFS_DIR: str = Field(default="data/pdfs")
    BATCHES_DIR: str = Field(default="data/batches")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()
