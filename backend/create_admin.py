import getpass
import sqlite3

from auth_utils import hash_password
from database import create_user, get_user_by_email, init_db, update_user_role

VALID_ROLES = {"admin", "recruiter"}


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
        print(exc)
        return 1

    existing_user = get_user_by_email(email)
    if existing_user:
        if existing_user.get("role") == role:
            print(f"{role.title()} user already exists: {email}")
            return 1
        else:
            update_user_role(email, role)
            print(f"Existing user updated to {role}: {email}")
            return 0

    password = getpass.getpass("Password: ")
    confirm_password = getpass.getpass("Confirm password: ")

    if password != confirm_password:
        print("Passwords do not match.")
        return 1

    if len(password) < 8:
        print("Password must be at least 8 characters.")
        return 1

    try:
        create_user(email, hash_password(password), role=role)
    except sqlite3.IntegrityError:
        print(f"User already exists: {email}")
        return 1

    print(f"{role.title()} user created: {email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
