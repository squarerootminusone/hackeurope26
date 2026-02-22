"""Database connection helper using Cloud SQL and Secret Manager."""

import pymysql
from google.cloud import secretmanager

PROJECT = "data-platform-dev-486916"
SECRET_ID = "eval-db-password"
DB_HOST = "35.205.179.231"
DB_USER = "eval_user"
DB_NAME = "evaluations_db"


def get_db_password() -> str:
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{PROJECT}/secrets/{SECRET_ID}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("utf-8")


def get_connection() -> pymysql.Connection:
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=get_db_password(),
        database=DB_NAME,
    )
