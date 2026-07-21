import getpass
import logging
import sqlite3

from .auth_utils import hash_password
from .database import create_user, get_user_by_email, init_db, update_user_role
from .logging_config import get_logger

VALID_ROLES = {"admin", "recruiter"}
admin_logger = get_logger("admin")


def normalize_email(email: str) -> str:
    normalized_email = email.lower().strip()
    if "@" not in normalized_email or "." not in normalized_email.rsplit("@", 1)[-1]:
        raise ValueError("Enter a valid email address.")

    return normalized_email


def prompt_role() -> str:
    role = input("Role (admin/recruiter): ").lower().strip()
    if role not in VALID_ROLES:
        raise ValueError("Role must be either admin or recruiter.")

    return role


def main() -> int:
    init_db()

    try:
        email = normalize_email(input("User email: "))
        role = prompt_role()
    except ValueError as exc:
        admin_logger.warning(str(exc))
        return 1

    existing_user = get_user_by_email(email)
    if existing_user:
        if existing_user.get("role") == role:
            admin_logger.warning("%s user already exists: %s", role.title(), email)
            return 1
        else:
            update_user_role(email, role)
            admin_logger.info("Existing user updated to %s: %s", role, email)
            return 0

    password = getpass.getpass("Password: ")
    confirm_password = getpass.getpass("Confirm password: ")

    if password != confirm_password:
        admin_logger.warning("Passwords do not match.")
        return 1

    if len(password) < 8:
        admin_logger.warning("Password must be at least 8 characters.")
        return 1

    try:
        create_user(email, hash_password(password), role=role)
    except sqlite3.IntegrityError:
        admin_logger.warning("User already exists: %s", email)
        return 1

    admin_logger.info("%s user created: %s", role.title(), email)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
