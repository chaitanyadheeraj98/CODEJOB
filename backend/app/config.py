from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "CodeJob Email Automation API"
    app_env: str = "dev"
    debug: bool = True

    postgres_dsn: str = "postgresql+psycopg://codejob:codejob@localhost:5432/codejob"
    redis_url: str = "redis://localhost:6379/0"
    openai_api_key: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
